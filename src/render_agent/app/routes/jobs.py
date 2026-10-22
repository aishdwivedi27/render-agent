"""Job list, one job's status, Retry, Cancel, opening its folders and serving its images."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from render_agent.app import designer, plain
from render_agent.app.main import AppContext
from render_agent.app.models import JobOut, JobSummary, OpenFolderIn, Problem, Progress
from render_agent.app.routes import Ctx
from render_agent.app.security import job_file, require_token
from render_agent.app.worker import find_job_dir
from render_agent.errors import AgentError
from render_agent.output.folders import names
from render_agent.pipeline.job import STAGES, cancel_requested, load_job
from render_agent.validate.checklist_validator import load_view_set

router = APIRouter()
RETRYABLE = {"FAILED", "PAUSED"}


def job_dir_or_404(ctx: AppContext, job_id: str) -> Path:
    d = find_job_dir(ctx.settings, job_id)
    if d is None:
        raise HTTPException(
            404, {"what_happened": "That job was not found.", "what_to_do": "Go back to Home."}
        )
    return d


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def progress(job_dir: Path, state: dict[str, Any]) -> Progress:
    prompts = _read(job_dir / "prompts" / "index.json")
    index = _read(job_dir / "output" / "render_index.json")
    if prompts and state["stages"].get("s7_render", {}).get("status") in ("running", "done"):
        total = len(prompts.get("renders", []))
        done = sum(1 for r in (index or {}).get("renders", {}).values() if r.get("status") != "pending")
        return Progress(done=min(done, total), total=total)
    finished = sum(1 for n in STAGES if state["stages"].get(n, {}).get("status") == "done")
    return Progress(done=finished, total=len(STAGES))


def summary(ctx: AppContext, job_dir: Path, full: bool = False) -> JobOut:
    job = load_job(job_dir)
    st = job.state
    status = st.get("status", "NEW")
    stage = job.first_unfinished_stage()
    queued = ctx.worker.position(job.id)
    info = st.get("designer") or {}
    text = plain.status_text(status)
    cancelling = cancel_requested(job_dir)
    if cancelling:
        text = plain.status_text("CANCELLING")
    elif queued and queued > 0:
        text = plain.status_text("QUEUED")
    elif queued == 0 and status not in ("AWAITING_VIEWS", "AWAITING_APPROVAL", "AWAITING_REVIEW"):
        text = plain.status_text("RUNNING")
    error = plain.error_of(job_dir) if status in RETRYABLE else None
    out = JobOut(
        id=job.id,
        input_name=info.get("input_name") or job.id,
        status=status,
        status_text=text,
        stage_text=plain.STAGE_TEXT.get(stage or "", "") if status not in ("SUCCEEDED",) else "",
        screen=plain.screen_for(status),
        queued=queued,
        can_retry=status in RETRYABLE and queued is None and not cancelling,
        can_cancel=status != "SUCCEEDED" and not cancelling,
        error=Problem(**error) if error else None,
        warnings=list(info.get("warnings") or []),
        updated=st.get("updated", ""),
        output_folder=info.get("output"),
        **plain.step_of(stage, status),
    )
    if full:
        out.progress = progress(job_dir, st)
        out.history_folder = info.get("history")
        out.publish_problem = info.get("publish_problem")
        n = names(load_view_set(ctx.settings.path("knowledge")))
        out.folders = {k: str(n[k]) for k in ("shared", "draft", "draft_final", "final")}
    return out


def all_jobs(ctx: AppContext) -> list[JobSummary]:
    dirs = []
    for root in (ctx.settings.path("jobs"), ctx.settings.path("failed")):
        if root.is_dir():
            dirs += [p.parent for p in root.glob("*/state.json")]
    out: list[JobSummary] = []
    for d in dirs:
        try:
            out.append(summary(ctx, d))
        except Exception:  # an unreadable job never hides the others
            continue
    return sorted(out, key=lambda j: j.updated, reverse=True)


@router.get("/jobs")
def list_jobs(ctx: Ctx) -> list[JobSummary]:
    return all_jobs(ctx)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, ctx: Ctx) -> JobOut:
    return summary(ctx, job_dir_or_404(ctx, job_id), full=True)


@router.post("/jobs/{job_id}/retry", dependencies=[Depends(require_token)])
def retry(job_id: str, ctx: Ctx) -> JobOut:
    """Resumes the existing job from where it stopped (designers never re-drop files)."""
    d = job_dir_or_404(ctx, job_id)
    ctx.reload_settings()  # a key added in Settings since the job paused
    ctx.worker.enqueue(job_id)
    return summary(ctx, d, full=True)


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
def cancel(job_id: str, ctx: Ctx) -> dict[str, Any]:
    """Removes the job, its renders and its Output folder; the drawing goes back to Input.
    A running job stops after its current step and is removed then."""
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    if job.state.get("status") == "SUCCEEDED":
        raise HTTPException(
            409,
            {
                "what_happened": "A finished job cannot be cancelled.",
                "what_to_do": "Its images are in Output; delete them there if you do not need them.",
            },
        )
    if not ctx.worker.cancel(job_id, d):
        return {"cancelled": "pending", "warnings": []}
    try:
        warnings = designer.cancel_job(ctx.settings, job)
    except AgentError as err:
        raise HTTPException(409, plain.problem(err.what_happened, err.what_to_do)) from err
    return {"cancelled": True, "warnings": warnings}


@router.post("/jobs/{job_id}/open-folder", dependencies=[Depends(require_token)])
def open_folder(job_id: str, body: OpenFolderIn, ctx: Ctx) -> dict[str, str]:
    info = load_job(job_dir_or_404(ctx, job_id)).state.get("designer") or {}
    target = info.get(body.which)
    if not target or not Path(target).is_dir():
        raise HTTPException(
            404,
            {
                "what_happened": f"The {body.which.title()} folder for this job is not there yet.",
                "what_to_do": "It appears once the first renders are made, or it was moved or deleted.",
            },
        )
    ctx.open_folder(Path(target))
    return {"opened": target}


@router.get("/jobs/{job_id}/file/{rel:path}")
def file(job_id: str, rel: str, ctx: Ctx) -> FileResponse:
    path = job_file(job_dir_or_404(ctx, job_id), rel)
    if path is None:
        raise HTTPException(404, {"what_happened": "Image not found.", "what_to_do": "Refresh the page."})
    return FileResponse(path, headers={"Cache-Control": "no-store"})
