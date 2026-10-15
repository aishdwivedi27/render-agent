"""Starting and continuing jobs: shared by the CLI and the desktop app (never duplicated)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from render_agent.config import Settings
from render_agent.logging_setup import attach_job_log, detach_job_log
from render_agent.pipeline.job import Job
from render_agent.pipeline.runner import RunContext, RunResult, run_job
from render_agent.util.fs import sha256_file


def drop_inputs(src: Path) -> list[Path]:
    """The files of one job: a folder's PDFs and .txt files, or a PDF plus a same-named .txt."""
    if src.is_dir():
        return sorted(src.glob("*.pdf")) + sorted(src.glob("*.txt")) + sorted(src.glob("*selections.yaml"))
    sides = [src.with_suffix(".txt"), src.with_suffix(".selections.yaml")]
    return [src, *(p for p in sides if p.is_file())]


def batch_sources(folder: Path) -> list[Path]:
    """Every PDF directly in the folder (its same-named .txt goes with it) and every sub-folder."""
    return sorted(p for p in folder.iterdir() if p.is_dir() or p.suffix.lower() == ".pdf")


def existing_job(jobs_dir: Path, inputs: list[Path]) -> Path | None:
    """A job already made from exactly these PDFs (same SHA-256), so a set is never processed twice."""
    want = sorted(sha256_file(p) for p in inputs if p.suffix.lower() == ".pdf")
    if not want or not jobs_dir.is_dir():
        return None
    for state_file in sorted(jobs_dir.glob("*/state.json")):
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        have = sorted(i["sha256"] for i in state.get("inputs", []) if i["name"].lower().endswith(".pdf"))
        if have == want:
            return state_file.parent
    return None


def continue_job(job: Job, settings: Settings, extras: dict[str, Any] | None = None) -> RunResult:
    """Runs the job from its first unfinished stage, with the per-job log attached."""
    handler = attach_job_log(job.dir)
    try:
        dry_run = bool(job.state.get("dry_run", False))
        return run_job(RunContext(job=job, settings=settings, dry_run=dry_run, extras=dict(extras or {})))
    finally:
        detach_job_log(handler)
