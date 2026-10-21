"""Approval screen: the planned views and materials, gaps to fill, fact corrections, Approve.

Reads approval.json / selections.json and calls gate.approve: no gate logic lives here.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent.app import feedback
from render_agent.app.models import ApproveIn, JobOut, ReplanIn
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import job_dir_or_404, summary
from render_agent.app.security import require_token
from render_agent.pipeline import corrections, gate, view_choice
from render_agent.pipeline.job import Job, load_job
from render_agent.util.directives import parse_directives
from render_agent.util.fs import atomic_write_json
from render_agent.validate.checklist_validator import Options, ViewRules, load_view_set

router = APIRouter()
NOT_RENDERED = {"NOT READY"}
PREVIOUS = "approval.prev.json"  # the plan before the designer's last re-plan, for "what changed"
REPLAN_STAGES = ["s4_spec", "s5_checklist", "gate_approval"]
LOCKED_RULES = {"missing_drawing", "insert_view"}  # validator blockers the designer cannot override
# reasons a view was left out that the designer's own view choice can undo
ADDABLE = ("not in the @views directive", "optional view not requested")


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
    locked = _not_overridable(d)
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
                "blockers": r.get("blockers") or [],
                # a blocked view the designer may still have rendered (marked for a closer look);
                # never one whose drawing type is missing from the set
                "can_render_anyway": r["readiness"] in NOT_RENDERED and r["render_id"] not in locked,
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
        "not_made": not_made(ctx, d),
        "views_chosen": parse_directives(_instructions(d)).views,
        "changes": changes(d, a, sel),
    }


def _not_overridable(d: Any) -> set[str]:
    """Renders blocked by code, not by a reading of the drawings: a drawing type missing from the set,
    or a required view the plan left out (decision 21 Oct 2026: these stay blocked)."""
    path = d / "checklist_validation.json"
    corrections_ = _read(path).get("corrections", []) if path.is_file() else []
    return {c["render_id"] for c in corrections_ if c.get("rule") in LOCKED_RULES and c.get("render_id")}


def _render_anyway(d: Any, ids: list[str], by: str) -> list[str]:
    """Blocked views the designer chose to have rendered: readiness REVIEW (marked for a closer look),
    blockers kept, recorded in checklist.json and approval.json. Returns the ids applied."""
    if not ids:
        return []
    locked = _not_overridable(d)
    checklist = _read(d / "checklist.json")
    done = []
    for r in checklist["RENDER_REQUIREMENTS"]:
        if r["render_id"] in ids and r["readiness"] in NOT_RENDERED and r["render_id"] not in locked:
            r["readiness"] = "REVIEW"
            r["designer_override"] = f"rendered anyway at approval by {by}"
            done.append(r["render_id"])
    if done:
        atomic_write_json(d / "checklist.json", checklist)
        a = _read(d / gate.APPROVAL)
        for r in a["plan"]["renders"]:
            if r["render_id"] in done:
                r["readiness"] = "REVIEW"
        a["rendered_anyway"] = done
        atomic_write_json(d / gate.APPROVAL, a)
    return done


def _instructions(d: Any) -> str:
    path = d / "ingest.json"
    return str(_read(path).get("instructions", "")) if path.is_file() else ""


def not_made(ctx: Any, d: Any) -> list[dict[str, Any]]:
    """Views left out of the plan, each with its plain reason and whether the designer can add it."""
    view_set = load_view_set(ctx.settings.path("knowledge"))
    rules = ViewRules(view_set, Options(dwellings=[], limit=0))
    labels = {v["id"]: v.get("label", v["id"]) for v in view_set["views"]}
    rows: dict[tuple[str, str], dict[str, Any]] = {}  # one row per view and reason, all its dwellings
    for o in _read(d / "checklist.json").get("OMITTED_VIEWS", []):
        vid = rules.view_id({"view_type": o["view_type"], "render_id": "", "location": ""})
        reason = o.get("reason_omitted", "")
        row = rows.setdefault(
            (vid or o["view_type"], reason),
            {
                "dwellings": [],
                "view_id": vid,
                "view": labels.get(vid or "", o["view_type"]),
                "why_not": reason.replace("the @views directive", "your view choice"),
                "addable": bool(vid) and reason.startswith(ADDABLE),
            },
        )
        if o["dwelling_id"] not in row["dwellings"]:
            row["dwellings"].append(o["dwelling_id"])
    return [{**r, "dwelling": ", ".join(r["dwellings"])} for r in rows.values()]


def changes(d: Any, now: dict[str, Any], sel: dict[str, Any]) -> list[str] | None:
    """What the last re-plan changed (None when there was no re-plan)."""
    path = d / PREVIOUS
    if not path.is_file():
        return None
    prev = _read(path)
    old = {r["render_id"]: r for r in prev["approval"]["plan"]["renders"]}
    new = {r["render_id"]: r for r in now["plan"]["renders"]}
    out = [f"Added: {new[k]['view_type']} ({new[k]['dwelling_id']})" for k in new if k not in old]
    out += [f"Removed: {old[k]['view_type']} ({old[k]['dwelling_id']})" for k in old if k not in new]
    for k in new.keys() & old.keys():
        if new[k]["readiness"] != old[k]["readiness"]:
            out.append(f"{new[k]['view_type']}: now {new[k]['readiness']} (was {old[k]['readiness']})")
    was = {i["key"]: i for i in prev.get("selections", {}).get("items", [])}
    for i in sel["items"]:
        before = was.get(i["key"], {}).get("pdf_value")
        if before is not None and before != i.get("pdf_value"):
            out.append(f"{i['label']}: now '{i.get('pdf_value')}' (was '{before}')")
    old_u = prev["approval"].get("drawing_uncertainties", [])
    new_u = now.get("drawing_uncertainties", [])
    out += [f"Resolved: {u}" for u in old_u if u not in new_u]
    out += [f"New question: {u}" for u in new_u if u not in old_u]
    return out


def _waiting(job: Job) -> None:
    if job.state.get("status") != "AWAITING_APPROVAL":
        raise HTTPException(
            409, {"what_happened": "This job is not waiting for approval.", "what_to_do": "Go back to Home."}
        )


def _replan(ctx: Any, d: Any, job: Job, stages: list[str]) -> JobOut:
    """Keeps the current plan for "what changed", re-runs `stages` and queues the job."""
    previous = {"approval": _read(d / gate.APPROVAL), "selections": _read(d / "selections.json")}
    atomic_write_json(d / PREVIOUS, previous)
    job.reset_stages(stages)
    job.set_status("RUNNING")
    ctx.worker.enqueue(job.id)
    return summary(ctx, d, full=True)


@router.post("/jobs/{job_id}/approval/choose-views", dependencies=[Depends(require_token)])
def choose_views_again(job_id: str, ctx: Ctx) -> JobOut:
    """Back to Choose views: the plan is made again after the new choice (decision 21 Oct 2026)."""
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    _waiting(job)
    previous = {"approval": _read(d / gate.APPROVAL), "selections": _read(d / "selections.json")}
    atomic_write_json(d / PREVIOUS, previous)
    view_choice.reopen(job)
    job.set_status("RUNNING")
    ctx.worker.enqueue(job.id)
    return summary(ctx, d, full=True)


@router.post("/jobs/{job_id}/approval/replan", dependencies=[Depends(require_token)])
def replan(job_id: str, body: ReplanIn, ctx: Ctx) -> JobOut:
    """Corrections of what the drawings mean: saved for this job only, then the specification and
    plan are made again with them (LLM, no images) and the revised plan comes back for approval."""
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    _waiting(job)
    warnings = _record_corrections(ctx, d, job, body.corrections)
    out = _replan(ctx, d, job, REPLAN_STAGES)
    out.warnings += warnings
    return out


def _record_corrections(ctx: Any, d: Any, job: Job, items: list[Any]) -> list[str]:
    warnings = []
    for c in items:  # data only: never a directive, limit or provider change
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
    return warnings


@router.post("/jobs/{job_id}/approval", dependencies=[Depends(require_token)])
def approve(job_id: str, body: ApproveIn, ctx: Ctx) -> JobOut:
    d = job_dir_or_404(ctx, job_id)
    job = load_job(d)
    if job.state.get("status") != "AWAITING_APPROVAL":
        raise HTTPException(
            409, {"what_happened": "This job is not waiting for approval.", "what_to_do": "Go back to Home."}
        )
    warnings = _record_corrections(ctx, d, job, body.corrections)
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
    _render_anyway(d, body.render_anyway, "designer (app)")
    ctx.worker.enqueue(job.id)
    out = summary(ctx, d, full=True)
    out.warnings += warnings
    return out
