"""The plan check on the two reference drawings through the app (docs/PLAN_VISUALS_SPEC.md 3a): the
plan is cut from the sheet for the reading model, cut into high-detail tiles with the traced overlay on each,
and a second model call per tile adds Orange questions. Practice answers only; no paid call is made."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.test_m5_app import TOKEN_HEADER, _start, env  # noqa: F401
from tests.integration.test_pv2_app import FailFor, _to_plan_check

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "private" / "fixtures"
NAMES = ("fixture4a", "fixture4b")


def _pdf(name: str) -> Path:
    found = sorted((FIXTURES / name).glob("*.pdf"))
    if not found:
        pytest.skip(f"{name} is not available")
    return found[0]


def _folder(env, job_id: str) -> Path:  # noqa: F811
    summary = json.loads((env.settings.path("jobs") / job_id / "plan" / "trace_summary.json").read_text())
    return env.settings.path("jobs") / job_id / summary["dwellings"][0]["folder"]


@pytest.mark.parametrize("name", NAMES)
def test_the_plan_is_cut_into_tiles_with_the_overlay_and_each_tile_is_checked(env, name):  # noqa: F811
    job_id = _to_plan_check(env, _pdf(name))
    job_dir = env.settings.path("jobs") / job_id
    folder = _folder(env, job_id)
    index = json.loads((folder / "tiles" / "tiles.json").read_text())
    assert index["tiles"], "the plan was not cut into tiles"
    for t in index["tiles"]:
        assert (folder / "tiles" / t["plain"]).is_file() and (folder / "tiles" / t["overlay"]).is_file()
    # the reading model got the plan cut from the sheet, after the page image
    (read,) = [c for c in env.llm.calls if c["stage"].startswith("plan_trace.")]
    assert len(read["images"]) == 2 and read["images"][1].name == "page-1_plan.png"
    assert (job_dir / "plan" / "page-1_plan.png").is_file()
    assert "A second image is attached" in read["prompt"]
    # one check per tile and per close-up (close-ups first, up to the per-dwelling cap), each given the
    # area as printed and the same area with the overlay
    areas = [*index["focus"], *index["tiles"]]
    assert index["focus"], "no close-up areas were cut"
    checks = [c for c in env.llm.calls if c["stage"].startswith("plan_verify.")]
    assert len(checks) == min(len(areas), 16)
    assert any(c["stage"].endswith(".focus-1") for c in checks)
    assert all(len(c["images"]) == 2 and c["images"][1].name.endswith("_overlay.png") for c in checks)
    assert all("<<<BEGIN OVERLAY_ITEMS" in c["prompt"] for c in checks)
    # the model was asked where else to look closer, shown the whole plan with the overlay
    (ask,) = [c for c in env.llm.calls if c["stage"].startswith("plan_focus.")]
    assert [i.name for i in ask["images"]] == ["overview_overlay.png"] and "<<<BEGIN CHOSEN_AREAS" in ask[
        "prompt"
    ]
    for e in index["focus"]:
        assert (folder / "tiles" / e["plain"]).is_file() and (folder / "tiles" / e["overlay"]).is_file()
    summary = json.loads((job_dir / "plan" / "trace_summary.json").read_text())
    assert summary["dwellings"][0]["tiles"] == len(areas)


def test_a_finding_on_a_tile_is_an_orange_question_and_the_trace_is_unchanged(env):  # noqa: F811
    pdf = _pdf("fixture4b")
    first = _to_plan_check(env, pdf)
    before = env.client.get(f"/api/jobs/{first}/plan-check").json()["dwellings"][0]
    stage = next(
        c["stage"] for c in env.llm.calls if c["stage"].endswith(".tile-1")
    )  # plan_verify.<model>.tile-1
    assert stage.startswith("plan_verify.")
    env.llm.canned[stage] = {
        "findings": [
            {
                "kind": "wall_missing",
                "at": [0.5, 0.5],
                "item_id": None,
                "what": "A wall band is drawn here with no overlay.",
            }
        ],
        "notes": "",
    }
    env.llm.calls.clear()
    env.drop(pdf)  # the same PDF again: a second job, started anyway
    r = env.post("/api/inputs/start", {"name": pdf.stem, "options": {"dry_run": True}, "start_anyway": True})
    assert r.status_code == 200, r.json()
    second = str(r.json()["job_id"])
    env.drain()
    r = env.post(f"/api/jobs/{second}/plan-start", {"plans_mode": "plans", "readout_llm": "claude"})
    assert r.status_code == 200, r.json()
    env.drain()
    after = env.client.get(f"/api/jobs/{second}/plan-check").json()["dwellings"][0]
    new = [r for r in after["rows"] if r["id"].startswith("check_")]
    assert new and all(r["confidence"] == "ORANGE" and r["question"] for r in new)
    assert "wall band is drawn here" in new[0]["evidence"]
    old_ids = {r["id"] for r in before["rows"]}
    assert {r["id"] for r in after["rows"]} - old_ids == {r["id"] for r in new}  # only additions
    greens = lambda d: sorted(r["id"] for r in d["rows"] if r["confidence"] == "GREEN")  # noqa: E731
    assert greens(after) == greens(before)  # a finding never turns a Green row into anything else


def test_a_tile_the_model_cannot_check_is_noted_and_the_plan_check_still_opens(env):  # noqa: F811
    env.llm = FailFor(env.llm, "plan_verify")
    job_id = _to_plan_check(env, _pdf("fixture4a"))
    job = env.client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["screen"]) == ("AWAITING_PLAN_CHECK", "plan_check")
    summary = json.loads((env.settings.path("jobs") / job_id / "plan" / "trace_summary.json").read_text())
    assert any("could not check" in n for n in summary["notes"])


def test_the_open_plan_is_split_and_the_kitchen_is_in_the_main_table(env):  # noqa: F811
    job_id = _to_plan_check(env, _pdf("fixture4a"))
    rows = env.client.get(f"/api/jobs/{job_id}/plan-check").json()["dwellings"][0]["rows"]
    rooms = {r["item"]: r for r in rows if r["category"] == "room"}
    assert {"Family", "Kitchen", "Dining"} <= set(rooms)
    # each zone asks where it ends: the cut between them is not a wall
    for name in ("Family", "Kitchen", "Dining"):
        assert rooms[name]["confidence"] == "ORANGE" and "no wall divides them" in rooms[name]["question"]
    # the island code read from the drawing is a kitchen row of the main table, straight after the rooms
    island = [r for r in rows if r["category"] == "kitchen" and r["item"].startswith("ISLAND")]
    assert island and island[0]["table"] == "main"
    order = [r["category"] for r in rows if r["table"] == "main"]
    assert order.index("kitchen") > max(i for i, c in enumerate(order) if c == "room") - 1
    assert order.index("kitchen") < order.index("connection")


def test_a_cooktop_the_model_reads_in_the_kitchen_joins_the_kitchen_rows(env):  # noqa: F811
    from render_agent.llm.offline import minimal_instance

    pdf = _pdf("fixture4a")
    _to_plan_check(env, pdf)
    stage = next(c["stage"] for c in env.llm.calls if c["stage"].startswith("plan_trace."))
    answer = minimal_instance(json.loads((ROOT / "schemas" / "plan_trace.json").read_text(encoding="utf-8")))
    answer["fixtures"] = [
        {
            "id": "F1",
            "dwelling": "",
            "type": "COOKTOP",
            "room": "KITCHEN",
            "outline": [[0.5, 0.5], [0.6, 0.5], [0.6, 0.55], [0.5, 0.55]],
            "evidence": "Four burners drawn on the bench",
            "confidence": "GREEN",
            "alternative": None,
            "question": None,
        }
    ]
    env.llm.canned[stage] = answer
    env.drop(pdf)  # the same PDF again: a second job, started anyway
    r = env.post("/api/inputs/start", {"name": pdf.stem, "options": {"dry_run": True}, "start_anyway": True})
    assert r.status_code == 200, r.json()
    second = str(r.json()["job_id"])
    env.drain()
    r = env.post(f"/api/jobs/{second}/plan-start", {"plans_mode": "plans", "readout_llm": "claude"})
    assert r.status_code == 200, r.json()
    env.drain()
    rows = env.client.get(f"/api/jobs/{second}/plan-check").json()["dwellings"][0]["rows"]
    cooktop = [r for r in rows if r["category"] == "kitchen" and r["item"].startswith("COOKTOP")]
    assert cooktop and cooktop[0]["table"] == "main"  # the kitchen's cooktop sits with the kitchen rows
    assert cooktop[0]["confidence"] == "GREEN"  # carried as the model read it, like any fixture it reads
