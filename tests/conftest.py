"""Shared fixtures: a throwaway install root with the real prompts, schemas and knowledge."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pymupdf
import pytest

from render_agent.config import load_config

ROOT = Path(__file__).resolve().parents[1]
os.environ["RENDER_AGENT_NO_KEYRING"] = "1"  # never read a real key from Credential Manager
FIXTURE_01 = ROOT / "private" / "fixtures" / "fixture_01"


def fixture_pdf(fixture_dir: Path) -> Path | None:
    """A fixture's drawing PDF: input/ (M5.5 layout), else the fixture root (older layout)."""
    return next(fixture_dir.glob("input/*.pdf"), None) or next(fixture_dir.glob("*.pdf"), None)


@pytest.fixture
def install(tmp_path, monkeypatch):
    """An install root without keys (no .env, key env vars cleared)."""
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MUAPI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    shutil.copy(ROOT / "config.example.yaml", tmp_path / "config.yaml")
    for d in ("private/knowledge", "private/prompts", "schemas"):
        shutil.copytree(ROOT / d, tmp_path / d)
    return tmp_path


@pytest.fixture
def install_settings(install):
    return load_config(install)


def make_pdf(path: Path, pages: list[str | None], password: str | None = None) -> Path:
    """One page per entry: text is written on it; None makes an image-only (scanned) page."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text is None:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
            pix.clear_with(200)
            page.insert_image(page.rect, pixmap=pix)
        else:
            page.insert_text((72, 72), text)
    path.parent.mkdir(parents=True, exist_ok=True)
    if password:
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    else:
        doc.save(path)
    return path


PRIVATE = [ROOT / "private" / "prompts", ROOT / "private" / "knowledge", FIXTURE_01]


def pytest_configure(config):
    """The prompts, knowledge rules and drawing fixtures are private and not in the repository."""
    missing = [p.relative_to(ROOT).as_posix() for p in PRIVATE if not p.is_dir()]
    if missing:
        pytest.exit(
            f"Private files missing: {', '.join(missing)}. They are not in the repository (see "
            "redacted/README.md). Restore them from the private store to run the tests.",
            returncode=4,
        )


def pytest_collection_modifyitems(config, items):
    """Tests marked `paid` make real API calls; they run only when RUN_PAID_TESTS=1."""
    if os.environ.get("RUN_PAID_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="paid API test; set RUN_PAID_TESTS=1 to run")
    for item in items:
        if "paid" in item.keywords:
            item.add_marker(skip)
