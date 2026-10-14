"""IP stays out of the repository: .gitignore covers it, and redacted/ is an up-to-date,
structure-only copy of the private prompts and knowledge files."""

from __future__ import annotations

import importlib.util

from tests.conftest import ROOT

spec = importlib.util.spec_from_file_location("make_redacted", ROOT / "scripts" / "make_redacted.py")
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)


def test_gitignore_and_hook_keep_private_files_out():
    lines = {ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    for entry in ("/tests/fixtures/*/", "*.pdf", ".env", "config.yaml", "jobs/", "logs/"):
        assert entry in lines, f".gitignore is missing {entry}"
    hook = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert "^private/" in hook and r"\.pdf$" in hook


def test_redacted_copy_is_current():
    expected = {}
    for name in mr.PRIVATE:
        for src in (mr.SRC / name).rglob("*"):
            if src.is_file():
                expected[src.relative_to(mr.SRC).as_posix()] = mr.redact_file(src)
    out = ROOT / "redacted"
    actual = {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
        for p in out.rglob("*")
        if p.is_file() and p.name != "README.md" or (p.is_file() and p.parent != out)
    }
    stale = sorted(k for k in expected if actual.get(k) != expected[k])
    extra = sorted(set(actual) - set(expected))
    assert not stale and not extra, f"run `python scripts/make_redacted.py` (stale {stale}, extra {extra})"


def test_redacted_copy_holds_no_wording():
    for p in (ROOT / "redacted").rglob("*"):
        if not p.is_file() or p == ROOT / "redacted" / "README.md":
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip().removeprefix("<!--").removesuffix("-->").strip().removeprefix("- ").strip()
            if s.startswith("Redacted copy") or s.startswith("{{") or s in ("[redacted]", "<redacted>", ""):
                continue
            if ":" in s and s.split(":", 1)[1].strip() in ("<redacted>", "", "true", "false", "[]", "null"):
                continue  # a YAML key
            assert line.lstrip().startswith("#"), f"{p.name}: unexpected text kept: {line!r}"
