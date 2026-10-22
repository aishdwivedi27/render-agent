"""Runs stages in order from the first unfinished one; stops at the approval gate and at review;
maps exceptions to statuses per the error table (TransientError retried inside the stage,
PauseJobError -> PAUSED, InputError -> failed/, PermanentError -> stage failed, job FAILED)."""

from __future__ import annotations

import importlib
import json
import logging
import shutil
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from render_agent.config import Settings
from render_agent.errors import AgentError, InputError, PauseJobError
from render_agent.pipeline import gate, view_choice
from render_agent.pipeline.job import Job, cancel_requested

log = logging.getLogger("render_agent.runner")
StageFn = Callable[["RunContext"], str | None]


@dataclass
class RunContext:
    job: Job
    settings: Settings
    dry_run: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


class StageWaiting(Exception):
    """A stage that needs a person before it can finish (e.g. review). The job takes `status`;
    the stage stays pending, so the next run picks it up again."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class RunResult:
    status: str
    stopped_at: str | None
    message: str


def stage_function(name: str) -> StageFn | None:
    """A stage module's `run(ctx)`; None while the stage is still a stub."""
    if name in ("gate_approval", "gate_views"):
        return None
    module = importlib.import_module(f"render_agent.pipeline.stages.{name}")
    fn = getattr(module, "run", None)
    return fn if callable(fn) else None


class JobBusy(Exception):
    """Another process is already running this job."""


@contextmanager
def job_lock(job_dir: Path) -> Iterator[None]:
    """An OS file lock on jobs/<id>/.running.lock. The OS releases it if the process dies, so a crash
    or reboot never leaves a stale lock behind."""
    path = job_dir / ".running.lock"
    fh = path.open("a+b")
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise JobBusy(str(job_dir)) from exc
        yield
    finally:
        fh.close()  # closing the handle releases the lock
        with suppress(OSError):  # another run holds it now; that run removes the file
            path.unlink()


def disk_low(ctx: RunContext) -> PauseJobError | None:
    need = ctx.settings.watcher.min_free_disk_gb
    free = shutil.disk_usage(ctx.job.dir).free / 1e9
    if free >= need:
        return None
    return PauseJobError(
        code="DISK_LOW",
        what_happened=f"Only {free:.1f} GB free on the job drive; at least {need} GB is required.",
        likely_cause="Old jobs, renders or other files have filled the disk.",
        what_to_do="Free up disk space (for example archive finished jobs), then resume the job.",
    )


def run_job(ctx: RunContext, stages: dict[str, StageFn | None] | None = None) -> RunResult:
    try:
        with job_lock(ctx.job.dir):
            result = _run_job(ctx, stages)
        if ctx.extras.pop("_move_to_failed", False):  # after the lock is released (Windows)
            _move_to_failed(ctx.job, ctx.settings.path("failed"))
        return result
    except JobBusy:
        log.warning("job %s is already being processed by another run", ctx.job.id)
        return RunResult(
            ctx.job.state.get("status", "RUNNING"),
            None,
            "This job is already being processed by another run; nothing was done.",
        )


def _run_job(ctx: RunContext, stages: dict[str, StageFn | None] | None) -> RunResult:
    job = ctx.job
    job.state["dry_run"] = ctx.dry_run
    job.set_status("RUNNING")
    while (name := job.first_unfinished_stage()) is not None:
        if cancel_requested(job.dir):
            log.info("job %s: cancelled before stage %s", job.id, name)
            return RunResult("CANCELLED", name, "The designer cancelled this job.")
        if name == "gate_views":
            if view_choice.needs_choice(job) and not view_choice.is_chosen(job.dir):
                job.set_status("AWAITING_VIEWS")
                return RunResult("AWAITING_VIEWS", name, "Waiting for the designer to choose the views.")
            job.mark_stage_done(name, view_choice.CHOSEN if view_choice.is_chosen(job.dir) else None)
            continue
        if name == "gate_approval":
            try:
                approved = gate.check_gate(job)
            except AgentError as err:
                return _handle_error(ctx, name, err)
            if not approved:
                job.set_status("AWAITING_APPROVAL")
                return RunResult("AWAITING_APPROVAL", name, _gate_message(job))
            job.mark_stage_done(name, gate.APPROVAL)
            log.info("job %s: approval gate passed", job.id)
            continue
        if (low := disk_low(ctx)) is not None:
            return _handle_error(ctx, name, low)
        fn = stages[name] if stages is not None and name in stages else stage_function(name)
        if fn is None:
            job.save()
            return RunResult(job.state["status"], name, f"Stage {name} is not implemented yet.")
        job.mark_stage_started(name)
        log.info("job %s: stage %s started", job.id, name)
        try:
            artifact = fn(ctx)
        except StageWaiting as wait:
            job.state["stages"][name]["status"] = "pending"
            job.set_status(wait.status)
            log.info("job %s: stage %s waiting: %s", job.id, name, wait.message)
            return RunResult(wait.status, name, wait.message)
        except AgentError as err:
            return _handle_error(ctx, name, err)
        job.mark_stage_done(name, artifact)
        log.info("job %s: stage %s done", job.id, name)
    job.set_status("SUCCEEDED")
    return RunResult("SUCCEEDED", None, "All stages finished.")


def _gate_message(job: Job) -> str:
    blocking = json.loads((job.dir / gate.APPROVAL).read_text(encoding="utf-8")).get("blocking", [])
    msg = "Waiting for the designer to approve the plan (approval.json)."
    return msg + "".join(f"\n  - {b}" for b in blocking)


def _handle_error(ctx: RunContext, stage: str, err: AgentError) -> RunResult:
    job = ctx.job
    job.mark_stage_failed(stage, err)
    job.write_error(err, stage)
    log.error("job %s: stage %s: %s", job.id, stage, err)
    if isinstance(err, PauseJobError):
        job.state["stages"][stage]["status"] = "pending"  # resumable once fixed
        job.set_status("PAUSED")
        resume = f'Then run: python -m render_agent resume "{job.dir}"'
        return RunResult("PAUSED", stage, f"{err.what_happened}\n  {err.what_to_do}\n  {resume}")
    job.set_status("FAILED")
    if isinstance(err, InputError):
        ctx.extras["_move_to_failed"] = True
    return RunResult("FAILED", stage, err.what_happened)


def _move_to_failed(job: Job, failed_dir: Path) -> None:
    from render_agent.logging_setup import close_job_logs

    close_job_logs(job.dir)  # an open job.log would block the move on Windows
    failed_dir.mkdir(parents=True, exist_ok=True)
    dest = failed_dir / job.dir.name
    shutil.move(str(job.dir), str(dest))
    job.dir = dest
