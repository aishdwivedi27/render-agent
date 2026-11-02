"""Check the plan: the traced walls on the drawing, the area table and the readout table, with the
floor plan questions in batches (PLAN_VISUALS_SPEC sections 3 and 4). The gate is enforced here as
well as in the runner: nothing moves on while any row is Orange or Red."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent.app import feedback
from render_agent.app.models import JobOut, PlanAnswersIn
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
    return plan_check.view(job_dir_or_404(ctx, job_id), ctx.settings)


@router.post("/jobs/{job_id}/plan-check/answers", dependencies=[Depends(require_token)])
def answer(job_id: str, body: PlanAnswersIn, ctx: Ctx) -> JobOut:
    """One batch of answers: saved, added to the job's corrections, the feedback list and the project's
    memory; then the plan is read again (the overlay is redrawn) and the job moves on once all is Green."""
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    _waiting(job)
    try:
        saved, warnings = plan_check.answer(
            job, ctx.settings, [a.model_dump() for a in body.answers], by="designer (app)"
        )
    except plan_check.AnswerError as err:
        raise HTTPException(
            422, {"what_happened": str(err), "what_to_do": "Refresh the page and answer the questions shown."}
        ) from err
    for a in saved:
        w = feedback.record(
            ctx.settings,
            {
                "job_id": job.id,
                "view": None,
                "category": "readout_answer",
                "correction": f"{a['item']}: {a['answer']}",
                "read_as": a["reading_before"],
                "dwelling": a["dwelling"],
                "applies_to_job": True,
                "where": "plan_check",
            },
        )
        if w and w not in warnings:
            warnings.append(w)
    if warnings:  # shown on the job card; the answers still apply
        info = job.state.setdefault("designer", {})
        info["warnings"] = [
            *info.get("warnings", []),
            *(w for w in warnings if w not in info.get("warnings", [])),
        ]
    job.set_status("RUNNING")
    ctx.worker.enqueue(job.id)
    return summary(ctx, d, full=True)


@router.post("/jobs/{job_id}/plan-check/continue", dependencies=[Depends(require_token)])
def continue_plan_check(job_id: str, ctx: Ctx) -> JobOut:
    """Refused while any row is Orange or Red (the gate is checked here, not only on the screen)."""
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    _waiting(job)
    if not plan_check.all_green(d):
        n = plan_check.open_rows(d)
        raise HTTPException(
            409,
            {
                "what_happened": f"{n} floor plan item(s) are still Orange or Red.",
                "what_to_do": "Answer the questions; when every row is Green, press Continue.",
            },
        )
    plan_check.press_continue(job, by="designer")
    job.set_status("RUNNING")
    ctx.worker.enqueue(job.id)
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
