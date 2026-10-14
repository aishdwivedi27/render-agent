"""Stage 9: designer review, targeted regeneration and delivery (docs/SPEC.md section 6).

review.json holds one entry per render: pending | approved | flagged (+ note) | failed.
- Decisions come from the review page, the `review` CLI command, or review.txt in the job folder:
  one `<render_id>: <note>` line per flag, and `approve all` to approve every other pending render.
- Approved renders are locked and exported clean (no DRAFT overlay) to Common / Dwelling N folders.
- Flagged renders, and only those, are regenerated with the note (stage 7 path, kind "review",
  counted in budget.json) and re-checked (stage 8); the new version goes back to pending.
- The job is SUCCEEDED when every render is approved; until then it waits at AWAITING_REVIEW.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from render_agent.errors import PermanentError
from render_agent.output.folders import export_approved
from render_agent.pipeline.runner import StageWaiting
from render_agent.pipeline.stages._common import read_json
from render_agent.pipeline.stages.s7_render import Renderer, latest
from render_agent.pipeline.stages.s8_check import Checker
from render_agent.util.fs import atomic_write_json, sha256_file
from render_agent.validate.checklist_validator import load_view_set

if TYPE_CHECKING:
    from render_agent.pipeline.job import Job
    from render_agent.pipeline.runner import RunContext

STAGE = "review"
REVIEW = "review.json"
REVIEW_TXT = "review.txt"
MANIFEST = "manifest.json"
log = logging.getLogger("render_agent.s9")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_review(job_dir: Path) -> dict[str, Any]:
    """review.json, kept in step with render_index.json (new renders and versions become pending)."""
    path = job_dir / REVIEW
    review: dict[str, Any] = read_json(path, STAGE) if path.is_file() else {"renders": {}}
    index = read_json(job_dir / "output" / "render_index.json", STAGE)["renders"]
    checks = {}
    if (job_dir / "checks" / "index.json").is_file():
        checks = read_json(job_dir / "checks" / "index.json", STAGE)["renders"]
    for rid, rec in index.items():
        entry = review["renders"].setdefault(rid, {"status": "pending", "version": None, "history": []})
        ver = latest(rec)
        entry["check"] = checks.get(rid, {}).get("outcome")
        if rec["status"] == "skipped":
            entry.update(status="skipped", version=None, reason=rec.get("reason"))
        elif ver is None:
            if entry["status"] != "flagged":
                entry.update(
                    status="failed", version=None, reason=(rec.get("error") or {}).get("what_happened")
                )
        elif entry["status"] != "approved" and entry.get("version") != ver["version"]:
            entry.update(status="pending", version=ver["version"], note=None)
    return review


def save_review(job_dir: Path, review: dict[str, Any]) -> None:
    atomic_write_json(job_dir / REVIEW, review)


def decide(
    review: dict[str, Any], render_id: str, action: str, note: str = "", by: str = "designer"
) -> str | None:
    """Records approve/flag. Returns a problem (str) instead of raising, for the page and the CLI."""
    entry = review["renders"].get(render_id)
    if entry is None:
        return f"unknown render id {render_id}"
    if action not in ("approve", "flag"):
        return f"unknown action {action}"
    if entry["status"] == "approved":
        return f"{render_id} is already approved and locked"
    if entry["status"] == "skipped":
        return f"{render_id} was not planned for rendering"
    if action == "approve" and entry["status"] != "pending":
        return f"{render_id} has no image to approve ({entry['status']})"
    if action == "flag" and not note.strip():
        return f"{render_id}: a flag needs a note saying what to change"
    entry["status"] = "approved" if action == "approve" else "flagged"
    entry["note"] = note.strip() or None
    entry["history"].append(
        {"action": action, "version": entry.get("version"), "note": entry["note"], "by": by, "time": _now()}
    )
    return None


def apply_review_txt(job_dir: Path, review: dict[str, Any]) -> list[str]:
    """Reads review.txt (then renames it, so it is applied once). Returns problems."""
    path = job_dir / REVIEW_TXT
    if not path.is_file():
        return []
    problems = []
    approve_all = False
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower() in ("approve all", "approve"):
            approve_all = True
            continue
        rid, sep, note = line.partition(":")
        if not sep:
            problems.append(f"review.txt: not '<render_id>: <note>': {line}")
            continue
        p = decide(review, rid.strip(), "flag", note, by="review.txt")
        if p:
            problems.append(f"review.txt: {p}")
    if approve_all:
        for rid, e in review["renders"].items():
            if e["status"] == "pending":
                decide(review, rid, "approve", by="review.txt")
    path.replace(path.with_name(f"review.applied-{datetime.now():%Y%m%d-%H%M%S}.txt"))
    return problems


def regenerate_flagged(ctx: RunContext, review: dict[str, Any]) -> list[str]:
    """Stages 7-8 again for flagged renders only, with the designer's note. Returns their ids."""
    flagged = [rid for rid, e in review["renders"].items() if e["status"] == "flagged"]
    if not flagged:
        return []
    renderer = Renderer(ctx)
    checker = Checker(ctx, renderer)
    for rid in flagged:
        note = review["renders"][rid]["note"] or ""
        log.info("job %s: regenerating flagged %s: %s", ctx.job.id, rid, note)
        rec = renderer.render(rid, kind="review", fix=[note])
        if latest(rec) is not None and rec["status"] == "rendered":
            checker.run_one(rid)
            review["renders"][rid].update(status="pending", note=None)
        else:
            review["renders"][rid].update(
                status="failed", reason=(rec.get("error") or {}).get("what_happened")
            )
    return flagged


def deliver(ctx: RunContext, review: dict[str, Any]) -> dict[str, Any]:
    """Exports approved renders and writes manifest.json."""
    job = ctx.job
    approved = {rid: e["version"] for rid, e in review["renders"].items() if e["status"] == "approved"}
    checklist = read_json(job.dir / "checklist.json", STAGE)
    dwellings = read_json(job.dir / "checklist_validation.json", STAGE).get("dwellings", [])
    view_set = load_view_set(ctx.settings.path("knowledge"))
    try:
        exported = export_approved(job.dir, checklist["RENDER_REQUIREMENTS"], approved, dwellings, view_set)
    except FileNotFoundError as exc:
        raise PermanentError(
            code="EXPORT_SOURCE_MISSING",
            what_happened=str(exc),
            likely_cause="A file in output/clean/ was deleted or moved.",
            what_to_do="Flag the render to regenerate it, then resume the job.",
        ) from exc
    knowledge = ctx.settings.path("knowledge")
    manifest = {
        "job_id": job.id,
        "generated": _now(),
        "inputs": job.state.get("inputs", []),
        "renders": exported,
        "pending": sorted(
            rid for rid, e in review["renders"].items() if e["status"] in ("pending", "flagged")
        ),
        "failed": sorted(rid for rid, e in review["renders"].items() if e["status"] == "failed"),
        "knowledge_files": {p.name: sha256_file(p) for p in sorted(knowledge.glob("*")) if p.is_file()},
        "budget": read_json(job.dir / "budget.json", STAGE) if (job.dir / "budget.json").is_file() else {},
    }
    atomic_write_json(job.dir / MANIFEST, manifest)
    from render_agent.output.report import write_report

    write_report(job.dir, review, manifest, ctx.settings.path("knowledge"))
    return manifest


def summary(review: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in review["renders"].values():
        out[e["status"]] = out.get(e["status"], 0) + 1
    return out


def run(ctx: RunContext) -> str:
    job = ctx.job
    review = load_review(job.dir)
    problems = apply_review_txt(job.dir, review)
    save_review(job.dir, review)
    redone = regenerate_flagged(ctx, review)
    if redone:
        review = load_review_after(job.dir, review)
    save_review(job.dir, review)
    deliver(ctx, review)
    counts = summary(review)
    open_items = [rid for rid, e in review["renders"].items() if e["status"] not in ("approved", "skipped")]
    if not open_items and not counts.get("approved"):
        skipped = [f"{rid}: {e.get('reason') or 'not rendered'}" for rid, e in review["renders"].items()]
        raise StageWaiting(
            "AWAITING_REVIEW",
            "Nothing was rendered, so nothing can be delivered. Fix the drawings or the plan for:\n  "
            + "\n  ".join(skipped or ["(no renders planned)"]),
        )
    if open_items:
        msg = (
            f"Review: {counts}. Open: {', '.join(open_items)}. Approve or flag with "
            f'`python -m render_agent review "{job.dir}"`, the review page, or review.txt.'
        )
        if redone:
            msg = f"Regenerated {', '.join(redone)}. " + msg
        if problems:
            msg += "\n  " + "\n  ".join(problems)
        raise StageWaiting("AWAITING_REVIEW", msg)
    return MANIFEST


def load_review_after(job_dir: Path, review: dict[str, Any]) -> dict[str, Any]:
    """After regeneration: keep the decisions just made, pick up the new versions."""
    save_review(job_dir, review)
    return load_review(job_dir)


def review_text(job: Job) -> str:
    """Plain summary for the CLI."""
    review = load_review(job.dir)
    lines = []
    for rid, e in review["renders"].items():
        v = f"v{e['version']}" if e.get("version") else "-"
        extra = e.get("note") or e.get("reason") or ""
        lines.append(f"  {rid:40} {e['status']:9} {v:4} {e.get('check') or '':22} {extra}")
    return "\n".join(lines)
