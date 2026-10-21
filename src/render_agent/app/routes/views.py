"""Choose views: after the drawings are read, the designer picks all views, some, or says them in
words, sees what the agent will plan, and confirms; then the plan is made (decision 21 Oct 2026)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent.app.models import ChooseViewsIn, JobOut
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import job_dir_or_404, summary
from render_agent.app.security import require_token
from render_agent.pipeline import view_choice
from render_agent.pipeline.job import load_job
from render_agent.validate.checklist_validator import load_view_set

router = APIRouter()


def _read_json(path: Any) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


@router.get("/jobs/{job_id}/views")
def get_views(job_id: str, ctx: Ctx) -> dict[str, Any]:
    d = job_dir_or_404(ctx, job_id)
    spec = _read_json(d / "house_specification.json")
    if not spec:
        raise HTTPException(
            409, {"what_happened": "The drawings are still being read.", "what_to_do": "Wait a moment."}
        )
    dwellings = [x["dwelling_id"] for x in spec.get("DWELLINGS", []) if x.get("dwelling_id")]
    before = _read_json(d / view_choice.CHOSEN)
    return {
        "views": view_choice.options(d, load_view_set(ctx.settings.path("knowledge"))),
        "dwellings": dwellings or ["DWELLING 1"],
        "words": before.get("words", ""),
        "from_file": view_choice.instruction_file(d),
    }


@router.post("/jobs/{job_id}/views", dependencies=[Depends(require_token)])
def choose_views(job_id: str, body: ChooseViewsIn, ctx: Ctx) -> JobOut:
    """The exact views to plan (required views are always added). Planning starts at once."""
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    if job.state.get("status") != "AWAITING_VIEWS":
        raise HTTPException(
            409,
            {"what_happened": "This job is not waiting for a view choice.", "what_to_do": "Go back to Home."},
        )
    view_set = load_view_set(ctx.settings.path("knowledge"))
    known = {v["id"] for v in view_set["views"]}
    if unknown := [v for v in body.views if v not in known]:
        raise HTTPException(
            422, {"what_happened": f"Unknown view: {', '.join(unknown)}.", "what_to_do": "Refresh the page."}
        )
    wanted = set(body.views) | {v["id"] for v in view_set["views"] if v.get("required")}
    if not body.views:
        raise HTTPException(
            422, {"what_happened": "Choose at least one view.", "what_to_do": "Tick the views to make."}
        )
    ids = [v["id"] for v in view_set["views"] if v["id"] in wanted]
    view_choice.choose(d, ids, body.words, by="designer (app)")
    job.set_status("RUNNING")
    ctx.worker.enqueue(job.id)
    return summary(ctx, d, full=True)
