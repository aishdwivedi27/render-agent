"""Review screen: thumbnails, approve / flag with a category and a note, Continue.

Decisions go through s9_review.decide; Continue queues the job, and stage 9 regenerates the
flagged renders only and delivers the approved ones (no review logic lives here).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent.app import feedback
from render_agent.app.models import DecideIn, JobOut
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import job_dir_or_404, summary
from render_agent.app.security import require_token
from render_agent.pipeline import corrections
from render_agent.pipeline.job import load_job
from render_agent.pipeline.stages import s9_review
from render_agent.pipeline.stages.s8_check import FAILED, NOT_CHECKED, PASSED
from render_agent.util.fs import safe_name

router = APIRouter()
CHECK_TEXT = {
    PASSED: "Matches the drawings",
    FAILED: "Did not match the drawings",
    NOT_CHECKED: "Not checked against the drawings",
}
STATUS_TEXT = {
    "pending": "Waiting for you",
    "approved": "Approved",
    "flagged": "Flagged, will be redone",
    "failed": "Could not be made",
    "skipped": "Not made (missing drawings)",
}


def _read(path: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@router.get("/review/categories")
def categories() -> dict[str, str]:
    return s9_review.FLAG_CATEGORIES


@router.get("/jobs/{job_id}/review")
def get_review(job_id: str, ctx: Ctx) -> dict[str, Any]:
    d = job_dir_or_404(ctx, job_id)
    if not (d / "output" / "render_index.json").is_file():
        raise HTTPException(409, {"what_happened": "No renders yet.", "what_to_do": "Wait for rendering."})
    review = s9_review.load_review(d)
    prompts = {r["render_id"]: r for r in (_read(d / "prompts" / "index.json") or {}).get("renders", [])}
    plan = {r["render_id"]: r for r in (_read(d / "checklist.json") or {}).get("RENDER_REQUIREMENTS", [])}
    index = (_read(d / "output" / "render_index.json") or {}).get("renders", {})

    def practice(rid: str, v: int | None) -> bool:
        versions = index.get(rid, {}).get("versions", [])
        return any(x.get("version") == v and x.get("provider") == "mock" for x in versions)

    cards = []
    for rid, e in review["renders"].items():
        v = e.get("version")
        refs = prompts.get(rid, {}).get("references") or []
        p = plan.get(rid, {})
        cards.append(
            {
                "render_id": rid,
                "title": p.get("view_type") or rid,
                "where": p.get("location") or "",
                "dwelling": p.get("dwelling_id") or "",
                "version": v,
                "image": f"output/{safe_name(rid, 80)}_v{v}.png" if v else None,
                "reference": refs[0] if refs else None,
                "check": CHECK_TEXT.get(e.get("check") or "", e.get("check") or "Not checked"),
                "uncertain": e.get("check") != PASSED,
                "confidence": e.get("confidence"),
                "practice": practice(rid, v),
                "status": e["status"],
                "status_text": STATUS_TEXT.get(e["status"], e["status"]),
                "note": e.get("note") or e.get("reason") or "",
                "category": e.get("category"),
            }
        )
    open_count = sum(1 for c in cards if c["status"] in ("pending", "flagged"))
    return {"cards": cards, "open": open_count, "categories": s9_review.FLAG_CATEGORIES}


@router.post("/jobs/{job_id}/review/continue", dependencies=[Depends(require_token)])
def continue_review(job_id: str, ctx: Ctx) -> JobOut:
    d = job_dir_or_404(ctx, job_id)
    ctx.worker.enqueue(job_id)
    return summary(ctx, d, full=True)


@router.post("/jobs/{job_id}/review/{render_id}", dependencies=[Depends(require_token)])
def decide(job_id: str, render_id: str, body: DecideIn, ctx: Ctx) -> dict[str, Any]:
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    if job.state.get("status") != "AWAITING_REVIEW" or ctx.worker.position(job.id) is not None:
        raise HTTPException(
            409, {"what_happened": "This job is not waiting for review.", "what_to_do": "Go back to Home."}
        )
    if body.action == "flag" and not body.category:
        body.category = "other"
    review = s9_review.load_review(d)
    problem = s9_review.decide(
        review, render_id, body.action, body.note, by="designer (app)", category=body.category
    )
    if problem:
        raise HTTPException(422, {"what_happened": problem, "what_to_do": "Check the note and try again."})
    s9_review.save_review(d, review)
    warning = None
    if body.action == "flag":
        if body.apply_to_job:
            label = s9_review.FLAG_CATEGORIES[body.category or "other"]
            corrections.add(d, label, body.note, f"from review of {render_id}", where="review")
        v = review["renders"][render_id].get("version")
        warning = feedback.record(
            ctx.settings,
            {
                "job_id": job.id,
                "view": render_id,
                "category": body.category,
                "correction": body.note.strip(),
                "suggest_rule": body.suggest_rule,
                "applies_to_job": body.apply_to_job,
                "where": "review",
                "image": f"output/{safe_name(render_id, 80)}_v{v}.png" if v else None,
            },
        )
    return {"ok": True, "warning": warning}
