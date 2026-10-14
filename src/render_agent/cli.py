"""Command line: watch | run | resume | approve | review | doctor."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import shutil
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from render_agent import __version__
from render_agent.config import Settings, load_config
from render_agent.errors import AgentError
from render_agent.logging_setup import attach_job_log, detach_job_log, setup_agent_logging
from render_agent.pipeline import gate
from render_agent.pipeline.job import Job, load_job, new_job
from render_agent.pipeline.runner import RunContext, RunResult, run_job
from render_agent.util.fs import sha256_file

MIN_PYTHON = (3, 12)
WRITABLE_PATHS = ("inbox", "jobs", "failed", "logs")
READABLE_PATHS = ("knowledge", "prompts", "schemas")


def _line(level: str, text: str) -> None:
    print(f"{level:<5} {text}")


def _print_error(err: AgentError) -> None:
    _line("FAIL", err.what_happened)
    if err.likely_cause:
        print(f"      Likely cause: {err.likely_cause}")
    if err.what_to_do:
        print(f"      What to do: {err.what_to_do}")


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".doctor-{uuid.uuid4().hex}.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        return False


def doctor_checks(settings: Settings) -> list[tuple[str, str]]:
    """(level, message) pairs; level is OK, WARN or FAIL."""
    results: list[tuple[str, str]] = []
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    results.append(("OK" if py_ok else "FAIL", f"Python {platform.python_version()} (need 3.12+)"))
    results.append(("OK", f"config: {settings.config_file}"))
    for var in settings.required_key_vars():
        present = settings.secret(var) is not None
        results.append(("OK" if present else "FAIL", f"{var} {'set' if present else 'missing from .env'}"))
    for name in WRITABLE_PATHS:
        folder = settings.path(name)
        ok = _writable(folder)
        results.append(("OK" if ok else "FAIL", f"{name}/ {'writable' if ok else 'not writable'}: {folder}"))
    for name in READABLE_PATHS:
        folder = settings.path(name)
        ok = folder.is_dir()
        results.append(("OK" if ok else "FAIL", f"{name}/ {'found' if ok else 'missing'}: {folder}"))
    free_gb = shutil.disk_usage(settings.root).free / 1e9
    min_gb = settings.watcher.min_free_disk_gb
    results.append(("OK" if free_gb >= min_gb else "FAIL", f"free disk {free_gb:.1f} GB (minimum {min_gb})"))
    from render_agent.images.muapi_cli import doctor_check

    results.append(doctor_check())
    found = shutil.which("ffmpeg")
    results.append(("OK" if found else "WARN", f"ffmpeg {'found' if found else 'not on PATH'}"))
    return results


def cmd_doctor(_: argparse.Namespace) -> int:
    print(f"render-agent {__version__} on {platform.system()} {platform.release()}")
    try:
        settings = load_config()
    except AgentError as err:
        _print_error(err)
        return 1
    results = doctor_checks(settings)
    for level, msg in results:
        _line(level, msg)
    failed = sum(1 for level, _ in results if level == "FAIL")
    print("Doctor: all checks passed." if not failed else f"Doctor: {failed} check(s) failed.")
    return 1 if failed else 0


def _run_job(job: Job, settings: Settings, dry_run: bool) -> RunResult:
    handler = attach_job_log(job.dir)
    try:
        result = run_job(RunContext(job=job, settings=settings, dry_run=dry_run))
    finally:
        detach_job_log(handler)
    print(f"Job {job.id}: {result.status}" + (f" at {result.stopped_at}" if result.stopped_at else ""))
    print(f"  {result.message}")
    print(f"  Folder: {job.dir}")
    return result


def _run(job: Job, settings: Settings, dry_run: bool) -> int:
    return 1 if _run_job(job, settings, dry_run).status == "FAILED" else 0


def _with_settings(fn: Callable[[argparse.Namespace, Settings], int]) -> Callable[[argparse.Namespace], int]:
    def wrapper(args: argparse.Namespace) -> int:
        try:
            settings = load_config()
            setup_agent_logging(settings.path("logs"), console=False)
            return fn(args, settings)
        except AgentError as err:
            _print_error(err)
            return 1

    return wrapper


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


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    sources = [Path(p) for p in args.paths]
    if args.batch:
        sources += batch_sources(Path(args.batch))
    if not sources:
        print("Nothing to run: give one or more PDFs or folders, or --batch <folder>.")
        return 1
    rows: list[tuple[str, str, str]] = []
    for src in sources:
        try:
            inputs = drop_inputs(src)
            prior = None if args.force else existing_job(settings.path("jobs"), inputs)
            if prior is not None:
                print(f"{src.name}: already processed in {prior} (use --force to run it again)")
                rows.append((src.name, "SKIPPED (duplicate)", str(prior)))
                continue
            job = new_job(settings.path("jobs"), inputs)
            logging.getLogger("render_agent").info("job %s created from %s", job.id, src.name)
            result = _run_job(job, settings, args.dry_run)
            rows.append((src.name, result.status, str(job.dir)))
        except AgentError as err:  # one bad drop never stops the others
            _print_error(err)
            rows.append((src.name, "FAILED", err.what_happened))
    if len(rows) > 1:
        print("\nBatch summary:")
        width = max(len(r[0]) for r in rows)
        for name, status, where in rows:
            print(f"  {name:{width}}  {status:20}  {where}")
    return 1 if any(status == "FAILED" for _, status, _ in rows) else 0


def cmd_resume(args: argparse.Namespace, settings: Settings) -> int:
    job = load_job(Path(args.job))
    return _run(job, settings, bool(job.state.get("dry_run", False)))


def cmd_approve(args: argparse.Namespace, settings: Settings) -> int:
    job = load_job(Path(args.job))
    if job.first_unfinished_stage() != "gate_approval":
        print(f"Job {job.id} is not waiting for approval (status {job.state['status']}).")
        return 1
    problems = gate.approve(job, gate.parse_choices(args.select), by="cli", dwellings=args.dwellings)
    if problems:
        print(f"Job {job.id}: not approved yet.")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"Job {job.id}: plan approved.")
    if args.no_resume:
        return 0
    return _run(job, settings, bool(job.state.get("dry_run", False)))


def cmd_review(args: argparse.Namespace, settings: Settings) -> int:
    """Shows the review list; --approve / --flag record decisions, then the job continues
    (flagged renders are regenerated, approved ones exported)."""
    from render_agent.pipeline.stages import s9_review

    job = load_job(Path(args.job))
    if job.first_unfinished_stage() != "s9_review":
        print(f"Job {job.id} is not waiting for review (status {job.state['status']}).")
        return 1
    if args.serve:
        return serve_review(job, settings)
    review = s9_review.load_review(job.dir)
    problems = []
    ids = list(review["renders"])
    for a in args.approve:
        targets = [r for r in ids if review["renders"][r]["status"] == "pending"] if a == "all" else [a]
        problems += [p for t in targets if (p := s9_review.decide(review, t, "approve", by="cli"))]
    for f in args.flag:
        rid, sep, note = f.partition("=")
        if not sep:
            problems.append(f"--flag needs id=note: {f}")
        elif p := s9_review.decide(review, rid.strip(), "flag", note, by="cli"):
            problems.append(p)
    s9_review.save_review(job.dir, review)
    for p in problems:
        print(f"  - {p}")
    if not args.approve and not args.flag:
        print(f"Job {job.id}: review list")
        print(s9_review.review_text(job))
        print('Approve: --approve <id> | --approve all   Flag: --flag <id>="what to change"')
        return 0
    if problems:
        return 1
    return _run(job, settings, bool(job.state.get("dry_run", False)))


def serve_review(job: Job, settings: Settings) -> int:
    """Runs the review page on 127.0.0.1 until Ctrl+C."""
    import webbrowser

    from render_agent.review.server import ReviewApp, make_server

    def continue_job() -> str:
        result = _run_job(load_job(job.dir), settings, bool(job.state.get("dry_run", False)))
        return f"{result.status}: {result.message}"

    app = ReviewApp(job.dir, continue_job)
    server = make_server(app, settings.review.host, settings.review.port)
    url = f"http://{settings.review.host}:{server.server_address[1]}/"
    print(f"Review page for {job.id}: {url}  (Ctrl+C to stop)")
    if settings.review.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _todo(name: str) -> Callable[[argparse.Namespace], int]:
    def run(_: argparse.Namespace) -> int:
        print(f"'{name}' is not implemented yet. See docs/MILESTONES.md.")
        return 2

    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="render-agent", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check the host").set_defaults(func=cmd_doctor)
    sub.add_parser("watch", help="watch the inbox").set_defaults(func=_todo("watch"))
    run = sub.add_parser("run", help="process PDFs or folders, one job each")
    run.add_argument("paths", nargs="*", help="PDF (with optional same-named .txt) or folder per job")
    run.add_argument("--batch", metavar="FOLDER", help="one job per PDF and per sub-folder in FOLDER")
    run.add_argument("--force", action="store_true", help="run even if the same PDFs were done before")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="no paid calls: offline placeholder LLM and mock image provider",
    )
    run.set_defaults(func=_with_settings(cmd_run))
    resume = sub.add_parser("resume", help="resume a job from its last finished stage")
    resume.add_argument("job")
    resume.set_defaults(func=_with_settings(cmd_resume))
    approve = sub.add_parser("approve", help="pass the approval gate and continue the job")
    approve.add_argument("job")
    approve.add_argument(
        "--select",
        action="append",
        default=[],
        metavar="ITEM=VALUE",
        help="material choice, e.g. --select roof_colour=Monument (repeatable)",
    )
    approve.add_argument(
        "--dwellings",
        default="all",
        metavar="all|N|N,M",
        help="multi-dwelling sites: render all dwellings (default), one, or several, e.g. 2 or 1,3",
    )
    approve.add_argument("--no-resume", action="store_true", help="record approval only")
    approve.set_defaults(func=_with_settings(cmd_approve))
    review = sub.add_parser("review", help="list renders for review, approve or flag them")
    review.add_argument("job")
    review.add_argument("--approve", action="append", default=[], metavar="ID|all")
    review.add_argument("--flag", action="append", default=[], metavar='ID="note"')
    review.add_argument("--serve", action="store_true", help="open the review page on 127.0.0.1")
    review.set_defaults(func=_with_settings(cmd_review))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
