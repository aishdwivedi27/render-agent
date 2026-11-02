"""PV3 fix round (trace parity): code is the first reader, the models are second readers; the table has the
project's rows and columns, and the extra checks sit in a second table (PLAN_VISUALS_SPEC sections 4, 14)."""

from __future__ import annotations

import pytest

from render_agent.plan import code_items, readout, readout_table, rows
from render_agent.plan.trace_rules import load_trace_rules
from tests.unit.test_readout import LABELS, PAGE, _door, _reading, _room

LABEL = {**LABELS, "code": "Code"}


@pytest.fixture
def rules(install_settings):
    return load_trace_rules(install_settings.path("knowledge"))


def _code_dict():
    return {
        "rooms": [
            {
                "id": "s1",
                "name": "BED 3",
                "labels": ["BED 3"],
                "outline": [[1000, 1000], [4000, 1000], [4000, 4000], [1000, 4000]],
                "area_m2": 9.0,
                "width_mm": 3000,
                "depth_mm": 3000,
                "outdoor": False,
            },
            {
                "id": "s2",
                "name": None,
                "labels": [],
                "outline": [[4000, 1000], [6000, 1000], [6000, 4000], [4000, 4000]],
                "area_m2": 6.0,
                "width_mm": 2000,
                "depth_mm": 3000,
                "outdoor": False,
            },
        ],
        "openings": [
            {
                "id": "o1",
                "kind": "hinged_door",
                "alternative": None,
                "confidence": "clear",
                "evidence": "Swing arc across a 950 mm gap",
                "frame": [3900, 2000, 4100, 2950],
                "axis": "v",
                "width_mm": 950,
                "position_mm": 2475,
                "wall_line_mm": 4000,
                "exterior": False,
                "hinge_end": 0,
                "swing_side": 1,
                "code": None,
                "code_note": None,
                "rooms": [],
                "off_drawing_mm": 0,
            },
            {
                "id": "o2",
                "kind": "window",
                "alternative": None,
                "confidence": "clear",
                "evidence": "Two thin lines",
                "frame": [1500, 900, 2500, 1100],
                "axis": "h",
                "width_mm": 1000,
                "position_mm": 2000,
                "wall_line_mm": 1000,
                "exterior": True,
                "hinge_end": None,
                "swing_side": None,
                "code": None,
                "code_note": None,
                "rooms": [],
                "off_drawing_mm": 0,
            },
        ],
        "recesses": [],
        "wall_rows": [],
        "connections": [{"opening": "o1", "item": "Bed 3 -> Unlabelled", "a": "s1", "b": "s2"}],
        "unlabelled": [{"room": "s2", "between": ["Bed 3"]}],
        "room_offsets": [{"id": "s1", "off_drawing_mm": 0}, {"id": "s2", "off_drawing_mm": 250}],
    }


def _rows(rules, code_reading, models=None, failed=()):
    readings = {"code": code_reading, **(models or {})}
    names = ["code", *(models or {})]
    return readout.model_rows("DWELLING 1", 1, readings, "code", names, list(failed), LABEL, PAGE, [], rules)


def test_code_reading_becomes_a_reader_in_the_models_shape(rules):
    got = code_items.as_reading(_code_dict(), _reading(), PAGE, rules)
    bed, hall = got["rooms"]
    assert bed["name"] == "BED 3" and bed["confidence"] == "GREEN"
    assert hall["confidence"] == "ORANGE" and "Unlabelled space between Bed 3" in hall["question"]
    door, window = got["openings"]
    assert door["swing"] == "BED 3"
    assert door["hinge"] and window["confidence"] == "ORANGE"  # no window code beside it
    assert window["question"] == "window_code"


def test_the_table_names_rows_like_the_project(rules):
    got = code_items.as_reading(_code_dict(), _reading(), PAGE, rules)
    out = _rows(rules, got)
    items = [r["item"] for r in out]
    assert "Bed 3" in items and "Bed 3 -> Unlabelled" in items
    window = next(r for r in out if r["category"] == "window")
    assert window["item"] == "Window, Bed 3 top wall" and "No window code" in window["question"]
    door = next(r for r in out if r["id"] == "connection_o1")
    assert door["reading"].startswith("Door with swing arc, hinge at the ")
    assert "opens into Bed 3" in door["reading"]


def test_code_clear_and_a_model_agreeing_is_green(rules):
    code = _reading(rooms=[_room("c1", "BED 1")])
    out = _rows(rules, code, {"sonnet": _reading(rooms=[_room("m1", "BED 1")])})
    assert out[0]["confidence"] == "GREEN" and out[0]["question"] is None


def test_code_clear_and_a_model_differing_is_orange_with_both_readings(rules):
    code = _reading(openings=[_door("c1", "cavity_slider")])
    model = _reading(openings=[_door("m1", "hinged_door")])
    row = _rows(rules, code, {"sonnet": model})[0]
    assert row["confidence"] == "ORANGE"
    assert row["reading"].startswith("Code: Cavity slider")
    assert "Claude: Door with swing arc" in row["reading"]
    assert [o["key"] for o in row["options"]][:2] == ["use:code", "use:sonnet"]


def test_code_unsure_and_a_model_deciding_is_orange_with_the_models_reading(rules):
    code_door = {**_door("c1", "cavity_slider", conf="ORANGE"), "alternative": "arch"}
    model = _reading(openings=[_door("m1", "arch")])
    row = _rows(rules, _reading(openings=[code_door]), {"sonnet": model})[0]
    assert row["confidence"] == "ORANGE" and row["reading"] == "Arch, 900 mm wide"
    assert "Code could not tell" in row["question"]


def test_an_item_only_a_model_found_is_orange(rules):
    out = _rows(rules, _reading(), {"sonnet": _reading(rooms=[_room("m1", "MUD")])})
    assert out[0]["confidence"] == "ORANGE" and "Only Claude found" in out[0]["question"]


def test_a_red_code_item_with_no_help_gets_an_exact_question(rules):
    out = _rows(rules, _reading(rooms=[_room("c1", "X", conf="RED")]))
    assert out[0]["confidence"] == "RED" and "Neither Code nor the models could read" in out[0]["question"]


def test_an_item_off_the_drawing_cannot_be_green(rules):
    room = {**_room("c1", "BED 1"), "off_drawing_mm": 250}
    assert _rows(rules, _reading(rooms=[room]))[0]["confidence"] == "ORANGE"


def test_main_rows_come_in_the_projects_order_and_extras_move_to_other_checks(rules):
    mixed = [
        rows.row("area_living", "Living area", "area", "x", "y", "GREEN"),
        rows.row("window_1", "Window", "window", "x", "y", "GREEN"),
        rows.row("room_1", "Bed 1", "room", "x", "y", "GREEN"),
        rows.row("scale", "Scale", "scale", "1:100", "y", "GREEN"),
        rows.row("fx", "WC", "fixture", "x", "y", "ORANGE", "Is it a WC?"),
    ]
    got = rows.assign_tables(mixed, rules)
    assert [r["id"] for r in got] == ["scale", "room_1", "window_1", "area_living", "fx"]
    assert [r["table"] for r in got] == ["main"] * 3 + ["other"] * 2
    s = rows.summaries(got)
    assert rows.summary_line(s["main"]) == "3 Green, 0 Orange, 0 Red"
    assert rows.summary_line(s["other"]) == "1 Green, 1 Orange, 0 Red"


def test_wall_rows_from_the_overlay_check_have_a_box_and_a_question(rules):
    walls = [
        {
            "id": "wall_missing_1_2",
            "kind": "missing",
            "box": [0, 0, 500, 200],
            "confidence": "ORANGE",
            "evidence": "Band 500 x 200 not traced",
            "question": "Is it a wall?",
        }
    ]
    row = rows.wall_rows(walls, rules)[0]
    assert row["category"] == "wall" and row["target_mm"] == [0, 0, 500, 200] and row["question"]


def test_readout_md_has_the_projects_columns_and_two_tables(rules):
    got = rows.assign_tables(
        [
            rows.row("scale", "Scale", "scale", "1:100", "Sheet", "GREEN"),
            rows.row("fx", "WC", "fixture", "WC", "Symbol", "ORANGE", "Is it a WC?"),
        ],
        rules,
    )
    s = rows.summaries(got)
    table = {
        "dwelling": "DWELLING 1",
        "models": ["Claude"],
        "rows": got,
        "main_summary": s["main"],
        "other_summary": s["other"],
    }
    text = readout_table.markdown(table, rules)
    assert "1 Green, 0 Orange, 0 Red" in text
    assert "| # | Item | What I read | Evidence | Confidence | Question for you |" in text
    assert "## Other checks" in text and "0 Green, 1 Orange, 0 Red" in text
    assert "🟢 Green" in text and "🟠 Orange" in text and "All items are Green." not in text
