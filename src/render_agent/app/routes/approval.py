"""Approval screen: the planned views and materials, gaps to fill, fact corrections, Approve.

Reads approval.json / selections.json and calls gate.approve: no gate logic lives here.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent.app import feedback
from render_agent.app.models import ApproveIn, JobOut
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import job_dir_or_404, summary
from render_agent.app.security import require_token
from render_agent.pipeline import corrections, gate
from render_agent.pipeline.job import load_job

router = APIRouter()
NOT_RENDERED = {"NOT READY"}


def _read(path: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/jobs/{job_id}/approval")
def get_approval(job_id: str, ctx: Ctx) -> dict[str, Any]:
    d = job_dir_or_404(ctx, job_id)
    if not (d / gate.APPROVAL).is_file():
        raise HTTPException(
            409,
            {
                "what_happened": "The plan is not ready yet.",
                "what_to_do": "Wait for the job to reach approval.",
            },
        )
    a = _read(d / gate.APPROVAL)
    sel = _read(d / "selections.json")
    renders = a["plan"]["renders"]
    planned = [r for r in renders if r["readiness"] not in NOT_RENDERED]
    if not ctx.settings.planning.render_review_views:
        planned = [r for r in planned if r["readiness"] != "REVIEW"]
    by_dwelling: dict[str, list[dict[str, Any]]] = {}
    for r in renders:
        by_dwelling.setdefault(r["dwelling_id"], []).append(
            {
                "render_id": r["render_id"],
                "view": r["view_type"],
                "where": r["location"],
                "ready": r["readiness"] not in NOT_RENDERED,
                "check": r["readiness"] == "REVIEW",
                "why_not": "; ".join(r.get("blockers") or []),
            }
        )
    materials = [
        {
            "key": i["key"],
            "label": i["label"],
            "from_drawings": i.get("pdf_value"),
            "value": i.get("chosen") or "",
            "source": i.get("chosen_by") or "",
            "options": i.get("options") or [],
            "needs_choice": bool(i.get("needs_choice")),
        }
        for i in sel["items"]
    ]
    return {
        "status": a.get("status"),
        "dwellings": by_dwelling,
        "dwellings_available": a.get("dwellings_available", []),
        "materials": materials,
        "needs_choice": a.get("needs_choice", []),
        "ignored_directives": a.get("ignored_directives", []),
        "uncertainties": a.get("drawing_uncertainties", []),
        "warnings": a.get("warnings", []),
        "blocking": a.get("blocking", []),
        "planned_images": len(planned),
        "corrections": corrections.load(d),
        "practice_run": bool(load_job(d).state.get("dry_run")),
    }


@router.post("/jobs/{job_id}/approval", dependencies=[Depends(require_token)])
def approve(job_id: str, body: ApproveIn, ctx: Ctx) -> JobOut:
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    if job.state.get("status") != "AWAITING_APPROVAL":
        raise HTTPException(
            409, {"what_happened": "This job is not waiting for approval.", "what_to_do": "Go back to Home."}
        )
    warnings = []
    for c in body.corrections:  # data only: never a directive, limit or provider change
        corrections.add(d, c.fact, c.value, c.note, by="designer", where="approval")
        w = feedback.record(
            ctx.settings,
            {
                "job_id": job.id,
                "view": None,
                "category": "fact_correction",
                "correction": f"{c.fact}: {c.value}" + (f" ({c.note})" if c.note else ""),
                "suggest_rule": c.suggest_rule,
                "where": "approval",
            },
        )
        if w:
            warnings.append(w)
    problems = gate.approve(job, body.choices, by="designer (app)", dwellings=body.dwellings)
    if problems:
        raise HTTPException(
            422,
            {
                "what_happened": "The plan cannot be approved yet.",
                "what_to_do": "Fill in the items marked below, then press Approve again.",
                "problems": problems,
            },
        )
    ctx.worker.enqueue(job.id)
    out = summary(ctx, d, full=True)
    out.warnings += warnings
    return out
