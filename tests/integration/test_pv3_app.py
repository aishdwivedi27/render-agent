"""PV3 through the app API: the floor plan readout table, question batches, answers and the gate
(docs/PLAN_VISUALS_SPEC.md section 4). The sample readings live with the private fixture
(fixture_03/canned: Claude with an Orange and a Red item, OpenAI with one seeded disagreement)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from render_agent.pipeline.job import load_job
from tests.conftest import ROOT
from tests.integration.test_m5_app import Env, _start, env  # noqa: F401
from tests.integration.test_pv2_app import FIXTURE_03, FailFor, _pdf, _to_plan_check

CANNED = FIXTURE_03 / "canned"
PAGE = "page-2"


def _sample(name: str) -> dict:
    path = CANNED / name
    if not path.is_file():
        pytest.skip(f"fixture_03/canned/{name} is not available")
    return json.loads(path.read_text(encoding="utf-8"))


def _samples(env, *models: str) -> None:  # noqa: F811
    for m in models:
        env.llm.canned[f"plan_trace.{m}.{PAGE}"] = _sample(f"plan_trace.{m}.json")
        env.llm.canned[f"plan_reread.{m}.{PAGE}"] = _sample("plan_reread.json")


def _view(env, job_id: str) -> dict:  # noqa: F811
    return env.client.get(f"/api/jobs/{job_id}/plan-check").json()


def _row(v: dict, row_id: str, dwelling: str = "DWELLING 1") -> dict:
    d = next(d for d in v["dwellings"] if d["id"] == dwelling)
    return next(r for r in d["rows"] if r["id"] == row_id)


def _seeded(v: dict) -> dict:
    """The row where OpenAI reads a cavity slider and the others read a door: the seeded disagreement."""
    return next(
        r
        for r in v["dwellings"][0]["rows"]
        if r["category"] == "connection" and "OpenAI: Cavity slider" in r["reading"]
    )


def _job_dir(env, job_id: str) -> Path:  # noqa: F811
    return env.settings.path("jobs") / job_id


def _answer_batch(env, job_id: str, v: dict, texts: dict[str, str] | None = None):  # noqa: F811
    """Answers every question of the batch: a free text where given, else the first choice."""
    answers = []
    for b in v["batch"]:
        r = _row(v, b["row_id"], b["dwelling"])
        if texts and r["id"] in texts:
            answers.append({**b, "option": "text", "text": texts[r["id"]]})
        else:
            answers.append({**b, "option": r["options"][0]["key"]})
    return env.post(f"/api/jobs/{job_id}/plan-check/answers", {"answers": answers})


def test_both_models_show_a_mix_and_the_seeded_disagreement_as_orange(env):  # noqa: F811
    _samples(env, "sonnet", "gpt")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03), readout="both")
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"]) == ("AWAITING_PLAN_CHECK", "plan_check")
    v = _view(env, job_id)
    s = v["summary"]
    assert s["green"] and s["orange"] and s["red"], s
    assert v["summary_text"] == f"{s['green']} Green, {s['orange']} Orange, {s['red']} Red"
    assert v["models"] == ["Code", "Claude", "OpenAI"]
    door = _seeded(v)
    assert door["confidence"] == "ORANGE"
    assert "Claude: Door with swing arc" in door["reading"] and "OpenAI: Cavity slider" in door["reading"]
    assert [x["model"] for x in door["readings"]] == ["Code", "Claude", "OpenAI"]
    assert door["question"] and {o["key"] for o in door["options"]} >= {"use:sonnet", "use:gpt"}
    assert {r["table"] for d in v["dwellings"] for r in d["rows"]} == {"main", "other"}
    assert all(r["question"] is None for d in v["dwellings"] for r in d["rows"] if r["confidence"] == "GREEN")
    assert all(r["question"] for d in v["dwellings"] for r in d["rows"] if r["confidence"] != "GREEN")


def test_first_batch_starts_with_red_and_rows_carry_a_box_for_the_overlay(env):  # noqa: F811
    _samples(env, "sonnet", "gpt")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03), readout="both")
    v = _view(env, job_id)
    assert 0 < len(v["batch"]) <= v["batch_size"] == 5
    first = _row(v, v["batch"][0]["row_id"])
    assert first["id"] == "fixture_F1" and first["confidence"] == "RED"
    colours = [_row(v, b["row_id"])["confidence"] for b in v["batch"]]
    assert colours == sorted(colours, key=lambda c: {"RED": 0, "ORANGE": 1}[c])
    x0, y0, x1, y1 = first["target"]
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def test_answers_go_to_corrections_feedback_and_memory_and_the_plan_is_read_again(env):  # noqa: F811
    _samples(env, "sonnet", "gpt")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03), readout="both")
    jd = _job_dir(env, job_id)
    v = _view(env, job_id)
    version = v["dwellings"][0]["overlay_version"]
    before = len(env.llm.calls)
    r = _answer_batch(env, job_id, v, texts={"fixture_F1": "It is a WC"})
    assert r.status_code == 200, r.json()
    env.drain()
    stages = [c["stage"] for c in env.llm.calls[before:]]
    assert stages == [f"plan_reread.sonnet.{PAGE}"]  # only the free-text answer is read again
    prompt = env.llm.calls[before]["prompt"]
    assert "DESIGNER_ANSWERS" in prompt and "It is a WC" in prompt
    v2 = _view(env, job_id)
    for b in v["batch"]:
        row = _row(v2, b["row_id"])
        assert row["confidence"] == "GREEN" and row["answer"], row
    assert _row(v2, "fixture_F1")["answer"] == "It is a WC"
    assert v2["dwellings"][0]["overlay_version"] != version  # the overlay was drawn again
    assert (
        len(v2["batch"])
        and v2["summary"]["orange"] + v2["summary"]["red"] < v["summary"]["orange"] + v["summary"]["red"]
    )
    items = json.loads((jd / "corrections.json").read_text())["items"]
    assert len(items) == len(v["batch"]) and {i["where"] for i in items} == {"plan_check"}
    lines = [
        json.loads(x) for x in (env.settings.path("feedback") / "feedback.jsonl").read_text().splitlines()
    ]
    assert sum(x["category"] == "readout_answer" for x in lines) == len(v["batch"])
    (mem,) = (env.settings.path("memory") / "projects").glob("*.json")
    assert len(json.loads(mem.read_text())["items"]) == len(v["batch"])


def test_picking_a_models_reading_changes_the_geometry(env):  # noqa: F811
    _samples(env, "sonnet", "gpt")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03), readout="both")
    seeded = _seeded(_view(env, job_id))
    r = env.post(
        f"/api/jobs/{job_id}/plan-check/answers",
        {"answers": [{"dwelling": "DWELLING 1", "row_id": seeded["id"], "option": "use:gpt"}]},
    )
    assert r.status_code == 200, r.json()
    env.drain()
    geo = json.loads((_job_dir(env, job_id) / "plan" / "dwelling-1" / "geometry.json").read_text())
    item = seeded["id"].removeprefix("connection_")
    assert next(o for o in geo["openings"] if o["id"] == item)["kind"] == "cavity_slider"
    door = _row(_view(env, job_id), seeded["id"])
    assert door["confidence"] == "GREEN" and door["answer"].startswith("OpenAI: Cavity slider")


def test_all_green_shows_the_final_tables_and_only_continue_moves_on(env):  # noqa: F811
    _samples(env, "sonnet", "gpt")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03), readout="both")
    for _ in range(40):
        v = _view(env, job_id)
        if v["all_green"]:
            break
        assert _answer_batch(env, job_id, v).status_code == 200
        env.drain()
    # every row is Green, but nothing moved on: the final tables wait for Continue
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"]) == ("AWAITING_PLAN_CHECK", "plan_check")
    assert _view(env, job_id)["summary_text"].endswith("0 Orange, 0 Red")
    jd = _job_dir(env, job_id)
    assert not (jd / "plan" / "readout_approved.json").exists()
    assert (
        (jd / "plan" / "dwelling-1" / "readout.md")
        .read_text(encoding="utf-8")
        .rstrip()
        .endswith("All items are Green.")
    )
    assert env.post(f"/api/jobs/{job_id}/plan-check/continue").status_code == 200
    env.drain()
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"], job["coming"]) == (
        "AWAITING_ELEVATION_CHOICE",
        "coming",
        "Elevation choice (coming in PV4).",
    )
    jd = _job_dir(env, job_id)
    approved = json.loads((jd / "plan" / "readout_approved.json").read_text())
    (d,) = approved["dwellings"]
    geo = (jd / "plan" / "dwelling-1" / "geometry.json").read_bytes()
    assert d["geometry_sha256"] == hashlib.sha256(geo).hexdigest() and approved["models"] == [
        "Claude",
        "OpenAI",
    ]
    assert load_job(jd).state["stages"]["gate_plan_check"]["status"] == "done"


def test_the_api_refuses_to_move_on_while_any_row_is_orange_or_red(env):  # noqa: F811
    _samples(env, "sonnet")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03))
    url = f"/api/jobs/{job_id}/plan-check"
    assert env.client.post(f"{url}/continue").status_code == 403  # no session token
    assert env.client.post(f"{url}/answers", json={"answers": []}).status_code == 403
    r = env.post(f"{url}/continue")
    assert r.status_code == 409 and "Orange or Red" in r.json()["what_happened"]

    def send(*answers):
        return env.post(f"{url}/answers", {"answers": list(answers)})

    d = "DWELLING 1"
    assert send({"dwelling": d, "row_id": "scale", "option": "confirm"}).status_code == 422  # Green
    assert send({"dwelling": d, "row_id": "nothing", "option": "confirm"}).status_code == 422
    assert send({"dwelling": d, "row_id": "fixture_F1", "option": "text", "text": " "}).status_code == 422
    assert send({"dwelling": d, "row_id": "fixture_F1", "option": "made-up"}).status_code == 422
    assert (
        send(*({"dwelling": d, "row_id": f"r{i}", "option": "confirm"} for i in range(6))).status_code == 422
    )
    assert load_job(_job_dir(env, job_id)).state["status"] == "AWAITING_PLAN_CHECK"
    assert not (_job_dir(env, job_id) / "plan" / "answers.json").exists()
    load_job(_job_dir(env, job_id)).set_status("AWAITING_VIEWS")
    assert send({"dwelling": d, "row_id": "fixture_F1", "option": "confirm"}).status_code == 409


def test_claude_only_has_no_per_model_readings(env):  # noqa: F811
    _samples(env, "sonnet")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03))
    v = _view(env, job_id)
    assert v["models"] == ["Claude"]
    assert all(r["readings"] == [] for d in v["dwellings"] for r in d["rows"])
    unlabelled = next(
        r for r in v["dwellings"][0]["rows"] if "Unlabelled space between" in (r["question"] or "")
    )
    assert unlabelled["confidence"] == "ORANGE" and unlabelled["category"] == "room"


def test_one_model_failing_leaves_the_other_reading_orange_not_read(env):  # noqa: F811
    _samples(env, "sonnet")
    env.llm = FailFor(env.llm, "plan_trace.gpt")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03), readout="both")
    v = _view(env, job_id)
    assert list(v["failed"]) == ["OpenAI"]
    bed = next(r for r in v["dwellings"][0]["rows"] if "OpenAI could not read" in (r["question"] or ""))
    assert bed["confidence"] == "ORANGE"
    assert next(x for x in bed["readings"] if x["model"] == "OpenAI")["status"] == "not_read"


def test_a_failed_re_read_keeps_the_answers_and_retry_reads_again(env):  # noqa: F811
    _samples(env, "sonnet")
    inner = env.llm
    env.llm = FailFor(inner, "plan_reread")
    job_id = _to_plan_check(env, _pdf(FIXTURE_03))
    answer = {"dwelling": "DWELLING 1", "row_id": "fixture_F1", "option": "text", "text": "It is a WC"}
    assert env.post(f"/api/jobs/{job_id}/plan-check/answers", {"answers": [answer]}).status_code == 200
    env.drain()
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "FAILED" and job["error"]["what_happened"]
    jd = _job_dir(env, job_id)
    assert json.loads((jd / "plan" / "answers.json").read_text())["answers"][0]["text"] == "It is a WC"
    env.llm = inner
    assert env.post(f"/api/jobs/{job_id}/retry", {}).status_code == 200
    env.drain()
    assert _row(_view(env, job_id), "fixture_F1")["answer"] == "It is a WC"
    assert (jd / "plan" / f"reread_sonnet_{PAGE}_batch-1.json").is_file()


def test_practice_run_uses_the_sample_reading_with_no_key(install, tmp_path, monkeypatch):
    _sample("plan_trace.sonnet.json")
    cfg = yaml.safe_load((install / "config.yaml").read_text(encoding="utf-8"))
    cfg["paths"]["practice"] = str(ROOT / "private" / "fixtures")
    (install / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    e = Env(install, tmp_path / "Desktop", monkeypatch, keyed=False)
    e.llm = None  # no test model: the app's own Practice run path
    job_id = _to_plan_check(e, _pdf(FIXTURE_03), readout="both")
    v = _view(e, job_id)
    assert "Practice run: sample reading" in v["dwellings"][0]["method"]
    assert "OpenAI: Cavity slider" in _seeded(v)["reading"]
    assert _row(v, "fixture_F1")["confidence"] == "RED"


def test_exterior_renders_only_still_goes_straight_to_choose_views(env):  # noqa: F811
    env.auto_views = False
    job_id = _start(env, _pdf(FIXTURE_03), dry_run=True)
    env.drain()  # takes "Exterior renders only"
    job = load_job(_job_dir(env, job_id))
    assert job.state["status"] == "AWAITING_VIEWS"
    assert job.state["stages"]["gate_plan_check"]["status"] == "skipped"
    assert job.state["stages"]["gate_elevation_choice"]["status"] == "skipped"
