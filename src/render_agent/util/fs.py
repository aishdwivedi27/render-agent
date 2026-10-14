"""File helpers. Every job artifact is written atomically (temp file, then rename)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(Path(tmp), path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


REPLACE_TRIES = 20  # x 0.1 s: Windows refuses a rename while antivirus or a reader holds the file


def _replace(tmp: Path, path: Path) -> None:
    for attempt in range(REPLACE_TRIES):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.1)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, ensure_ascii=False))


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def safe_name(text: str, max_len: int = 80) -> str:
    """A Windows-safe file or folder name."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return cleaned[:max_len] or "unnamed"


def read_text(path: Path) -> str:
    """read_text that waits out a brief Windows lock (antivirus, a concurrent rename)."""
    for attempt in range(REPLACE_TRIES):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.1)
    raise AssertionError("unreachable")
