"""Start the plan check: the readout model (Claude, OpenAI or both) or "Exterior renders only (no
plans)", right after the drawings are read (PLAN_VISUALS_SPEC section 2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent.app.models import JobOut, PlanStartIn
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import job_dir_or_404, summary
from render_agent.app.security import require_token
from render_agent.pipeline import plan_start
from render_agent.pipeline.job import load_job

router = APIRouter()


@router.get("/jobs/{job_id}/plan-start")
def get_plan_start(job_id: str, ctx: Ctx) -> dict[str, Any]:
    return plan_start.options(job_dir_or_404(ctx, job_id), ctx.settings)


@router.post("/jobs/{job_id}/plan-start", dependencies=[Depends(require_token)])
def start_plan_check(job_id: str, body: PlanStartIn, ctx: Ctx) -> JobOut:
    """Records the choice; the job carries on at once (plan check, or Choose views if renders only)."""
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    if job.state.get("status") != "AWAITING_PLAN_START":
        raise HTTPException(
            409,
            {
                "what_happened": "This job is not waiting to start the plan check.",
                "what_to_do": "Go back to Home.",
            },
        )
    if body.plans_mode == "plans":
        option = next(
            o for o in plan_start.options(d, ctx.settings)["options"] if o["id"] == body.readout_llm
        )
        if not option["available"]:
            raise HTTPException(
                422,
                {
                    "what_happened": f"{option['label']} cannot read the drawings: {option['why_not']}.",
                    "what_to_do": "Choose another readout model, or ask the person who set up Render Agent.",
                },
            )
    plan_start.choose(job, ctx.settings, body.plans_mode, body.readout_llm, by="designer (app)")
    job.set_status("RUNNING")
    ctx.worker.enqueue(job.id)
    return summary(ctx, d, full=True)
