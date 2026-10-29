"""Plain words for designers: job statuses, stages and error.json (never a stack trace)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from render_agent.pipeline.job import PLAN_STAGES

STATUS_TEXT = {
    "NEW": "Starting…",
    "QUEUED": "Waiting its turn",
    "CANCELLING": "Cancelling…",
    "RUNNING": "Working on it…",
    "RENDERING": "Making your renders…",
    "AWAITING_PLAN_START": "Start the plan check",
    "AWAITING_PLAN_CHECK": "Check the plan",
    "AWAITING_ELEVATION_CHOICE": "Do the elevation readout, or skip it?",
    "AWAITING_ELEVATION_CHECK": "Check the elevations: questions for you",
    "AWAITING_STYLES": "Choose plan styles",
    "AWAITING_FURNISH_CHECK": "Check the furniture",
    "AWAITING_PLAN_REVIEW": "Review the plans",
    "AWAITING_CONTINUE": "Plans are ready",
    "PLAN_COMPLETE": "Finished: plans only",
    "AWAITING_VIEWS": "Choose the views",
    "AWAITING_APPROVAL": "Waiting for your approval",
    "AWAITING_REVIEW": "Ready for your review",
    "SUCCEEDED": "Finished",
    "FAILED": "Something went wrong",
    "PAUSED": "Paused, needs your attention",
}

STAGE_TEXT = {
    "s1_ingest": "Opening the drawings",
    "s2_sheets": "Reading each sheet",
    "s3_evidence": "Collecting the facts",
    "s4_spec": "Writing the house specification",
    "gate_plan_start": "Waiting for you to start the plan check",
    "p1_trace": "Tracing the floor plan",
    "gate_plan_check": "Waiting for your answers on the floor plan",
    "gate_elevation_choice": "Waiting for you: elevation readout or skip",
    "p1e_elevations": "Reading the elevations",
    "gate_elevation_check": "Waiting for your answers on the elevations",
    "gate_styles": "Waiting for you to choose plan styles",
    "p2_furnish": "Proposing the furniture",
    "gate_furnish_check": "Waiting for you to check the furniture",
    "p3_plan_build": "Drawing the plans",
    "gate_plan_review": "Waiting for you to review the plans",
    "gate_continue": "Plans are ready: finish or choose renders",
    "gate_views": "Waiting for you to choose the views",
    "s5_checklist": "Planning the views",
    "gate_approval": "Waiting for your approval",
    "s6_prompts": "Preparing each render",
    "s7_render": "Making the renders",
    "s8_check": "Checking renders against the drawings",
    "s9_review": "Getting renders ready for review",
}

# the steps the app shows, each with the pipeline stages that are its parts
STEPS = [
    ("Read the drawings", ["s1_ingest", "s2_sheets", "s3_evidence", "s4_spec"]),
    ("Plans", PLAN_STAGES),
    ("Choose views", ["gate_views"]),
    ("Approve the plan", ["s5_checklist", "gate_approval"]),
    ("Make the renders", ["s6_prompts", "s7_render", "s8_check"]),
    ("Review", ["s9_review"]),
    ("Done", []),
]


def step_of(stage: str | None, status: str) -> dict[str, Any]:
    """{step (1-5), step_name, steps, part, parts, part_name} for the job's current stage."""
    n = len(STEPS) - 1 if status == "SUCCEEDED" or stage is None else 0
    if n == 0:
        n = next((i for i, (_, stages) in enumerate(STEPS) if stage in stages), 0)
    name, stages = STEPS[n]
    part = stages.index(stage) + 1 if stage in stages else 0
    return {
        "step": n + 1,
        "step_name": name,
        "steps": len(STEPS),
        "part": part,
        "parts": len(stages),
        "part_name": STAGE_TEXT.get(stage or "", "") if part else "",
    }


# plan visuals screens not built yet: status -> the PV step that builds it (a placeholder until then)
COMING_IN = {
    "AWAITING_PLAN_CHECK": 3,  # the floor plan questions; PV2 shows the trace first (screen plan_check)
    "AWAITING_ELEVATION_CHOICE": 4,
    "AWAITING_ELEVATION_CHECK": 4,
    "AWAITING_STYLES": 5,
    "AWAITING_FURNISH_CHECK": 6,
    "AWAITING_PLAN_REVIEW": 9,
    "AWAITING_CONTINUE": 10,
    "PLAN_COMPLETE": 10,
}

# the screen the app shows for a job in each status
SCREEN = {
    "AWAITING_PLAN_START": "plan_start",
    "AWAITING_VIEWS": "views",
    "AWAITING_APPROVAL": "approval",
    "AWAITING_REVIEW": "review",
    "SUCCEEDED": "results",
}


def status_text(status: str) -> str:
    return STATUS_TEXT.get(status, "Working on it…")


def screen_for(status: str) -> str:
    if status in COMING_IN:
        return "coming"
    return SCREEN.get(status, "progress")


def coming_text(status: str) -> str:
    """The placeholder line for a plan visuals screen that a later PV step builds."""
    step = COMING_IN.get(status)
    return f"This screen is coming in PV{step}." if step else ""


def error_of(job_dir: Path) -> dict[str, str] | None:
    """error.json as {title, what_happened, what_to_do}; None when there is none."""
    path = job_dir / "error.json"
    if not path.is_file():
        return None
    try:
        err: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {
            "what_happened": "The job stopped, and its error note could not be read.",
            "what_to_do": "Press Retry. If it stops again, use Help → Copy diagnostics.",
        }
    out = {
        "what_happened": str(err.get("what_happened") or "The job stopped."),
        "what_to_do": str(err.get("what_to_do") or "Press Retry."),
    }
    if err.get("code") == "OUT_OF_CREDIT":
        out["what_to_do"] += " Then press Retry: finished work is kept and not paid for twice."
    return out


def problem(what_happened: str, what_to_do: str) -> dict[str, str]:
    return {"what_happened": what_happened, "what_to_do": what_to_do}
