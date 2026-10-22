"""Stage 7: render every planned view through the image provider (docs/SPEC.md section 6).

- budget guard before every provider call (images/budget.py)
- `images.concurrency` renders at a time; one render failing never stops the others
- renders use images.openai.draft (size and quality); an approved draft is delivered as its clean copy
  scaled to images.delivery_size, with no further image call (decision 20 Oct 2026)
- versioned outputs (decision 20 Oct 2026): output/<home>/Draft/<id>_vN.png carries the DRAFT overlay
  and is kept; output/clean/<id>_vN.png is the overlay-free copy of the LATEST version only (older
  clean copies are deleted when a newer version is made); drafts are never overwritten
- tokens the provider reports are saved on each version and in budget.json (cost report)
- output/render_index.json records every version, failure and skip; resume skips finished renders
- a PauseJobError (key, quota, budget) pauses the job after the in-flight renders finish
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from render_agent.errors import AgentError, PauseJobError
from render_agent.images import ImageProvider, RenderRequest, get_provider, render_with_retry, size_for
from render_agent.images.budget import Budget, allowances
from render_agent.images.overlay import draft_overlay
from render_agent.output.folders import home_folder, names
from render_agent.pipeline import corrections
from render_agent.pipeline.job import cancel_requested
from render_agent.pipeline.stages._common import ingest_info, read_json
from render_agent.util.directives import parse_directives
from render_agent.util.fs import atomic_write_bytes, atomic_write_json, atomic_write_text, safe_name
from render_agent.util.prompts import quote_block
from render_agent.validate.checklist_validator import load_view_set

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

STAGE = "render"
ARTIFACT = "output/render_index.json"
log = logging.getLogger("render_agent.s7")
_index_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Renderer:
    """Shared by stage 7, the drawing-check retry (stage 8) and review regeneration (M4)."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.job_dir = ctx.job.dir
        self.prompts = read_json(self.job_dir / "prompts" / "index.json", STAGE)["renders"]
        self.index_path = self.job_dir / ARTIFACT
        self.index: dict[str, Any] = (
            read_json(self.index_path, STAGE) if self.index_path.is_file() else {"renders": {}}
        )
        self.provider: ImageProvider = ctx.extras.get("image_provider") or get_provider(
            ctx.settings, self._directive_provider(), ctx.dry_run or self._directive_dry_run()
        )
        p = ctx.settings.planning
        planned = Counter(r["dwelling_id"] for r in self.prompts)
        self.budget = Budget(
            self.job_dir,
            allowances(dict(planned), p.max_planned_renders_per_dwelling, p.auto_retries_after_failed_check),
            ctx.settings.images.max_images_per_round,
        )
        self.wait = ctx.extras.get("image_wait")
        self.view_set = load_view_set(ctx.settings.path("knowledge"))
        checklist = self.job_dir / "checklist.json"
        self.plan = (
            {r["render_id"]: r for r in read_json(checklist, STAGE)["RENDER_REQUIREMENTS"]}
            if checklist.is_file()
            else {}
        )
        validation = self.job_dir / "checklist_validation.json"
        self.dwellings = read_json(validation, STAGE).get("dwellings", []) if validation.is_file() else []

    def draft_dir(self, render_id: str) -> str:
        """output/<home>/Draft: where every version of this render is kept with the DRAFT overlay."""
        req = self.plan.get(render_id) or {
            "render_id": render_id,
            "dwelling_id": self.entry(render_id)["dwelling_id"],
            "view_type": "",
            "location": "",
        }
        return f"output/{home_folder(req, self.dwellings, self.view_set)}/{names(self.view_set)['draft']}"

    def _directives(self) -> Any:
        info = self.job_dir / "ingest.json"
        return parse_directives(ingest_info(self.ctx).get("instructions", "") if info.is_file() else "")

    def _directive_provider(self) -> str | None:
        name = self._directives().provider
        allowed = [*self.ctx.settings.images.enabled_providers, "mock"]
        return name if name in allowed else None  # a provider not enabled is never used

    def _directive_dry_run(self) -> bool:
        return bool(self._directives().dry_run)

    def entry(self, render_id: str) -> dict[str, Any]:
        return next(r for r in self.prompts if r["render_id"] == render_id)

    def record(self, render_id: str) -> dict[str, Any]:
        return self.index["renders"].setdefault(render_id, {"status": "pending", "versions": []})

    def save(self) -> None:
        with _index_lock:
            atomic_write_json(self.index_path, self.index)

    def next_version(self, render_id: str) -> int:
        recorded = [v["version"] for v in self.record(render_id)["versions"]]
        stem = safe_name(render_id, 80)
        pat = re.compile(rf"^{re.escape(stem)}_v(\d+)\.png$")
        seen = [
            int(m.group(1))
            for d in (self.job_dir / "output", self.job_dir / "output" / "clean")
            if d.is_dir()
            for f in d.iterdir()
            if (m := pat.match(f.name))
        ]
        return max([*seen, *recorded], default=0) + 1

    def render(self, render_id: str, kind: str = "first", fix: list[str] | None = None) -> dict[str, Any]:
        """One version of one render. Errors other than PauseJobError are recorded, not raised."""
        e = self.entry(render_id)
        rec = self.record(render_id)
        prompt = (self.job_dir / e["prompt_file"]).read_text(encoding="utf-8")
        prompt += corrections.prompt_block(self.job_dir)
        if fix and kind == "review":
            prompt += (
                "\nDESIGNER CHANGES REQUESTED AT REVIEW (apply them; the drawings still decide the "
                "architecture)\n" + quote_block("DESIGNER_NOTE", "\n".join(fix)) + "\n"
            )
        elif fix:
            prompt += "\nFIX FROM DRAWING CHECK (the previous image broke these; correct them)\n"
            prompt += "".join(f"- {f}\n" for f in fix)
        draft = self.ctx.settings.images.openai.draft
        req = RenderRequest(
            render_id=render_id,
            prompt=prompt,
            references=[self.job_dir / r for r in e["references"]],
            size=size_for(draft.size, e.get("aspect", "landscape")),
            aspect=e.get("aspect", "landscape"),
            quality=draft.quality,
        )
        try:
            call = self.budget.reserve(
                render_id, e["dwelling_id"], kind, self.provider.name, self.provider.paid, req.quality
            )
            png, attempts = render_with_retry(self.provider, req, self.ctx.settings.images.retries, self.wait)
            self.budget.add_usage(call, req.usage)
        except PauseJobError:
            raise
        except AgentError as err:
            rec.update(status="failed", error={**err.to_dict(), "kind": type(err).__name__, "time": _now()})
            rec["failed_prompt"] = prompt
            self.save()
            log.error("job %s: render %s failed: %s", self.ctx.job.id, render_id, err)
            return rec
        with _index_lock:
            v = self.next_version(render_id)
            stem = f"{safe_name(render_id, 80)}_v{v}"
            clean = f"output/clean/{stem}.png"
            draft_file = f"{self.draft_dir(render_id)}/{stem}.png"
            atomic_write_bytes(self.job_dir / clean, png)
            atomic_write_bytes(self.job_dir / draft_file, draft_overlay(png))
            prune_clean(self.job_dir, render_id, keep=clean)
        if fix:
            atomic_write_text(self.job_dir / f"prompts/{stem}.txt", prompt)
        rec["versions"].append(
            {
                "version": v,
                "file": draft_file,
                "clean_file": clean,
                "provider": self.provider.name,
                "model": self.provider.model,
                "kind": kind,
                "attempts": attempts,
                "size": req.size,
                "quality": req.quality,
                "usage": req.usage,
                "fix_instructions": fix or [],
                "time": _now(),
            }
        )
        rec.update(status="rendered", error=None)
        rec.pop("failed_prompt", None)
        self.save()
        return rec


def prune_clean(job_dir: Path, render_id: str, keep: str | None = None) -> None:
    """Deletes the overlay-free copies of this render except `keep` (the latest version): a replaced
    version's clean copy is never delivered, so it is not kept (decision 20 Oct 2026)."""
    folder = job_dir / "output" / "clean"
    if not folder.is_dir():
        return
    pat = re.compile(rf"^{re.escape(safe_name(render_id, 80))}_v\d+\.png$")
    for f in folder.iterdir():
        if pat.match(f.name) and (keep is None or f.as_posix() != (job_dir / keep).as_posix()):
            f.unlink(missing_ok=True)


def _skip_reason(ctx: RunContext, e: dict[str, Any]) -> str | None:
    if e["readiness"] == "NOT READY":
        return "NOT READY at the approval gate"
    if e["readiness"] == "REVIEW" and not ctx.settings.planning.render_review_views:
        return "REVIEW view and planning.render_review_views is off"
    return None


def run(ctx: RunContext) -> str:
    ctx.job.set_status("RENDERING")
    r = Renderer(ctx)
    todo = []
    for e in r.prompts:
        rec = r.record(e["render_id"])
        rec["dwelling_id"] = e["dwelling_id"]
        reason = _skip_reason(ctx, e)
        if reason:
            rec.update(status="skipped", reason=reason)
        elif not rec["versions"]:
            todo.append(e["render_id"])
    r.save()
    pauses: list[PauseJobError] = []

    def one(render_id: str) -> None:
        if pauses or cancel_requested(ctx.job.dir):  # cancel: stop after the renders in flight
            return
        try:
            r.render(render_id)
        except PauseJobError as err:
            pauses.append(err)

    with ThreadPoolExecutor(max_workers=ctx.settings.images.concurrency) as pool:
        list(pool.map(one, todo))
    r.index["budget"] = r.budget.summary()
    r.save()
    if pauses:
        raise pauses[0]
    failed = [k for k, v in r.index["renders"].items() if v["status"] == "failed"]
    if failed:
        log.warning("job %s: %d render(s) failed: %s", ctx.job.id, len(failed), failed)
    return ARTIFACT


def latest(rec: dict[str, Any]) -> dict[str, Any] | None:
    return rec["versions"][-1] if rec.get("versions") else None
