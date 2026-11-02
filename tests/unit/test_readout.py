"""PV3: readout rows scored and merged by code, question batches, answers applied to the reading, project
memory and the Practice run samples (docs/PLAN_VISUALS_SPEC.md sections 4 and 13)."""

from __future__ import annotations

import json

import pytest

from render_agent import memory
from render_agent.llm.practice import PracticeLLM, find_canned
from render_agent.plan import readout
from render_agent.plan.trace_rules import load_trace_rules
from render_agent.util.fs import sha256_file
from tests.conftest import make_pdf

PAGE = (10000.0, 10000.0)  # page size in mm: a page fraction of 0.01 is 100 mm
LABELS = {"sonnet": "Claude", "gpt": "OpenAI"}


@pytest.fixture
def rules(install_settings):
    return load_trace_rules(install_settings.path("knowledge"))


def _room(rid="R1", name="BED 1", conf="GREEN", box=(0.1, 0.1, 0.4, 0.4), alt=None, q=None):
    x0, y0, x1, y1 = box
    return {
        "id": rid,
        "dwelling": "DWELLING 1",
        "name": name,
        "outline": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        "evidence": f"Label {name}",
        "confidence": conf,
        "alternative": alt,
        "question": q,
    }


def _door(oid="O1", kind="hinged_door", at=(0.25, 0.1), conf="GREEN"):
    return {
        "id": oid,
        "dwelling": "DWELLING 1",
        "kind": kind,
        "at": list(at),
        "width_mm": 900,
        "code": None,
        "swing": None,
        "rooms": ["BED 1", "HALL"],
        "evidence": "Swing arc",
        "confidence": conf,
        "alternative": None,
        "question": None,
    }


def _reading(rooms=(), openings=(), fixtures=()):
    return {
        "dwellings": [],
        "walls": [],
        "rooms": list(rooms),
        "openings": list(openings),
        "recesses": [],
        "fixtures": list(fixtures),
        "clearance_zones": [],
        "notes": [],
    }


def _rows(rules, readings, names=("sonnet",), failed=(), walls=()):
    return readout.model_rows(
        "DWELLING 1", 1, readings, names[0], list(names), list(failed), LABELS, PAGE, list(walls), rules
    )


# --- scoring --------------------------------------------------------------------------------------
def test_green_rows_have_no_question_and_orange_rows_get_one(rules):
    (green,) = _rows(rules, {"sonnet": _reading([_room(q="should be dropped")])})
    assert green["confidence"] == "GREEN" and green["question"] is None and green["options"] == []
    (orange,) = _rows(rules, {"sonnet": _reading([_room(conf="ORANGE")])})
    assert orange["confidence"] == "ORANGE" and orange["question"]  # the template, as the model gave none
    assert [o["key"] for o in orange["options"]] == ["confirm", "not_there"]


def test_an_alternative_reading_cannot_be_green(rules):
    (r,) = _rows(rules, {"sonnet": _reading([_room(alt="part of the hall")])})
    assert r["confidence"] == "ORANGE" and "part of the hall" in r["question"]
    assert "alternative" in [o["key"] for o in r["options"]]


def test_a_room_off_the_traced_walls_cannot_be_green(rules):
    walls = [(1000.0, 1000.0, 4000.0, 1090.0), (1000.0, 1000.0, 1090.0, 4000.0)]  # two of four walls
    (r,) = _rows(rules, {"sonnet": _reading([_room()])}, walls=walls)
    assert r["confidence"] == "ORANGE" and "off the traced walls" in r["question"]
    near = [*walls, (1000.0, 3910.0, 4000.0, 4000.0), (3910.0, 1000.0, 4000.0, 4000.0)]
    (r,) = _rows(rules, {"sonnet": _reading([_room()])}, walls=near)
    assert r["confidence"] == "GREEN"


def test_an_opening_is_measured_from_its_edges_not_its_middle(rules):
    walls = [(1000.0, 950.0, 2050.0, 1050.0), (2950.0, 950.0, 4000.0, 1050.0)]  # a 900 mm gap at 2500
    (r,) = _rows(rules, {"sonnet": _reading(openings=[_door()])}, walls=walls)
    assert r["confidence"] == "GREEN" and r["category"] == "connection"


# --- both models ---------------------------------------------------------------------------------
def test_both_models_reading_the_same_is_green_with_each_reading(rules):
    both = {"sonnet": _reading([_room()], [_door()]), "gpt": _reading([_room("A")], [_door("B")])}
    rs = _rows(rules, both, names=("sonnet", "gpt"))
    assert [r["confidence"] for r in rs] == ["GREEN", "GREEN"]
    assert [x["model"] for x in rs[0]["readings"]] == ["Claude", "OpenAI"]


def test_a_disagreement_is_orange_and_shows_both_readings(rules):
    both = {"sonnet": _reading(openings=[_door()]), "gpt": _reading(openings=[_door("X", "cavity_slider")])}
    (r,) = _rows(rules, both, names=("sonnet", "gpt"))
    assert r["confidence"] == "ORANGE"
    assert r["reading"].startswith("Claude: Door with swing arc") and "OpenAI: Cavity slider" in r["reading"]
    assert [o["key"] for o in r["options"]] == ["use:sonnet", "use:gpt", "not_there"]
    assert r["ref"]["model_items"] == [{"model": "sonnet", "item_id": "O1"}, {"model": "gpt", "item_id": "X"}]


def test_an_item_only_one_model_found_is_orange(rules):
    both = {
        "sonnet": _reading([_room()]),
        "gpt": _reading([_room("A"), _room("B", "WIR", box=(0.5, 0.5, 0.6, 0.6))]),
    }
    rs = _rows(rules, both, names=("sonnet", "gpt"))
    only = next(r for r in rs if r["id"] == "room_gpt_B")
    assert only["confidence"] == "ORANGE" and "Only OpenAI" in only["question"]


def test_a_model_that_failed_leaves_the_other_reading_orange_not_read(rules):
    (r,) = _rows(rules, {"sonnet": _reading([_room()])}, names=("sonnet", "gpt"), failed=("gpt",))
    assert r["confidence"] == "ORANGE" and "OpenAI could not read" in r["question"]
    assert r["readings"][1] == {
        "model": "OpenAI",
        "status": "not_read",
        "reading": "not read",
        "confidence": "RED",
    }


# --- batches --------------------------------------------------------------------------------------
def test_batches_hold_at_most_the_batch_size_red_first(rules):
    rows = [("D", {"id": f"o{i}", "confidence": "ORANGE"}) for i in range(4)]
    rows += [("D", {"id": "g", "confidence": "GREEN"}), ("D", {"id": "r1", "confidence": "RED"})]
    rows += [("D", {"id": "r2", "confidence": "RED"})]
    got = [r["id"] for _, r in readout.batch(rows, rules.readout.batch_size)]
    assert got == ["r1", "r2", "o0", "o1", "o2"]


# --- answers --------------------------------------------------------------------------------------
def _answer(row, option, text="", batch=1):
    return {"row_id": row["id"], "ref": row["ref"], "option": option, "text": text, "batch": batch}


def test_answers_patch_the_reading_and_free_text_is_read_again(rules):
    both = {
        "sonnet": _reading([_room(), _room("R2", "WIR", conf="ORANGE")], [_door()]),
        "gpt": _reading([_room("A")], [_door("X", "cavity_slider")]),
    }
    rs = {r["id"]: r for r in _rows(rules, both, names=("sonnet", "gpt"))}
    asked = []

    def reread(batch, items, answers):
        asked.append((batch, items, [a["text"] for a in answers]))
        return {
            "rooms": [{**items["rooms"][0], "name": "WALK-IN ROBE"}],
            "openings": [],
            "recesses": [],
            "fixtures": [],
        }

    out = readout.apply_answers(
        both["sonnet"],
        both,
        [_answer(rs["connection_O1"], "use:gpt"), _answer(rs["room_R2"], "text", "It is the walk-in robe")],
        rules,
        reread,
    )
    assert out["openings"][0]["kind"] == "cavity_slider" and out["openings"][0]["id"] == "O1"
    assert out["rooms"][1]["name"] == "WALK-IN ROBE"
    assert asked == [(1, {"rooms": [both["sonnet"]["rooms"][1]]}, ["It is the walk-in robe"])]
    gone = readout.apply_answers(both["sonnet"], both, [_answer(rs["room_R2"], "not_there")], rules)
    assert [r["id"] for r in gone["rooms"]] == ["R1"]
    assert both["sonnet"]["rooms"][1]["name"] == "WIR"  # the cached reading itself is never changed


def test_bath_with_shower_over_records_a_confirmed_shower(rules):
    bath = {
        "id": "F1",
        "dwelling": "DWELLING 1",
        "type": "BATH",
        "room": "BATH",
        "outline": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.15], [0.1, 0.15]],
        "evidence": "Bath symbol",
        "confidence": "GREEN",
        "alternative": None,
        "question": None,
    }
    reading = _reading([_room("R7", "BATH")], fixtures=[bath])
    out = readout.apply_answers(
        reading, {"sonnet": reading}, [{"row_id": "shower_R7", "ref": None, "option": "shower_over"}], rules
    )
    shower = out["fixtures"][1]
    assert shower["type"] == "SHOWER" and shower["source"] == "confirmed" and shower["room"] == "BATH"


def test_answered_rows_are_green_and_show_what_changed():
    row = {"id": "room_R1", "reading": "WIR, 2.0 m²", "confidence": "ORANGE", "question": "?", "options": [1]}
    a = {
        "row_id": "room_R1",
        "answer": "It is the walk-in robe",
        "reading_before": "BED 1, 9.0 m²",
        "item": "X",
    }
    gone = {
        "row_id": "room_R9",
        "answer": "It is not on the drawing",
        "reading_before": "LINEN",
        "item": "HALL",
    }
    out = readout.finalize([row], {"room_R1": a, "room_R9": gone})
    assert (
        out[0]["confidence"] == "GREEN"
        and out[0]["changed"] == "was: BED 1, 9.0 m²"
        and out[0]["question"] is None
    )
    assert out[1]["id"] == "room_R9" and out[1]["confidence"] == "GREEN" and out[1]["changed"] == "was: LINEN"


# --- project memory --------------------------------------------------------------------------------
def test_answers_are_remembered_for_the_project(install_settings, tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "house_specification.json").write_text(json.dumps({"PROJECT": {"project_number": "P 12"}}))
    assert (
        memory.remember(install_settings, job, "j1", "DWELLING 1", "readout_answer", "MUD: yes", "floor plan")
        is None
    )
    assert (
        memory.remember(install_settings, job, "j1", "DWELLING 1", "readout_answer", "WC: yes", "floor plan")
        is None
    )
    key, number = memory.project_key(job, "DWELLING 1")
    data = json.loads((install_settings.path("memory") / "projects" / f"{key}.json").read_text())
    assert number == "P 12" and [i["text"] for i in data["items"]] == ["MUD: yes", "WC: yes"]
    (install_settings.path("memory") / "projects" / f"{key}.json").write_text("{broken")
    warn = memory.remember(install_settings, job, "j1", "DWELLING 1", "readout_answer", "x", "floor plan")
    assert warn and "could not be saved" in warn  # the job carries on


def test_without_a_project_number_the_drawing_fingerprint_is_the_key(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"inputs": [{"sha256": "ab" * 32}]}))
    key, number = memory.project_key(tmp_path, "DWELLING 2")
    assert number == "NOT FOUND" and key == "drawing-abababababab_dwelling-2"


# --- practice samples ------------------------------------------------------------------------------
def test_practice_samples_are_found_by_the_drawing_fingerprint(install, tmp_path):
    from render_agent.config import load_config

    pdf = make_pdf(install / "private" / "fixtures" / "set_a" / "input" / "a.pdf", ["FLOOR PLAN"])
    canned = install / "private" / "fixtures" / "set_a" / "canned"
    canned.mkdir()
    schema = json.loads((install / "schemas" / "plan_trace.json").read_text())
    sample = _reading([_room()])
    (canned / "plan_trace.gpt.json").write_text(json.dumps(sample))
    settings = load_config(install)
    assert find_canned(settings, ["0" * 64]) is None
    found = find_canned(settings, [sha256_file(pdf)])
    assert found == canned
    llm = PracticeLLM(found)
    assert llm.call_json("plan_trace.gpt.page-2", "", [], schema, "plan_trace") == sample
    assert llm.call_json("plan_trace.sonnet.page-2", "", [], schema, "plan_trace")["rooms"] == []  # no sample
