"""Background job worker: one job runs at a time, the rest wait in a queue.

The worker only calls pipeline.start.continue_job (the same code the CLI uses); state.json stays
the single source of truth. The queue is saved to jobs/queue.json, so after a crash or a reboot
reopening the app resumes the queued and running jobs.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from render_agent.app import designer
from render_agent.config import Settings
from render_agent.errors import AgentError
from render_agent.pipeline.job import CANCEL_FILE, cancel_requested, load_job
from render_agent.pipeline.start import continue_job
from render_agent.util.fs import atomic_write_json, atomic_write_text

QUEUE_FILE = "queue.json"
RESUME_ON_LAUNCH = {"NEW", "RUNNING", "RENDERING"}  # stopped mid-work (crash, reboot, app closed)
log = logging.getLogger("render_agent.worker")


def find_job_dir(settings: Settings, job_id: str) -> Path | None:
    """A job folder by id, in jobs/ or failed/; None for anything that is not a plain job id."""
    if not job_id or any(c in job_id for c in "/\\:") or job_id in (".", ".."):
        return None
    for root in (settings.path("jobs"), settings.path("failed")):
        d = root / job_id
        if (d / "state.json").is_file():
            return d
    return None


class JobWorker:
    def __init__(
        self,
        settings: Callable[[], Settings],
        extras: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.extras = extras or dict
        self._cond = threading.Condition()
        self._queue: list[str] = []
        self.current: str | None = None
        self._stop = False
        self._thread: threading.Thread | None = None

    # --- queue ---------------------------------------------------------------------------------
    def _queue_file(self) -> Path:
        return self.settings().path("jobs") / QUEUE_FILE

    def _save(self) -> None:
        pending = ([self.current] if self.current else []) + self._queue
        atomic_write_json(self._queue_file(), {"queue": pending})

    def enqueue(self, job_id: str) -> None:
        with self._cond:
            if job_id != self.current and job_id not in self._queue:
                self._queue.append(job_id)
                self._save()
            self._cond.notify()

    def position(self, job_id: str) -> int | None:
        """0 while running, 1.. while waiting, None when not queued."""
        with self._cond:
            if job_id == self.current:
                return 0
            return self._queue.index(job_id) + 1 if job_id in self._queue else None

    def cancel(self, job_id: str, job_dir: Path) -> bool:
        """True when the job can be removed now; False when it is running and stops at its next
        step (run_one removes it then)."""
        with self._cond:
            if job_id == self.current:
                atomic_write_text(job_dir / CANCEL_FILE, "cancel\n")
                return False
            if job_id in self._queue:
                self._queue.remove(job_id)
                self._save()
            return True

    def resume_on_launch(self) -> list[str]:
        """Queues what was queued or running when the app last closed."""
        ids: list[str] = []
        qf = self._queue_file()
        if qf.is_file():
            try:
                ids = list(json.loads(qf.read_text(encoding="utf-8")).get("queue", []))
            except ValueError:
                ids = []
        jobs_dir = self.settings().path("jobs")
        for state_file in sorted(jobs_dir.glob("*/state.json")) if jobs_dir.is_dir() else []:
            try:
                status = json.loads(state_file.read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                continue
            if status in RESUME_ON_LAUNCH and state_file.parent.name not in ids:
                ids.append(state_file.parent.name)
        for job_id in ids:
            if find_job_dir(self.settings(), job_id):
                self.enqueue(job_id)
        return ids

    # --- running -------------------------------------------------------------------------------
    def run_one(self, job_id: str) -> None:
        settings = self.settings()
        job_dir = find_job_dir(settings, job_id)
        if job_dir is None:
            return
        try:
            result = continue_job(load_job(job_dir), settings, self.extras())
            log.info("job %s: %s", job_id, result.status)
        except AgentError as err:  # e.g. state.json unreadable: shown on the job card
            log.error("job %s: %s", job_id, err)
        except Exception:  # never let one job stop the worker
            log.exception("job %s: unexpected error", job_id)
        job_dir = find_job_dir(settings, job_id)
        if job_dir is not None and cancel_requested(job_dir):
            try:
                designer.cancel_job(settings, load_job(job_dir))
            except AgentError as err:  # originals could not go back: the job stays, marker removed
                log.error("job %s: cancel failed: %s", job_id, err)
                (job_dir / CANCEL_FILE).unlink(missing_ok=True)
            return
        if job_dir is not None:
            job = load_job(job_dir)
            problem = designer.publish(job) or designer.complete(settings, job)
            info = job.state.get("designer")
            if info is not None and info.get("publish_problem") != problem:
                info["publish_problem"] = problem
                job.save()

    def _next(self) -> str | None:
        with self._cond:
            while not self._queue and not self._stop:
                self._cond.wait(timeout=1.0)
            if self._stop:
                return None
            self.current = self._queue.pop(0)
            self._save()
            return self.current

    def _done(self) -> None:
        with self._cond:
            self.current = None
            self._save()

    def _loop(self) -> None:
        while (job_id := self._next()) is not None:
            try:
                self.run_one(job_id)
            finally:
                self._done()

    def start(self) -> None:
        self.resume_on_launch()
        self._thread = threading.Thread(target=self._loop, name="render-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    def drain(self) -> None:
        """Runs everything queued, in this thread (tests and the no-thread mode)."""
        while True:
            with self._cond:
                if not self._queue:
                    return
                self.current = self._queue.pop(0)
                self._save()
            try:
                self.run_one(self.current)
            finally:
                self._done()
