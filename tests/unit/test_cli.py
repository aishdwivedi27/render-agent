import shutil
from pathlib import Path

import pymupdf

from render_agent.cli import doctor_checks, main
from render_agent.config import load_config

ROOT = Path(__file__).resolve().parents[2]


def _install(tmp_path, monkeypatch, key="sk-test-0123456789abcdef"):
    shutil.copy(ROOT / "config.example.yaml", tmp_path / "config.yaml")
    for d in ("private/knowledge", "private/prompts", "schemas"):
        shutil.copytree(ROOT / d, tmp_path / d)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={key}\n", encoding="utf-8")
    monkeypatch.setenv("RENDER_AGENT_HOME", str(tmp_path))


def test_doctor_passes_with_key(tmp_path, monkeypatch, capsys):
    _install(tmp_path, monkeypatch)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY set" in out and "sk-test" not in out


def test_doctor_fails_without_key(tmp_path, monkeypatch, capsys):
    _install(tmp_path, monkeypatch, key="")
    assert main(["doctor"]) == 1
    assert "OPENAI_API_KEY missing" in capsys.readouterr().out


def test_doctor_names_bad_setting(tmp_path, monkeypatch, capsys):
    _install(tmp_path, monkeypatch)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("poll_seconds", "pol_seconds"), encoding="utf-8")
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "'watcher.pol_seconds': unknown setting" in out
    assert "'watcher.poll_seconds'" in out


def test_external_tools_only_warn(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda _: None)
    levels = {msg.split()[0]: lvl for lvl, msg in doctor_checks(load_config(tmp_path))}
    assert levels["muapi"] == "WARN" and levels["ffmpeg"] == "WARN"


def test_run_dry_run_then_resume(tmp_path, monkeypatch, capsys):
    _install(tmp_path, monkeypatch)
    pdf = tmp_path / "set.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "GROUND FLOOR PLAN  SCALE 1:100  SHEET 1 OF 1")
    doc.save(pdf)
    assert main(["run", str(pdf), "--dry-run"]) == 0
    jobs = list((tmp_path / "jobs").iterdir())
    assert len(jobs) == 1
    assert (jobs[0] / "state.json").is_file() and (jobs[0] / "job.log").is_file()
    assert main(["resume", str(jobs[0])]) == 0
    out = capsys.readouterr().out
    assert "AWAITING_APPROVAL" in out  # s1-s5 ran offline
    assert (jobs[0] / "house_specification.json").is_file()
    assert not (jobs[0] / "llm").exists(), "dry run must not use a real LLM"
