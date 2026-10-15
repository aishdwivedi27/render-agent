"""Feedback store: every flag and job correction, all jobs, in feedback/feedback.jsonl.

It is read only by people (and scripts/review_feedback.py); the app never turns it into rules.
Export makes a zip of the text and small thumbnails, with no keys and no drawings.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from render_agent.config import Settings
from render_agent.util.fs import atomic_write_bytes

FEEDBACK = "feedback.jsonl"
THUMB_PX = 480
log = logging.getLogger("render_agent.feedback")


def store(settings: Settings) -> Path:
    return settings.path("feedback") / FEEDBACK


def record(settings: Settings, entry: dict[str, Any]) -> str | None:
    """Appends one line. Returns a plain warning when the store is not writable (the correction
    still applies to the job, which carries on)."""
    line = {"time": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
    path = store(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("feedback store not writable: %s", exc)
        return (
            "Your correction was applied to this job, but it could not be saved to the feedback "
            f"list ({exc.strerror or exc}). Nothing else is affected."
        )
    return None


def _entries(settings: Settings) -> list[dict[str, Any]]:
    path = store(settings)
    if not path.is_file():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def _thumb(src: Path) -> bytes:
    with Image.open(src) as opened:
        im = opened.convert("RGB")
    im.thumbnail((THUMB_PX, THUMB_PX))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def export(settings: Settings, dest_dir: Path, now: datetime | None = None) -> Path:
    """Writes 'RenderAgent feedback <date>.zip' to dest_dir atomically (no partial zip on failure)."""
    entries = _entries(settings)
    jobs_dir = settings.path("jobs")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(FEEDBACK, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
        for e in entries:
            image = e.get("image")
            if not image or not e.get("job_id"):
                continue
            src = (jobs_dir / e["job_id"] / image).resolve()
            if not src.is_relative_to((jobs_dir / e["job_id"] / "output").resolve()) or not src.is_file():
                continue  # only rendered images, never drawings or other job files
            name = f"thumbnails/{e['job_id']}/{Path(image).stem}.jpg"
            if name not in zf.namelist():
                zf.writestr(name, _thumb(src))
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H%M")
    out = dest_dir / f"RenderAgent feedback {stamp}.zip"
    atomic_write_bytes(out, buf.getvalue())
    return out
