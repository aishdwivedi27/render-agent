"""PV2 through the app API: the floor plan trace, its overlay and area check on Check the plan
(docs/PLAN_VISUALS_SPEC.md section 3). Canned readout answers live with the private fixtures."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from render_agent.errors import PermanentError
from render_agent.output import disclaimer
from render_agent.pipeline.job import load_job
from tests.conftest import FIXTURE_01, ROOT, fixture_pdf, make_pdf
from tests.integration.test_m5_app import TOKEN_HEADER, _start, env  # noqa: F401

FIXTURE_03 = ROOT / "private" / "fixtures" / "fixture_03"


def _pdf(fixture: Path) -> Path:
    pdf = fixture_pdf(fixture)
    if pdf is None:
        pytest.skip(f"{fixture.name} is not available")
    return pdf


def _canned(fixture: Path) -> dict:
    path = fixture / "canned" / "plan_trace.json"
    if not path.is_file():
        pytest.skip(f"{fixture.name}/canned/plan_trace.json is not available")
    return json.loads(path.read_text(encoding="utf-8"))


def _to_plan_check(env, pdf, readout="claude") -> str:  # noqa: F811
    env.auto_plan_start = False
    env.auto_views = False
    job_id = _start(env, pdf, dry_run=True)
    env.drain()
    r = env.post(f"/api/jobs/{job_id}/plan-start", {"plans_mode": "plans", "readout_llm": readout})
    assert r.status_code == 200, r.json()
    env.drain()
    return job_id


def _areas(dwelling: dict) -> dict:
    return {a["area"]: a for a in dwelling["areas"]}


class FailFor:
    """Delegates to the test LLM but fails every call whose stage starts with `prefix`."""

    def __init__(self, inner, prefix: str) -> None:
        self.inner, self.prefix, self.calls = inner, prefix, []

    def call_json(self, stage, prompt, images, schema, schema_name):
        self.calls.append(stage)
        if stage.startswith(self.prefix):
            raise PermanentError(
                code="LLM_SCHEMA_INVALID", what_happened="The answer did not fit the schema."
            )
        return self.inner.call_json(stage, prompt, images, schema, schema_name)


def test_practice_run_shows_walls_on_the_drawing_and_the_total(env):  # noqa: F811
    job_id = _to_plan_check(env, _pdf(FIXTURE_03))
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"], job["status_text"], job["step_name"]) == (
        "AWAITING_PLAN_CHECK",
        "plan_check",
        "Check the plan",
        "Plans",
    )
    v = env.client.get(f"/api/jobs/{job_id}/plan-check").json()
    (d,) = v["dwellings"]
    assert "Walls traced from the vector drawing (code)" in d["method"]
    areas = _areas(d)
    assert areas["Total"]["traced"] is not None and not areas["Total"]["over"]
    assert areas["Garage"]["traced"] is None and areas["Garage"]["drawing"] == pytest.approx(32.68)
    img = env.client.get(f"/api/jobs/{job_id}/file/{d['overlay']}")
    assert img.status_code == 200
    with Image.open(io.BytesIO(img.content)) as overlay:
        assert disclaimer.check_image(overlay).ok
    geo = json.loads(
        (env.settings.path("jobs") / job_id / "plan" / "dwelling-1" / "geometry.json").read_text()
    )
    assert geo["method"] == "vector" and len(geo["walls"]) > 50


def test_canned_reading_gives_zone_areas_and_the_clearance_label(env):  # noqa: F811
    env.llm.canned["plan_trace.sonnet.page-2"] = _canned(FIXTURE_03)
    job_id = _to_plan_check(env, _pdf(FIXTURE_03))
    (d,) = env.client.get(f"/api/jobs/{job_id}/plan-check").json()["dwellings"]
    assert "Rooms read by Claude" in d["method"]
    for zone in ("Living", "Garage", "Alfresco", "Porch", "Total"):
        a = _areas(d)[zone]
        assert a["traced"] is not None and not a["over"], (zone, a)
    assert any(r["reading"] == "accessibility clearance (not drawn)" for r in d["rows"])
    geo = json.loads(
        (env.settings.path("jobs") / job_id / "plan" / "dwelling-1" / "geometry.json").read_text()
    )
    assert geo["clearance_zones"][0]["kind"] == "wc_clearance"
    assert {o["kind"] for o in geo["openings"]} >= {"garage_door", "hinged_door"}
    assert all(o["host_wall"].startswith("W") for o in geo["openings"])


def test_multi_dwelling_one_overlay_each_and_a_same_as_note(env):  # noqa: F811
    env.llm.canned["plan_trace.sonnet.page-2"] = _canned(FIXTURE_01)
    job_id = _to_plan_check(env, _pdf(FIXTURE_01))
    v = env.client.get(f"/api/jobs/{job_id}/plan-check").json()
    assert [d["id"] for d in v["dwellings"]] == ["DWELLING 1", "DWELLING 2", "DWELLING 3"]
    for d in v["dwellings"]:
        assert env.client.get(f"/api/jobs/{job_id}/file/{d['overlay']}").status_code == 200
        assert _areas(d)["Total"]["drawing"] is not None  # each dwelling's own schedule block
    same = [d for d in v["dwellings"] if d["mirror"]]
    assert same and same[0]["mirror"].startswith("Same as DWELLING")
    assert any(
        r["item"].endswith(same[0]["mirror"].split(",")[0][8:]) or "same layout" in r["item"]
        for r in same[0]["rows"]
    )


def test_both_models_one_fails_the_other_is_kept_and_read_again_retries_only_it(env):  # noqa: F811
    env.llm.canned["plan_trace.sonnet.page-2"] = _canned(FIXTURE_03)
    env.llm = FailFor(env.llm, "plan_trace.gpt")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03), readout="both")
    v = env.client.get(f"/api/jobs/{job_id}/plan-check").json()
    assert list(v["failed"]) == ["OpenAI"]
    assert "Rooms read by Claude" in v["dwellings"][0]["method"]
    jd = env.settings.path("jobs") / job_id
    assert (jd / "plan" / "trace_sonnet_page-2.json").is_file() and not (
        jd / "plan" / "trace_gpt_page-2.json"
    ).exists()
    env.llm = env.llm.inner  # OpenAI answers now
    calls_before = len(env.llm.calls)
    r = env.post(f"/api/jobs/{job_id}/plan-check/reread")
    assert r.status_code == 200, r.json()
    env.drain()
    stages = [c["stage"] for c in env.llm.calls[calls_before:]]
    assert stages == ["plan_trace.gpt.page-2"]  # Claude's reading is reused
    v = env.client.get(f"/api/jobs/{job_id}/plan-check").json()
    assert v["failed"] == {} and "Claude + OpenAI" in v["dwellings"][0]["method"]
    assert env.post(f"/api/jobs/{job_id}/plan-check/reread").status_code == 409  # nothing failed now


def test_no_vector_walls_means_the_readout_model_traces_them(env):  # noqa: F811
    pdf = make_pdf(env.install / "drop" / "Scan.pdf", ["FLOOR PLAN 1:100"])
    answer = _canned(FIXTURE_03)
    answer["walls"] = [
        {
            "id": "W1",
            "dwelling": "DWELLING 1",
            "outline": [[0.3, 0.3], [0.6, 0.3], [0.6, 0.302], [0.3, 0.302]],
            "exterior": True,
        }
    ]
    env.llm.canned["plan_trace.sonnet.page-1"] = answer
    job_id = _to_plan_check(env, pdf)
    v = env.client.get(f"/api/jobs/{job_id}/plan-check").json()
    assert v["notes"] and "No vector walls" in v["notes"][0] and "Claude" in v["notes"][0]
    assert "Walls traced by Claude" in v["dwellings"][0]["method"]
    geo = json.loads(
        (env.settings.path("jobs") / job_id / "plan" / "dwelling-1" / "geometry.json").read_text()
    )
    assert geo["method"] == "raster_trace" and len(geo["walls"]) == 1


def test_drawing_without_a_floor_plan_stops_with_a_plain_error(env):  # noqa: F811
    pdf = make_pdf(env.install / "drop" / "Elev.pdf", ["ELEVATIONS"])
    job_id = _to_plan_check(env, pdf)
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "FAILED" and "No floor plan" in job["error"]["what_happened"]
    assert "Exterior renders only" in job["error"]["what_to_do"]


def test_continue_goes_to_the_questions_placeholder(env):  # noqa: F811
    job_id = _to_plan_check(env, _pdf(FIXTURE_03))
    url = f"/api/jobs/{job_id}/plan-check/continue"
    assert env.client.post(url).status_code == 403  # no session token
    r = env.post(url)
    assert r.status_code == 200, r.json()
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"], job["coming"]) == (
        "AWAITING_PLAN_CHECK",
        "coming",
        "This screen is coming in PV3.",
    )
    load_job(env.settings.path("jobs") / job_id).set_status("AWAITING_VIEWS")
    assert env.post(url).status_code == 409
    assert env.client.get(f"/api/jobs/{job_id}/file/plan/../state.json").status_code == 404
    assert TOKEN_HEADER
