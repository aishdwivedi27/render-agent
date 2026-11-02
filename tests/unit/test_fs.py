from pathlib import Path

import pytest

from render_agent.util import fs
from render_agent.util.fs import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    safe_name,
    sha256_file,
)


def test_atomic_write_json_roundtrip(tmp_path):
    target = tmp_path / "a" / "state.json"
    atomic_write_json(target, {"status": "RENDERING"})
    assert target.read_text(encoding="utf-8").strip().startswith("{")
    assert not list(target.parent.glob("*.tmp"))
    assert len(sha256_file(target)) == 64


def test_atomic_write_bytes_and_text(tmp_path):
    atomic_write_bytes(tmp_path / "b.bin", b"\x00\x01")
    atomic_write_text(tmp_path / "t.txt", "café")
    assert (tmp_path / "b.bin").read_bytes() == b"\x00\x01"
    assert (tmp_path / "t.txt").read_text(encoding="utf-8") == "café"


def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "f.txt"
    atomic_write_text(target, "old")

    def boom(self, other):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        fs.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("*.tmp"))


def test_safe_name_is_windows_safe():
    assert safe_name('Dwelling 1: "front"?') == "Dwelling 1_ _front__"


def test_safe_name_never_empty_and_truncates():
    assert safe_name("...") == "unnamed"
    assert len(safe_name("x" * 200)) == 80


def test_atomic_write_retries_while_windows_holds_the_file(tmp_path, monkeypatch):
    from pathlib import Path

    from render_agent.util import fs

    real = Path.replace
    calls = {"n": 0}

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(32, "being used by another process")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    fs.atomic_write_text(tmp_path / "a.json", "{}")
    assert (tmp_path / "a.json").read_text() == "{}" and calls["n"] == 3
    assert not list(tmp_path.glob(".*.tmp"))


def test_safe_name_truncation_never_ends_in_space_or_dot():
    assert safe_name("Plan - Lot 1 - Stage. Two", 13) == "Plan - Lot 1"
    assert safe_name("abc . def", 5) == "abc"


def test_read_is_tried_again_while_the_file_is_being_replaced(tmp_path, monkeypatch):
    """Windows refuses an open for a moment while another thread renames over the file (the job worker
    saving state.json while the app reads it)."""
    path = tmp_path / "state.json"
    atomic_write_json(path, {"status": "RUNNING"})
    real = Path.read_text
    refused = []

    def flaky(self, *a, **kw):
        if self == path and len(refused) < 2:
            refused.append(1)
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    assert fs.read_text(path) == real(path, encoding="utf-8") and len(refused) == 2
    from render_agent.pipeline.job import load_job, new_job
    from tests.conftest import make_pdf

    job = new_job(tmp_path / "jobs", [make_pdf(tmp_path / "in" / "a.pdf", ["FLOOR PLAN"])])
    refused.clear()
    path = job.state_file
    assert load_job(job.dir).id == job.id and len(refused) == 2  # the app's job read no longer fails


def test_read_still_fails_when_the_file_stays_locked(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    path.write_text("{}")
    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **kw: (_ for _ in ()).throw(PermissionError(13, "x"))
    )
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        fs.read_text(path)
