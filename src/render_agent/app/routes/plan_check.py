"""Check the plan: the traced walls on the drawing, the area table and the rows code wrote, read-only
until the floor plan questions (PV3) are built (PLAN_VISUALS_SPEC sections 3 and 4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent.app.models import JobOut
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import job_dir_or_404, summary
from render_agent.app.security import require_token
from render_agent.pipeline import plan_check
from render_agent.pipeline.job import Job, load_job

router = APIRouter()


def _waiting(job: Job) -> None:
    if job.state.get("status") != "AWAITING_PLAN_CHECK":
        raise HTTPException(
            409,
            {"what_happened": "This job is not waiting at Check the plan.", "what_to_do": "Go back to Home."},
        )


@router.get("/jobs/{job_id}/plan-check")
def get_plan_check(job_id: str, ctx: Ctx) -> dict[str, Any]:
    return plan_check.view(job_dir_or_404(ctx, job_id))


@router.post("/jobs/{job_id}/plan-check/continue", dependencies=[Depends(require_token)])
def continue_plan_check(job_id: str, ctx: Ctx) -> JobOut:
    d = job_dir_or_404(ctx, job_id)
    _waiting(load_job(d))
    plan_check.mark_seen(d, by="designer (app)")
    return summary(ctx, d, full=True)


@router.post("/jobs/{job_id}/plan-check/reread", dependencies=[Depends(require_token)])
def reread(job_id: str, ctx: Ctx) -> JobOut:
    """Reads the drawing again with the model(s) that could not read it; answered readings are kept."""
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    _waiting(job)
    if not plan_check.view(d)["failed"]:
        raise HTTPException(
            409,
            {"what_happened": "Every chosen model has read the drawing.", "what_to_do": "Refresh the page."},
        )
    ctx.reload_settings()  # a key added in Settings since
    plan_check.reread(job)
    job.set_status("RUNNING")
    ctx.worker.enqueue(job.id)
    return summary(ctx, d, full=True)
