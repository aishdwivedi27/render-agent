"""PV1 through the app API: Start the plan check after the drawings are read, the readout model or
Exterior renders only, and the placeholder for the plan check (docs/PLAN_VISUALS_SPEC.md section 2)."""

from __future__ import annotations

from render_agent.pipeline import plan_start
from render_agent.pipeline.job import load_job
from tests.conftest import make_pdf
from tests.integration.test_m5_app import _fixture_pdf, _start, env  # noqa: F401


def _at_plan_start(env, pdf=None) -> str:  # noqa: F811
    env.auto_plan_start = False
    env.auto_views = False
    job_id = _start(env, pdf or _fixture_pdf(env), dry_run=True)
    env.drain()
    return job_id


def test_practice_run_stops_at_start_the_plan_check_with_claude(env):  # noqa: F811
    job_id = _at_plan_start(env)
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"], job["status_text"]) == (
        "AWAITING_PLAN_START",
        "plan_start",
        "Start the plan check",
    )
    assert (job["step"], job["step_name"], job["readout"], job["coming"]) == (2, "Plans", "", "")
    r = env.client.get(f"/api/jobs/{job_id}/plan-start").json()
    assert (r["readout_llm"], r["plans_mode"]) == ("claude", "plans")
    assert "about double the reading cost" in r["info"]
    assert [(o["id"], o["available"], o["cost"]) for o in r["options"]] == [
        ("claude", True, "price not set"),
        ("openai", True, "price not set"),
        ("both", True, "price not set"),
    ]


def test_both_goes_to_the_plan_check_placeholder(env):  # noqa: F811
    job_id = _at_plan_start(env)
    r = env.post(f"/api/jobs/{job_id}/plan-start", {"plans_mode": "plans", "readout_llm": "both"})
    assert r.status_code == 200, r.json()
    env.drain()
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"], job["coming"]) == ("AWAITING_PLAN_CHECK", "plan_check", "")
    assert job["readout"] == "Claude + OpenAI"
    home = {j["id"]: j for j in env.client.get("/api/home").json()["jobs"]}
    assert home[job_id]["readout"] == "Claude + OpenAI"
    chosen = plan_start.chosen(env.settings.path("jobs") / job_id)
    assert chosen["readout_models"] == ["sonnet", "gpt"] and chosen["by"] == "designer (app)"


def test_exterior_renders_only_opens_choose_views_as_before(env):  # noqa: F811
    job_id = _at_plan_start(env)
    r = env.post(f"/api/jobs/{job_id}/plan-start", {"plans_mode": "renders_only"})
    assert r.status_code == 200, r.json()
    env.drain()
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"], job["readout"]) == (
        "AWAITING_VIEWS",
        "views",
        "Exterior renders only",
    )
    views = env.client.get(f"/api/jobs/{job_id}/views").json()["views"]
    env.auto_views = True
    env.post(f"/api/jobs/{job_id}/views", {"views": [v["id"] for v in views if v["ticked"]]})
    env.drain()
    assert env.client.get(f"/api/jobs/{job_id}").json()["status"] == "AWAITING_APPROVAL"


def test_the_api_validates_the_choice(env):  # noqa: F811
    job_id = _at_plan_start(env)
    url = f"/api/jobs/{job_id}/plan-start"
    assert env.post(url, {"plans_mode": "plans", "readout_llm": "gemini"}).status_code == 422
    assert env.post(url, {"plans_mode": "everything"}).status_code == 422
    assert env.client.post(url, json={"plans_mode": "renders_only"}).status_code == 403  # no token
    assert load_job(env.settings.path("jobs") / job_id).state["status"] == "AWAITING_PLAN_START"
    assert env.post(url, {"plans_mode": "renders_only"}).status_code == 200
    r = env.post(url, {"plans_mode": "plans"})  # no longer waiting here
    assert r.status_code == 409 and "not waiting" in r.json()["what_happened"]


def test_instruction_file_preselects_openai(env):  # noqa: F811
    pdf = make_pdf(env.install / "drop" / "Lot.pdf", ["ELEVATIONS", "FLOOR PLAN"])
    (env.install / "drop" / "Lot.txt").write_text("@readout_llm=openai\n", encoding="utf-8")
    env.drop(env.install / "drop" / "Lot.txt")
    job_id = _at_plan_start(env, pdf)
    r = env.client.get(f"/api/jobs/{job_id}/plan-start").json()
    assert (r["readout_llm"], r["from_file"]) == ("openai", {"readout_llm": "openai"})
