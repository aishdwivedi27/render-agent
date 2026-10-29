"""PV0 through the app API: Settings defaults, Help checks, diagnostics, the 'coming' placeholder and a
practice run that still reaches Choose views (docs/PLAN_VISUALS_SPEC.md sections 1, 12, 13 and 15)."""

from __future__ import annotations

import json

from render_agent.app import keys
from render_agent.pipeline.job import PLAN_STAGES, load_job
from render_agent.plan import health
from tests.integration.test_m5_app import TOKEN_HEADER, _fixture_pdf, _start, env  # noqa: F401


def test_settings_shows_plain_defaults_and_every_key(env):  # noqa: F811
    s = env.client.get("/api/settings").json()
    assert s["defaults"] == {
        "readout": "Claude",
        "prompt_writers": "Claude + OpenAI",
        "drawing_check": "OpenAI",
        "images": "OpenAI",
        "images_also": ["MuAPI"],
    }
    providers = {p["name"]: p for p in s["providers"]}
    assert list(providers) == ["anthropic", "muapi", "openai"]
    assert providers["anthropic"]["used_for"] == "plan readout and render prompts"
    assert providers["muapi"]["used_for"] == "making images when chosen"
    assert providers["openai"]["has_key"] and not providers["muapi"]["has_key"]
    assert "sk-test-not-real" not in json.dumps(s)


def test_help_lists_new_checks_with_fix_text(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        health, "_webgl_probe", lambda: (False, "the headless browser (Playwright) is not installed")
    )
    checks = env.client.get("/api/help/doctor").json()
    by_text = {c["text"]: c for c in checks}
    for text in ("Anthropic (Claude) key not saved", "OpenAI key saved", "MuAPI key not saved"):
        assert text in by_text
    pricing = next(c for c in checks if c["text"].startswith("Pricing not set"))
    assert pricing["level"] == "Check this" and "pricing" in pricing["fix"]
    assert any(c["text"].startswith("Wet-area tile contrast") and c["level"] == "OK" for c in checks)
    assert any(c["text"].startswith("Agent memory: 3 approved") for c in checks)
    browser = next(c for c in checks if "headless browser" in c["text"])
    assert "playwright install chromium" in browser["fix"]
    engine = next(c for c in checks if "Plan engine" in c["text"])
    assert engine["level"] in ("OK", "Problem") and (engine["level"] == "OK" or engine["fix"])
    not_ok = [c for c in checks if c["level"] != "OK" and "key" in c["text"]]
    assert not_ok and all("Settings" in c["fix"] for c in not_ok)
    assert "sk-test-not-real" not in json.dumps(checks)


def test_check_keys_now_is_free_and_never_echoes_a_key(env, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setitem(keys.VALIDATORS, "openai", lambda k: calls.append("openai"))
    assert env.client.post("/api/help/check-keys").status_code in (401, 403)  # needs the session token
    r = env.post("/api/help/check-keys")
    assert r.status_code == 200 and calls == ["openai"]  # only saved keys are tested
    out = {k["label"]: k for k in r.json()}
    assert out["OpenAI"]["ok"] and not out["Anthropic (Claude)"]["ok"]
    assert "Add it in Settings" in out["MuAPI"]["message"]
    assert "sk-test-not-real" not in r.text


def test_practice_run_reaches_choose_views_and_diagnostics_show_skipped_stages(env):  # noqa: F811
    env.auto_views = False
    job_id = _start(env, _fixture_pdf(env), dry_run=True)
    env.drain()
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "AWAITING_VIEWS" and job["screen"] == "views" and job["coming"] == ""
    state = load_job(env.settings.path("jobs") / job_id).state
    later = [n for n in PLAN_STAGES if n != "gate_plan_start"]  # Exterior renders only (env.drain)
    assert state["stages"]["gate_plan_start"]["status"] == "done"
    assert all(state["stages"][n]["status"] == "skipped" for n in later)
    text = env.client.get("/api/help/diagnostics").json()["text"]
    assert "    skipped: not enabled yet: " in text
    assert "    skipped: Exterior renders only (no plans): p1_trace, gate_plan_check" in text


def test_plan_status_shows_the_coming_placeholder(env):  # noqa: F811
    env.auto_views = False
    job_id = _start(env, _fixture_pdf(env), dry_run=True)
    env.drain()
    job = load_job(env.settings.path("jobs") / job_id)
    job.set_status("AWAITING_ELEVATION_CHOICE")
    out = env.client.get(f"/api/jobs/{job_id}").json()
    assert out["screen"] == "coming" and out["coming"] == "This screen is coming in PV4."
    assert out["status_text"] == "Do the elevation readout, or skip it?" and out["can_cancel"]
