import logging

from render_agent.logging_setup import attach_job_log, detach_job_log, setup_agent_logging


def test_keys_are_redacted_in_agent_and_job_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("MUAPI_API_KEY", "muapi-secret-value-42")
    setup_agent_logging(tmp_path / "logs", console=False)
    handler = attach_job_log(tmp_path / "job")
    log = logging.getLogger("render_agent.test")
    log.info("calling with key %s", "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX")
    log.info("header Authorization: Bearer abcdefghijklmnop1234 and muapi-secret-value-42")
    detach_job_log(handler)
    for path in (tmp_path / "logs" / "agent.log", tmp_path / "job" / "job.log"):
        text = path.read_text(encoding="utf-8")
        assert "calling with key" in text
        assert "sk-proj-ABCD" not in text
        assert "abcdefghijklmnop1234" not in text
        assert "muapi-secret-value-42" not in text
        assert "REDACTED" in text


def test_setup_is_idempotent(tmp_path):
    setup_agent_logging(tmp_path, console=False)
    setup_agent_logging(tmp_path, console=False)
    agent = [h for h in logging.getLogger("render_agent").handlers if getattr(h, "_agent_handler", False)]
    assert len(agent) == 1
