"""Launcher faults (SPEC section 9 System rows) and the three roots (config.py)."""

from __future__ import annotations

import sys
import types

import pytest
import yaml

from render_agent.app import launcher
from render_agent.config import load_config, working_root
from render_agent.errors import PermanentError
from render_agent.pipeline.runner import job_lock


@pytest.fixture
def settings(install):
    return load_config(install)


def test_second_app_instance_brings_the_window_forward_and_exits(settings, monkeypatch):
    focused = []
    monkeypatch.setattr(launcher, "_focus_existing", lambda root: focused.append(root) or True)
    monkeypatch.setattr(launcher, "_run", lambda s, b: pytest.fail("a second app must not start"))
    lock_dir = settings.work_root / "app_lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with job_lock(lock_dir):  # the first instance holds the lock
        assert launcher.run(settings) == 0
    assert focused == [settings.work_root]


def test_port_in_use_gives_a_plain_message(settings, monkeypatch):
    shown = []
    monkeypatch.setattr(launcher, "message_box", shown.append)

    def no_port():
        raise OSError("no free port")

    monkeypatch.setattr(launcher, "free_port", no_port)
    assert launcher.run(settings) == 1
    assert shown and "Restart Windows" in shown[0]


def test_webview2_missing_gives_a_plain_message(settings, monkeypatch):
    shown = []
    monkeypatch.setattr(launcher, "message_box", shown.append)

    class FakeServer:
        started = True
        should_exit = False

        def __init__(self, config):
            pass

        def run(self):
            pass

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: object()

    def start(**k):
        raise RuntimeError("WebView2 runtime not found")

    fake.start = start
    monkeypatch.setitem(sys.modules, "webview", fake)
    assert launcher.run(settings) == 1
    assert shown and "WebView2" in shown[0]
    assert not (settings.work_root / launcher.PORT_FILE).exists()


def test_working_root_is_separate_when_installed(tmp_path, monkeypatch):
    monkeypatch.delenv("RENDER_AGENT_WORK", raising=False)
    monkeypatch.setenv("RENDER_AGENT_INSTALLED", "1")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert working_root(tmp_path / "Program") == tmp_path / "Local" / "RenderAgent"
    monkeypatch.delenv("RENDER_AGENT_INSTALLED")
    assert working_root(tmp_path / "Program") == tmp_path / "Program"  # dev mode: repo layout


def test_installed_layout_reads_rules_from_install_and_writes_to_work(install, tmp_path):
    work = tmp_path / "work"
    (install / "config.example.yaml").write_text((install / "config.yaml").read_text())
    (install / "config.yaml").unlink()
    s = load_config(install, work)  # first launch copies config.example.yaml
    assert (work / "config.yaml").is_file()
    assert s.path("knowledge") == install.resolve() / "private" / "knowledge"
    assert s.path("jobs") == work.resolve() / "jobs" and s.path("feedback") == work.resolve() / "feedback"


def test_image_provider_must_be_enabled(install):
    cfg = yaml.safe_load((install / "config.yaml").read_text())
    cfg["images"]["provider"] = "muapi"
    (install / "config.yaml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(PermanentError) as exc:
        load_config(install)
    assert "enabled_providers" in exc.value.what_happened
