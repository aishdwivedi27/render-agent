"""Stage 8: drawing check of every render against its reference crops and the documented openings
(docs/SPEC.md section 6, schemas/drawing_check.json).

Code, not the model, decides the outcome: any failed check -> fail; any unsure (or no checks) ->
unsure. A fail gets `planning.auto_retries_after_failed_check` regenerations with the fix
instructions; still failing -> "Failed drawing check". An LLM error, an unsure answer, a dry run or
a disabled check -> "Not checked" (never silently passed). Writes checks/<id>_vN.json and
checks/index.json, then the job waits for review.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from render_agent.errors import AgentError, PauseJobError
from render_agent.pipeline.stages._common import llm_for, load_schema, read_json
from render_agent.pipeline.stages.s7_render import Renderer, latest
from render_agent.util.fs import atomic_write_json
from render_agent.util.prompts import load_prompt, quote_block, render

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

STAGE = "drawing_check"
ARTIFACT = "checks/index.json"
PASSED, FAILED, NOT_CHECKED = "Passed drawing check", "Failed drawing check", "Not checked"
log = logging.getLogger("render_agent.s8")


def decide(answer: dict[str, Any], render_id: str) -> str:
    """pass | fail | unsure, recomputed from the individual checks."""
    results = [c["result"] for c in answer.get("checks", [])]
    if answer.get("render_id") != render_id or not results:
        return "unsure"
    if "fail" in results:
        return "fail"
    return "unsure" if "unsure" in results else "pass"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def view_labels(job_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in sorted((job_dir / "sheets").glob("sheet-*.json")):
        if f.name.endswith(".notes.json"):
            continue
        page = int(f.stem.split("-")[1])
        for v in json.loads(f.read_text(encoding="utf-8")).get("views", []):
            out.setdefault(v["view_id"], {**v, "page": page})
    return out


def openings_for(spec: dict[str, Any], labels: list[str]) -> str:
    wanted = [_norm(lb) for lb in labels if lb.strip()]
    rows = []
    for el in spec.get("ELEVATIONS", []):
        name = _norm(el.get("elevation", ""))
        if name and any(w in name or name in w for w in wanted):
            rows.append(
                f"{el['elevation']}\n"
                + "".join(f"- {o}\n" for o in el.get("openings", []))
                + "".join(f"- Roof: {o}\n" for o in el.get("roof_information", []))
            )
    if not rows:
        return "No documented elevation matches the reference images; judge openings from the images."
    return quote_block("ELEVATION_OPENINGS", "\n".join(rows))


def facade_text(entry: dict[str, Any]) -> str:
    items = entry.get("facade_left_to_right") or []
    if not items:
        return "No left-to-right list was extracted for this view; read the order from the reference image."
    body = "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1))
    return quote_block("FACADE", f"As drawn on {entry.get('facade_source', 'the elevation')}:\n{body}")


class Checker:
    def __init__(self, ctx: RunContext, renderer: Renderer) -> None:
        self.ctx = ctx
        self.r = renderer
        self.job_dir = ctx.job.dir
        self.spec = read_json(self.job_dir / "house_specification.json", STAGE)
        self.checklist = {
            c["render_id"]: c
            for c in read_json(self.job_dir / "checklist.json", STAGE)["RENDER_REQUIREMENTS"]
        }
        self.identities = read_json(self.job_dir / "prompts" / "index.json", STAGE)["identity_blocks"]
        self.labels = view_labels(self.job_dir)
        self.template = load_prompt(ctx.settings.path("prompts"), STAGE)
        self.schema = load_schema(ctx, "drawing_check")
        self.offline = ctx.dry_run and "llm" not in ctx.extras

    def view_label(self, ref_id: str) -> dict[str, Any]:
        """Label and sheet of a reference; "<view>@<dwelling>" is that dwelling's crop of the view."""
        parent, _, dwelling = ref_id.partition("@")
        view = self.labels.get(parent, {"label": parent, "page": "?"})
        return {**view, "label": f"{view['label']}, {dwelling} only"} if dwelling else view

    def prompt(self, render_id: str) -> str:
        e = self.r.entry(render_id)
        req = self.checklist.get(render_id, {})
        views = [self.view_label(v) for v in e["reference_view_ids"]]
        legend = [f"- Image {i} is {v['label']}, sheet {v['page']}" for i, v in enumerate(views, 2)]
        facts = [*req.get("architectural_facts", []), *req.get("materials_and_colours", [])]
        return render(
            self.template,
            {
                "RENDER_ID": render_id,
                "VIEW": f"{e['view_type']} ({req.get('location', '')})",
                "STRICTNESS": self.ctx.settings.drawing_check.strictness,
                "LEGEND": "\n".join(["- Image 1 is the render.", *legend]),
                "FACTS": quote_block("FACTS", "\n".join(f"- {f}" for f in facts) or "- (none listed)"),
                "FACADE": facade_text(e),
                "OPENINGS": openings_for(self.spec, [v["label"] for v in views]),
                "IDENTITY": quote_block("IDENTITY", self.identities.get(e["dwelling_id"], "")),
            },
        )

    def check_version(self, render_id: str, ver: dict[str, Any]) -> dict[str, Any]:
        out_file = self.job_dir / "checks" / f"{Path(ver['file']).stem}.json"
        if out_file.is_file():
            return json.loads(out_file.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        result: dict[str, Any] = {"render_id": render_id, "version": ver["version"], "file": ver["file"]}
        if not self.ctx.settings.drawing_check.enabled:
            result.update(overall="unsure", reason="drawing_check.enabled is off")
        elif self.offline:
            result.update(overall="unsure", reason="dry run: no drawing check was made")
        else:
            e = self.r.entry(render_id)
            images = [self.job_dir / ver["clean_file"], *(self.job_dir / p for p in e["references"])]
            try:
                answer = llm_for(self.ctx, STAGE).call_json(
                    STAGE, self.prompt(render_id), images, self.schema, "drawing_check"
                )
            except PauseJobError:
                raise
            except AgentError as err:
                result.update(overall="unsure", reason=f"check failed: {err}")
            else:
                result.update(answer=answer, overall=decide(answer, render_id))
                if answer.get("overall") != result["overall"]:
                    result["overall_from_model"] = answer.get("overall")
        atomic_write_json(out_file, result)
        return result

    def run_one(self, render_id: str) -> dict[str, Any]:
        rec = self.r.record(render_id)
        ver = latest(rec)
        if ver is None:
            return {"outcome": None, "status": rec["status"]}
        retries_left = self.ctx.settings.planning.auto_retries_after_failed_check - sum(
            1 for v in rec["versions"] if v["kind"] == "auto_retry"
        )
        while True:
            res = self.check_version(render_id, ver)
            if res["overall"] == "pass":
                outcome = PASSED
                break
            if res["overall"] == "unsure":
                outcome = NOT_CHECKED
                break
            if retries_left <= 0:
                outcome = FAILED
                break
            retries_left -= 1
            fix = res.get("answer", {}).get("fix_instructions") or ["Match the reference drawings exactly."]
            log.info("job %s: %s failed the drawing check; regenerating", self.ctx.job.id, render_id)
            rec = self.r.render(render_id, kind="auto_retry", fix=fix)
            new = latest(rec)
            if new is None or new["version"] == ver["version"]:
                outcome = FAILED  # the regeneration itself failed; reason is in render_index.json
                break
            ver = new
        return {
            "outcome": outcome,
            "version": ver["version"],
            "check_file": f"checks/{Path(ver['file']).stem}.json",
        }


def run(ctx: RunContext) -> str:
    renderer = Renderer(ctx)
    checker = Checker(ctx, renderer)
    ids = list(renderer.index["renders"])
    with ThreadPoolExecutor(max_workers=ctx.settings.images.concurrency) as pool:
        summary = dict(zip(ids, pool.map(checker.run_one, ids), strict=True))
    atomic_write_json(ctx.job.dir / ARTIFACT, {"renders": summary})
    ctx.job.set_status("AWAITING_REVIEW")
    return ARTIFACT
