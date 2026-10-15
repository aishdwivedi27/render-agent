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
    "s5_checklist": "Planning the views",
    "gate_approval": "Waiting for your approval",
    "s6_prompts": "Preparing each render",
    "s7_render": "Making the renders",
    "s8_check": "Checking renders against the drawings",
    "s9_review": "Getting renders ready for review",
}

# the screen the app shows for a job in each status
SCREEN = {
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
