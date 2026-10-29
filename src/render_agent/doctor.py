"""Health checks: shared by `doctor` (CLI) and the app's Help screen."""

from __future__ import annotations

import platform
import shutil
import sys
import uuid
from pathlib import Path

from render_agent.config import Settings

MIN_PYTHON = (3, 12)
WRITABLE_PATHS = ("jobs", "failed", "logs", "feedback")
READABLE_PATHS = ("knowledge", "prompts", "schemas")


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".doctor-{uuid.uuid4().hex}.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _base_checks(settings: Settings) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    py_ok = sys.version_info[:2] >= MIN_PYTHON
    results.append(("OK" if py_ok else "FAIL", f"Python {platform.python_version()} (need 3.12+)"))
    results.append(("OK", f"config: {settings.config_file}"))
    for var in settings.required_key_vars():
        present = settings.secret(var) is not None
        state = "set" if present else "missing (add it in Settings, or .env for developers)"
        results.append(("OK" if present else "FAIL", f"{var} {state}"))
    for name in WRITABLE_PATHS:
        folder = settings.path(name)
        ok = _writable(folder)
        results.append(("OK" if ok else "FAIL", f"{name}/ {'writable' if ok else 'not writable'}: {folder}"))
    for name in READABLE_PATHS:
        folder = settings.path(name)
        ok = folder.is_dir()
        results.append(("OK" if ok else "FAIL", f"{name}/ {'found' if ok else 'missing'}: {folder}"))
    free_gb = shutil.disk_usage(settings.work_root).free / 1e9
    min_gb = settings.watcher.min_free_disk_gb
    results.append(("OK" if free_gb >= min_gb else "FAIL", f"free disk {free_gb:.1f} GB (minimum {min_gb})"))
    from render_agent.images.muapi_cli import doctor_check

    results.append(doctor_check())
    found = shutil.which("ffmpeg")
    results.append(("OK" if found else "WARN", f"ffmpeg {'found' if found else 'not on PATH'}"))
    return results


def doctor_checks(settings: Settings, browser: bool = True) -> list[tuple[str, str]]:
    """(level, message) pairs; level is OK, WARN or FAIL. Plan visuals checks carry their fix."""
    from render_agent.plan.health import plan_checks

    results = _base_checks(settings)
    for level, msg, fix in plan_checks(settings, browser=browser):
        results.append((level, f"{msg}. Fix: {fix}" if fix else msg))
    return results


def plain_checks(settings: Settings, browser: bool = True) -> list[dict[str, str]]:
    """Doctor results for designers: OK / Check this / Problem, without hidden providers. Plan visuals
    checks carry a separate `fix` line."""
    from render_agent.plan.health import plan_checks

    words = {"OK": "OK", "WARN": "Check this", "FAIL": "Problem"}
    out = []
    for level, msg in _base_checks(settings):
        name = msg.split()[0].lower()
        if name == "muapi" and "muapi" not in settings.images.enabled_providers:
            continue
        if name == "ffmpeg":
            continue  # phase 2 (video); nothing a designer can act on
        if name.endswith("_api_key"):
            continue  # the key lines below say saved / not saved, never the key
        out.append({"level": words[level], "text": msg, "fix": ""})
    for level, msg, fix in plan_checks(settings, browser=browser):
        out.append({"level": words[level], "text": msg, "fix": fix})
    return out
