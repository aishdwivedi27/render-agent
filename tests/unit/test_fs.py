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
