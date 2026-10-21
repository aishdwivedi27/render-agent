"""Plain words for designers: job statuses, stages and error.json (never a stack trace)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATUS_TEXT = {
    "NEW": "Starting…",
    "QUEUED": "Waiting its turn",
    "RUNNING": "Working on it…",
    "RENDERING": "Making your renders…",
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
    "gate_views": "Waiting for you to choose the views",
    "s5_checklist": "Planning the views",
    "gate_approval": "Waiting for your approval",
    "s6_prompts": "Preparing each render",
    "s7_render": "Making the renders",
    "s8_check": "Checking renders against the drawings",
    "s9_review": "Getting renders ready for review",
}

# the five steps the app shows, each with the pipeline stages that are its parts
STEPS = [
    ("Read the drawings", ["s1_ingest", "s2_sheets", "s3_evidence", "s4_spec"]),
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


# the screen the app shows for a job in each status
SCREEN = {
    "AWAITING_VIEWS": "views",
    "AWAITING_APPROVAL": "approval",
    "AWAITING_REVIEW": "review",
    "SUCCEEDED": "results",
}


def status_text(status: str) -> str:
    return STATUS_TEXT.get(status, "Working on it…")


def screen_for(status: str) -> str:
    return SCREEN.get(status, "progress")


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
