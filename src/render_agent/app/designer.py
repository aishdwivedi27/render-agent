"""Designer folders on the Desktop: Input, Output, History (docs/SPEC.md section 8).

There is no watcher: a job starts only when the designer presses Start. Start copies the inputs
into the job, verifies every copy by SHA-256, then moves the originals to a dated History folder
with started.txt. If the copy or the check fails, the originals stay in Input. A job's working
state never depends on Output or History: moving or deleting them never breaks a job.
"""

from __future__ import annotations

import getpass
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from render_agent.config import Settings
from render_agent.errors import InputError
from render_agent.pipeline.job import Job, load_job, new_job
from render_agent.pipeline.start import drop_inputs, existing_job
from render_agent.util.fs import atomic_write_bytes, atomic_write_text, safe_name, sha256_file

log = logging.getLogger("render_agent.designer")
SUBFOLDERS = ("Input", "Output", "History")
OPTIONS_FILE = "start_options.txt"
PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp", ".partial", ".download")
SIDE_SUFFIXES = (".txt", ".selections.yaml")


@dataclass
class Folders:
    root: Path
    input: Path
    output: Path
    history: Path
    desktop: Path

    @property
    def on_onedrive(self) -> bool:
        return "onedrive" in str(self.desktop).lower()


@dataclass
class InputItem:
    name: str
    path: Path
    kind: str  # "pdf" | "pdf+txt" | "folder"
    files: list[Path] = field(default_factory=list)
    size_mb: float = 0.0
    startable: bool = True
    reason: str = ""


@dataclass
class Started:
    job: Job
    history: Path | None
    output: Path
    warnings: list[str]


def known_desktop() -> Path:
    """The Desktop known folder (follows OneDrive or a redirected profile); home/Desktop elsewhere."""
    if sys.platform == "win32":
        import ctypes
        import uuid
        from ctypes import wintypes

        guid = uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")  # FOLDERID_Desktop
        buf = ctypes.c_char_p(guid.bytes_le)
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.c_char_p,
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        if shell32.SHGetKnownFolderPath(buf, 0, None, ctypes.byref(out)) == 0 and out.value:
            path = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
            return path
    return Path.home() / "Desktop"


def folders(settings: Settings, create: bool = True) -> Folders:
    desktop = Path(settings.designer.desktop) if settings.designer.desktop else known_desktop()
    root = desktop / settings.designer.folder
    f = Folders(root, root / "Input", root / "Output", root / "History", desktop)
    if create:
        for d in (f.input, f.output, f.history):
            d.mkdir(parents=True, exist_ok=True)
    return f


# --- Input ---------------------------------------------------------------------------------------
def _stable(paths: list[Path], stable_seconds: float, now: float) -> str:
    """'' when every file is finished and readable, else the plain reason."""
    for p in paths:
        if p.name.startswith("~$") or p.name.lower().endswith(PARTIAL_SUFFIXES):
            return "still copying"
        try:
            st = p.stat()
            if now - st.st_mtime < stable_seconds:
                return "still copying"
            with p.open("rb") as fh:
                fh.read(1)
        except OSError:
            return "in use by another program"
    return ""


def list_inputs(settings: Settings, now: float | None = None) -> list[InputItem]:
    """What is waiting in Input: PDFs (with a same-named .txt / .selections.yaml) and sub-folders."""
    f = folders(settings)
    now = time.time() if now is None else now
    items = []
    for entry in sorted(f.input.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            files = drop_inputs(entry)
            kind = "folder"
            pdfs = [p for p in files if p.suffix.lower() == ".pdf"]
            if not pdfs:
                items.append(InputItem(entry.name, entry, kind, files, 0, False, "no PDF in this folder"))
                continue
        elif entry.suffix.lower() == ".pdf":
            files = drop_inputs(entry)
            kind = "pdf+txt" if len(files) > 1 else "pdf"
        elif entry.name.lower().endswith(PARTIAL_SUFFIXES):
            items.append(InputItem(entry.stem, entry, "pdf", [entry], 0, False, "still copying"))
            continue
        else:
            continue  # side files travel with their PDF; anything else is ignored
        size = sum(p.stat().st_size for p in files if p.is_file()) / 1e6
        reason = _stable(files, settings.watcher.stable_seconds, now)
        name = entry.name if entry.is_dir() else entry.stem
        items.append(InputItem(name, entry, kind, files, round(size, 1), not reason, reason))
    return items


def find_item(settings: Settings, name: str) -> InputItem:
    for item in list_inputs(settings):
        if item.name == name:
            return item
    raise InputError(
        code="INPUT_GONE",
        what_happened=f"'{name}' is no longer in the Input folder.",
        what_to_do="Press Refresh to see what is waiting in Input.",
    )


def save_upload(settings: Settings, filename: str, data: bytes) -> Path:
    """A PDF dragged onto the window goes into Input; it is then started like any other item."""
    name = safe_name(Path(filename).name, 150)
    if not name.lower().endswith(".pdf"):
        raise InputError(
            code="NOT_A_PDF",
            what_happened=f"'{filename}' is not a PDF.",
            what_to_do="Drop the PDF exported from Revit.",
        )
    dest = folders(settings).input / name
    atomic_write_bytes(dest, data)
    done = time.time() - settings.watcher.stable_seconds - 1
    os.utime(dest, (done, done))  # written in one piece by the app: startable at once
    return dest


# --- Start ---------------------------------------------------------------------------------------
def options_text(options: dict[str, Any]) -> str:
    """Start options as the documented @directives (the only text that changes behaviour)."""
    lines = []
    for key in ("night", "streetscape", "dry_run"):
        if options.get(key) is not None:
            lines.append(f"@{key}={'true' if options[key] else 'false'}")
    for key in ("provider", "llm"):
        if options.get(key):
            lines.append(f"@{key}={options[key]}")
    for key in ("dwellings", "views"):
        if options.get(key):
            lines.append(f"@{key}={','.join(options[key])}")
    if options.get("max_images"):
        lines.append(f"@max_images={int(options['max_images'])}")
    return "\n".join(lines)


def _unique(path: Path, suffix_fmt: str) -> Path:
    if not path.exists():
        return path
    n = 2
    while (cand := path.with_name(suffix_fmt.format(name=path.name, n=n))).exists():
        n += 1
    return cand


def history_folder(f: Folders, input_name: str, when: datetime) -> Path:
    base = f.history / safe_name(f"{when:%Y-%m-%d %H%M} - {input_name}", 120)
    return _unique(base, "{name}-{n}")


def output_folder(f: Folders, input_name: str, when: datetime) -> Path:
    base = f.output / safe_name(input_name, 100)
    if not base.exists():
        return base
    dated = base.with_name(f"{base.name} ({when:%Y-%m-%d})")
    if not dated.exists():
        return dated
    n = 2
    while (cand := base.with_name(f"{base.name} ({when:%Y-%m-%d}-{n})")).exists():
        n += 1
    return cand


def started_text(when: datetime, output: Path, job_id: str) -> str:
    return (
        f"Started: {when:%A %d %B %Y at %H:%M}\n"
        f"Started by: {getpass.getuser()}\n"
        f"Renders will be saved in: {output}\n"
        f"Job ID: {job_id}\n"
    )


def start_item(
    settings: Settings,
    item: InputItem,
    options: dict[str, Any] | None = None,
    start_anyway: bool = False,
    now: datetime | None = None,
) -> Started:
    """Copy, verify, move the originals to History, write started.txt. Returns the new job.

    Raises InputError (originals untouched) when the item is not ready, is a duplicate, or the
    copy cannot be verified. A duplicate's error carries the existing job id in `duplicate_of`.
    """
    when = now or datetime.now()
    if not item.startable:
        raise InputError(
            code="INPUT_NOT_READY",
            what_happened=f"'{item.name}' is {item.reason}.",
            what_to_do="Wait until the file has finished copying, then press Refresh and Start again.",
        )
    jobs_dir = settings.path("jobs")
    if not start_anyway and (prior := existing_job(jobs_dir, item.files)) is not None:
        err = InputError(
            code="ALREADY_STARTED",
            what_happened=f"'{item.name}' was started before (the same PDF).",
            what_to_do="Open the existing job, or choose Start anyway to make a new one.",
        )
        err.duplicate_of = prior.name  # type: ignore[attr-defined]
        raise err
    originals = {p.name: sha256_file(p) for p in item.files}
    job = new_job(jobs_dir, item.files, now=when)
    try:
        for entry in job.state["inputs"]:
            src_name = next(n for n in originals if safe_name(n, 150) == entry["name"])
            if entry["sha256"] != originals[src_name]:
                raise OSError(f"copy of {src_name} does not match the original")
        if text := options_text(options or {}):
            atomic_write_text(job.dir / "input" / OPTIONS_FILE, text + "\n")
            job.state["inputs"].append(
                {"name": OPTIONS_FILE, "sha256": sha256_file(job.dir / "input" / OPTIONS_FILE)}
            )
    except (OSError, StopIteration) as exc:
        shutil.rmtree(job.dir, ignore_errors=True)
        raise InputError(
            code="COPY_NOT_VERIFIED",
            what_happened=f"'{item.name}' could not be copied safely, so nothing was started.",
            likely_cause=f"The disk is full or the file changed while it was copied ({exc}).",
            what_to_do="Your file is still in Input. Free some disk space if needed and press Start again.",
        ) from exc

    f = folders(settings)
    output = output_folder(f, item.name, when)
    history: Path | None = history_folder(f, item.name, when)
    warnings = []
    try:
        assert history is not None
        history.mkdir(parents=True)
        shutil.move(str(item.path), str(history / item.path.name))
        for side in item.files:  # a PDF's same-named .txt / .selections.yaml
            if side.exists():
                shutil.move(str(side), str(history / side.name))
        atomic_write_text(history / "started.txt", started_text(when, output, job.id))
    except OSError as exc:
        log.warning("job %s: could not move the originals to History: %s", job.id, exc)
        warnings.append(
            f"The job started, but '{item.name}' could not be moved to History ({exc.strerror or exc}). "
            "Move it out of Input yourself so it is not started twice."
        )
        if history is not None and not any(history.iterdir()):
            history.rmdir()
            history = None
    job.state["designer"] = {
        "input_name": item.name,
        "history": str(history) if history else None,
        "output": str(output),
        "started": when.isoformat(timespec="seconds"),
        "warnings": warnings,
    }
    job.save()
    log.info("job %s started from %s", job.id, item.name)
    return Started(load_job(job.dir), history, output, warnings)


# --- Output --------------------------------------------------------------------------------------
def _copy_if_changed(src: Path, dest: Path) -> None:
    data = src.read_bytes()
    if dest.is_file() and dest.stat().st_size == len(data) and dest.read_bytes() == data:
        return
    atomic_write_bytes(dest, data)


def publish(job: Job) -> str | None:
    """Copies the job's output folders to its Output folder, same layout (decision 20 Oct 2026):
    <Dwelling N | Common>/Draft (every version, DRAFT overlay), Draft/draft-final (approved version,
    overlay) and FINAL APPROVED (approved, no overlay), plus report.html. The job's hidden clean
    copies are never copied. Returns a plain problem, or None."""
    info = job.state.get("designer") or {}
    if not info.get("output"):
        return None  # a CLI job: no designer folders
    out = Path(info["output"])
    src = job.dir / "output"
    try:
        for png in sorted(src.rglob("*.png")):
            rel = png.relative_to(src)
            if rel.parts[0] == "clean":
                continue
            if len(rel.parts) == 1:  # drafts of jobs made before 20 Oct 2026 lived in output/ itself
                rel = Path("Draft") / rel
            _copy_if_changed(png, out / rel)
        report = job.dir / "report.html"
        if report.is_file():  # its thumbnails point at the job's output/; here that folder is the root
            text = report.read_text(encoding="utf-8").replace('"output/', '"')
            if not (out / "report.html").is_file() or (out / "report.html").read_text("utf-8") != text:
                atomic_write_text(out / "report.html", text)
    except OSError as exc:
        log.warning("job %s: Output not writable: %s", job.id, exc)
        return (
            f"The renders could not be copied to {out} ({exc.strerror or exc}). They are safe in the "
            "job. Close any program using that folder (or pause OneDrive), then press Retry."
        )
    return None
