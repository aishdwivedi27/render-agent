"""Job folder layout and state.json checkpoints. See docs/SPEC.md section 8."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from render_agent.errors import AgentError, InputError, PermanentError
from render_agent.util.fs import atomic_write_json, safe_name, sha256_file

STAGES = [
    "s1_ingest",
    "s2_sheets",
    "s3_evidence",
    "s4_spec",
    "gate_plan_start",
    "p1_trace",
    "gate_plan_check",
    "gate_elevation_choice",
    "p1e_elevations",
    "gate_elevation_check",
    "gate_styles",
    "p2_furnish",
    "gate_furnish_check",
    "p3_plan_build",
    "gate_plan_review",
    "gate_continue",
    "gate_views",
    "s5_checklist",
    "gate_approval",
    "s6_prompts",
    "s7_render",
    "s8_check",
    "s9_review",
]
# Plan visuals stages (docs/PLAN_VISUALS_SPEC.md section 1). Each PV step switches its stages on by
# adding them to ENABLED_PLAN_STAGES; until then new jobs skip them.
PLAN_STAGES = STAGES[STAGES.index("gate_plan_start") : STAGES.index("gate_views")]
# PV1: Start the plan check. PV2: the trace and the read-only Check the plan screen.
ENABLED_PLAN_STAGES: set[str] = {"gate_plan_start", "p1_trace", "gate_plan_check"}
SKIP_NOT_ENABLED = "not enabled yet"
SKIP_ADDED_LATER = "added after this job started"
SUBDIRS = ["input", "pages", "sheets", "references", "prompts", "checks", "output"]
JOB_STATUSES = {
    "NEW",
    "RUNNING",
    "AWAITING_PLAN_START",
    "AWAITING_PLAN_CHECK",
    "AWAITING_ELEVATION_CHOICE",
    "AWAITING_ELEVATION_CHECK",
    "AWAITING_STYLES",
    "AWAITING_FURNISH_CHECK",
    "AWAITING_PLAN_REVIEW",
    "AWAITING_CONTINUE",
    "PLAN_COMPLETE",
    "AWAITING_VIEWS",
    "AWAITING_APPROVAL",
    "RENDERING",
    "AWAITING_REVIEW",
    "SUCCEEDED",
    "FAILED",
    "PAUSED",
}
STAGE_STATUSES = {"pending", "running", "done", "failed", "skipped"}
FINISHED_STAGE_STATUSES = ("done", "skipped")
CANCEL_FILE = "cancel_requested"  # written by the app; the runner stops at the next step


def cancel_requested(job_dir: Path) -> bool:
    return (job_dir / CANCEL_FILE).is_file()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    dir: Path
    state: dict[str, Any]

    @property
    def id(self) -> str:
        return self.dir.name

    @property
    def state_file(self) -> Path:
        return self.dir / "state.json"

    def save(self) -> None:
        self.state["updated"] = _now()
        atomic_write_json(self.state_file, self.state)

    def set_status(self, status: str) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(f"unknown job status {status}")
        self.state["status"] = status
        self.save()

    def stage(self, name: str) -> dict[str, Any]:
        if name not in STAGES:
            raise ValueError(f"unknown stage {name}")
        return self.state["stages"][name]

    def mark_stage_started(self, name: str) -> None:
        st = self.stage(name)
        st.update(status="running", started=_now(), finished=None)
        self.save()

    def mark_stage_done(self, name: str, artifact: str | None = None) -> None:
        st = self.stage(name)
        st.update(status="done", finished=_now(), artifact=artifact)
        self.save()

    def mark_stage_failed(self, name: str, err: AgentError) -> None:
        st = self.stage(name)
        st.update(status="failed", finished=_now())
        self.state["errors"].append({**err.to_dict(), "stage": name, "time": _now()})
        self.save()

    def mark_stage_skipped(self, name: str, reason: str) -> None:
        st = self.stage(name)
        st.update(status="skipped", finished=_now(), reason=reason)
        self.save()

    def reset_stages(self, names: list[str]) -> None:
        """Marks these stages to run again (a re-plan from the approval screen); keeps their files."""
        for name in names:
            self.stage(name).update(status="pending", finished=None)
        self.save()

    def first_unfinished_stage(self) -> str | None:
        for name in STAGES:
            if self.state["stages"][name]["status"] not in FINISHED_STAGE_STATUSES:
                return name
        return None

    def write_error(self, err: AgentError, stage: str | None = None) -> Path:
        path = self.dir / "error.json"
        atomic_write_json(path, {**err.to_dict(), "kind": type(err).__name__, "stage": stage, "time": _now()})
        return path


def _stage_entry(name: str) -> dict[str, Any]:
    if name in PLAN_STAGES and name not in ENABLED_PLAN_STAGES:
        return _skipped(SKIP_NOT_ENABLED)
    return {"status": "pending", "started": None, "finished": None, "artifact": None}


def _skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "started": None, "finished": None, "artifact": None, "reason": reason}


def _new_state(job_id: str, inputs: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": "NEW",
        "created": _now(),
        "updated": _now(),
        "inputs": inputs,
        "stages": {n: _stage_entry(n) for n in STAGES},
        "errors": [],
    }


def new_job(jobs_dir: Path, input_paths: list[Path], now: datetime | None = None) -> Job:
    """Creates jobs/<yyyymmdd-hhmmss>-<stem>/ with the folder layout and copies the inputs in."""
    if not input_paths:
        raise InputError(
            code="NO_INPUT",
            what_happened="No input files were given.",
            what_to_do="Drop a PDF (or a folder of PDFs) into the inbox.",
        )
    for p in input_paths:
        if not p.is_file():
            raise InputError(
                code="INPUT_MISSING",
                what_happened=f"Input file not found: {p.name}",
                what_to_do="Check the path and try again.",
            )
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{safe_name(input_paths[0].stem, 50)}"
    job_dir, n = jobs_dir / base, 1
    while job_dir.exists():
        n += 1
        job_dir = jobs_dir / f"{base}-{n}"
    for sub in SUBDIRS:
        (job_dir / sub).mkdir(parents=True, exist_ok=True)
    inputs = []
    for p in input_paths:
        dest = job_dir / "input" / safe_name(p.name, 150)
        shutil.copy2(p, dest)
        inputs.append({"name": dest.name, "sha256": sha256_file(dest)})
    job = Job(job_dir, _new_state(job_dir.name, inputs))
    job.save()
    return job


def load_job(job_dir: Path) -> Job:
    state_file = job_dir / "state.json"
    if not state_file.is_file():
        raise PermanentError(
            code="JOB_NOT_FOUND",
            what_happened=f"No state.json in {job_dir}.",
            what_to_do="Check the job folder path (jobs/<job-id>).",
        )
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PermanentError(
            code="STATE_CORRUPT",
            what_happened=f"state.json in {job_dir.name} is unreadable.",
            likely_cause="The file was edited by hand or the disk is damaged.",
            what_to_do="Restore state.json or start the job again.",
        ) from exc
    for i, name in enumerate(STAGES):  # stages added in a later version start as pending,
        if name not in state["stages"]:  # or done when the stage after them already ran
            if name in PLAN_STAGES:  # the plan path never applies to a job started without it
                state["stages"][name] = _skipped(SKIP_ADDED_LATER)
                continue
            after = state["stages"].get(STAGES[i + 1], {}) if i + 1 < len(STAGES) else {}
            later_done = after.get("status") == "done"
            state["stages"][name] = {
                "status": "done" if later_done else "pending",
                "started": None,
                "finished": None,
                "artifact": None,
            }
    state["stages"] = {n: state["stages"][n] for n in STAGES}  # pipeline order
    return Job(job_dir, state)
