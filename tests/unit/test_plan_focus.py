"""Open-plan zones, kitchen joinery and close-up areas (PLAN_VISUALS_SPEC sections 3a and 3b). Synthetic
drawings cover each rule; nothing here calls a model or a vendor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from render_agent.errors import PermanentError
from render_agent.plan import focus, kitchen, rooms, tiles, verify
from render_agent.plan.source import Word
from render_agent.plan.trace_rules import load_trace_rules
from tests.fakes import FakeLLM
from tests.unit.test_plan_tiles import FINDING, READING, WALLS, _Blank, _Raises

ROOT = Path(__file__).resolve().parents[2]
PLAN = (0.0, 0.0, 20000.0, 30000.0)


@pytest.fixture(scope="module")
def rules():
    return load_trace_rules(ROOT / "private" / "knowledge")


# --- open-plan zones -----------------------------------------------------------------------------------
def _area(cell) -> float:
    return sum((r[2] - r[0]) * (r[3] - r[1]) for r in cell)


def test_open_space_is_divided_between_the_labels_nearest_each_point():
    members = [(0.0, 0.0, 6000.0, 4000.0)]
    left, right = rooms._zones(members, [(1000.0, 2000.0), (5000.0, 2000.0)], 10.0)
    assert _area(left) + _area(right) == pytest.approx(6000 * 4000, rel=0.01)  # nothing lost or counted twice
    assert max(r[2] for r in left) == pytest.approx(3000, abs=20)  # the cut is halfway between the labels
    assert min(r[0] for r in right) == pytest.approx(3000, abs=20)


def test_three_labels_give_three_zones_that_cover_the_space():
    members = [(0.0, 0.0, 6000.0, 6000.0)]
    cells = rooms._zones(members, [(1000.0, 1000.0), (5000.0, 1000.0), (3000.0, 5000.0)], 10.0)
    assert len(cells) == 3 and all(cells)
    assert sum(_area(c) for c in cells) == pytest.approx(6000 * 6000, rel=0.01)


def test_a_staircase_cut_becomes_one_straight_line_and_wall_corners_stay():
    # a 4 x 4 m square whose right side is a diagonal drawn as 10 mm steps
    stairs = [
        p
        for i in range(100)
        for p in ((3000.0 + i * 10, 1000.0 + i * 10), (3010.0 + i * 10, 1000.0 + i * 10))
    ]
    outline = [(0.0, 0.0), (3000.0, 0.0), *stairs, (4000.0, 4000.0), (0.0, 4000.0)]
    out = rooms._straighten(outline, 130.0)
    assert len(out) <= 8 and (0.0, 0.0) in out and (0.0, 4000.0) in out


def test_a_zone_too_small_to_be_a_space_keeps_the_space_whole(rules):
    room = rooms.Room("R", [], 30.0, ["A", "B"], False, [(0.0, 0.0, 6000.0, 5000.0)])
    far = [rooms.Label("A", [Word(0, 0, 100, 100, "A")]), rooms.Label("B", [Word(5900, 0, 6000, 100, "B")])]
    split = rooms._open_plan_zones(room, far, 10.0, rules)
    assert split is not None and {z.name for z in split} == {"A", "B"}
    assert all(z.shares_with for z in split)
    close = [far[0], rooms.Label("B", [Word(40, 40, 140, 140, "B")])]  # labels nearly on top of each other
    assert rooms._open_plan_zones(room, close, 10.0, rules) is None


# --- kitchen joinery -----------------------------------------------------------------------------------
def _ink(draw_fn) -> Image.Image:
    img = Image.new("L", (600, 400), 0)  # 10 mm per pixel; 255 = ink
    draw_fn(ImageDraw.Draw(img))
    return img


def test_a_free_standing_box_of_four_lines_is_found_even_with_dimension_lines_touching_it(rules):
    def box(d):
        d.rectangle([100, 100, 330, 190], outline=255, width=3)  # 2300 x 900 mm
        d.line([(100, 60), (330, 60)], fill=255, width=2)  # a dimension line above, joined by its ticks
        d.line([(100, 60), (100, 100)], fill=255, width=2)
        d.line([(330, 60), (330, 100)], fill=255, width=2)

    found = kitchen._boxes(_ink(box), 10.0, rules)
    assert any(abs((b[2] - b[0]) - 2300) <= 100 and abs((b[3] - b[1]) - 900) <= 100 for b in found)


def test_three_lines_and_a_small_square_are_not_an_island(rules):
    def shapes(d):
        d.line([(100, 100), (330, 100)], fill=255, width=3)
        d.line([(100, 100), (100, 190)], fill=255, width=3)
        d.line([(330, 100), (330, 190)], fill=255, width=3)  # no bottom edge
        d.rectangle([400, 100, 440, 140], outline=255, width=3)  # 400 mm square: a sink, not a bench

    assert kitchen._boxes(_ink(shapes), 10.0, rules) == []


# --- close-up areas ------------------------------------------------------------------------------------
def _room(rid, name, box, area):
    x0, y0, x1, y1 = box
    return {"id": rid, "name": name, "outline": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "area_m2": area}


def _reading():
    return {
        "rooms": [
            _room("BATH", "BATH", (1000, 1000, 3500, 3500), 6.2),
            _room("VAN", None, (3500, 1000, 5300, 2500), 2.7),
            _room("WC", "WC", (5300, 1000, 6200, 2500), 1.3),
            _room("ENS", "ENSUITE", (1000, 20000, 4000, 22000), 6.0),
            _room("HALL", "HALL", (6200, 1000, 8000, 12000), 20.0),
            _room("BED", "BED 3", (9000, 1000, 13000, 5000), 16.0),
        ],
        "connections": [
            {"opening": "O1", "item": "x", "a": "BATH", "b": "VAN"},
            {"opening": "O2", "item": "x", "a": "VAN", "b": "WC"},
            {"opening": "O3", "item": "x", "a": "VAN", "b": "HALL"},
        ],
        "openings": [
            {"id": "O1", "kind": "hinged_door", "frame": [3400, 1500, 3600, 2400], "confidence": "clear"},
            {"id": "O4", "kind": "window", "frame": [900, 1200, 1000, 3000], "confidence": "clear"},
        ],
        "recesses": [],
        "kitchen": [],
    }


def test_a_bath_vanity_and_wc_are_one_close_up_and_a_far_ensuite_is_another(rules):
    areas = focus.group_areas(_reading(), PLAN, rules)
    wet = [a for a in areas if a.name.startswith("Wet rooms")]
    assert len(wet) == 2
    near = next(a for a in wet if "Bath" in a.name)
    assert {"BATH", "VAN", "WC", "O1"} <= set(
        near.items
    )  # the small unlabelled vanity joined by a door comes along
    assert "HALL" not in near.items  # a hall is bigger than a vanity: not pulled in
    assert (
        near.box[0] <= 1000 and near.box[2] >= 6200 and near.box[3] <= 5500
    )  # the cluster, not the whole plan
    assert max(a.box[2] - a.box[0] for a in wet) <= rules.tiles.focus.max_mm


def test_a_close_up_is_never_smaller_than_the_minimum_or_outside_the_plan(rules):
    box = focus.fit((100.0, 100.0, 300.0, 300.0), PLAN, rules)
    f = rules.tiles.focus
    assert box[2] - box[0] >= f.min_mm and box[3] - box[1] >= f.min_mm
    assert box[0] >= PLAN[0] and box[1] >= PLAN[1] and box[2] <= PLAN[2] and box[3] <= PLAN[3]


def test_items_the_code_is_unsure_of_get_a_close_up_unless_a_group_already_covers_them(rules):
    reading = _reading()
    reading["openings"].append(
        {"id": "O9", "kind": "arch", "frame": [14000, 25000, 14900, 25100], "confidence": "unsure"}
    )
    reading["openings"].append(
        {"id": "O8", "kind": "arch", "frame": [4000, 1500, 4100, 2400], "confidence": "unsure"}
    )  # inside the wet cluster's area
    groups = focus.group_areas(reading, PLAN, rules)
    spots = focus.hotspot_areas(reading, PLAN, rules, groups)
    assert [s.items for s in spots] == [["O9"]]


def test_overlapping_close_ups_are_merged_and_capped(rules):
    big = focus.Area("big", (0.0, 0.0, 9000.0, 9000.0), "", "group")
    inside = focus.Area("inside", (1000.0, 1000.0, 5000.0, 5000.0), "", "group")
    apart = focus.Area("apart", (12000.0, 12000.0, 16000.0, 16000.0), "", "group")
    assert [a.name for a in focus.merge([big, inside, apart], rules)] == ["big", "apart"]
    many = [focus.Area(str(i), (i * 1000.0, 0.0, i * 1000.0 + 500.0, 500.0), "", "group") for i in range(30)]
    assert len(focus.merge(many, rules)) == rules.tiles.focus.max_areas


SCHEMA = json.loads((ROOT / "schemas" / "plan_focus.json").read_text(encoding="utf-8"))


def _overview(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "overview_overlay.png"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(path)
    return path


def test_the_models_suggested_areas_are_checked_by_code(tmp_path, rules):
    answer = {
        "areas": [
            {
                "name": "Garage door",
                "box": [0.1, 0.8, 0.4, 0.95],
                "reason": "Door to house.",
                "item_ids": ["O1"],
            },
            {"name": "Backwards", "box": [0.6, 0.6, 0.2, 0.2], "reason": "x", "item_ids": []},
            {"name": "Off page", "box": [-3.0, -3.0, 4.0, 4.0], "reason": "y", "item_ids": []},
        ],
        "notes": "",
    }
    llm = FakeLLM({"plan_focus.claude": answer})
    got = focus.ask_model(llm, "claude", SCHEMA, tmp_path, _overview(tmp_path), "prompt", PLAN, rules)
    assert [a.name for a in got] == ["Garage door", "Off page"]  # a back-to-front box is no area
    garage, off = got
    assert garage.source == "model" and garage.items == ["O1"]
    assert PLAN[0] <= garage.box[0] and garage.box[2] <= PLAN[2]
    assert PLAN[1] <= garage.box[1] <= garage.box[3] <= PLAN[3]
    assert off.box[2] - off.box[0] <= rules.tiles.focus.max_mm  # clamped, not the whole plan


def test_the_model_cannot_ask_for_more_close_ups_than_the_rules_allow(tmp_path, rules):
    many = [{"name": f"A{i}", "box": [0.1, 0.1, 0.3, 0.3], "reason": "r", "item_ids": []} for i in range(10)]
    llm = FakeLLM({"plan_focus.claude": {"areas": many, "notes": ""}})
    got = focus.ask_model(llm, "claude", SCHEMA, tmp_path, _overview(tmp_path), "p", PLAN, rules)
    assert len(got) == rules.tiles.focus.llm_max_areas


def test_the_models_suggestions_are_cached_and_a_failed_call_adds_nothing(tmp_path, rules):
    llm = FakeLLM({"plan_focus.claude": {"areas": [], "notes": ""}})
    focus.ask_model(llm, "claude", SCHEMA, tmp_path, _overview(tmp_path), "p", PLAN, rules)
    focus.ask_model(llm, "claude", SCHEMA, tmp_path, _overview(tmp_path), "p", PLAN, rules)
    assert len(llm.calls) == 1
    err = PermanentError(code="LLM_SCHEMA_INVALID", what_happened="bad")
    other = tmp_path / "b"
    assert focus.ask_model(_Raises(err), "claude", SCHEMA, other, _overview(other), "p", PLAN, rules) == []


def test_the_focus_prompt_lists_what_is_chosen_and_the_items_as_data(rules):
    prompt = focus.model_prompt(ROOT / "private" / "prompts", PLAN, _reading(), [], 100, rules)
    assert "<<<BEGIN OVERLAY_ITEMS" in prompt and "<<<BEGIN CHOSEN_AREAS" in prompt and '"BATH"' in prompt


def test_write_focus_saves_each_close_up_plain_and_overlaid(tmp_path, rules):
    box = tiles.plan_box(WALLS, rules)
    index = tiles.write(tmp_path, _Blank(), box, WALLS, READING, rules)
    area = focus.Area("Bed 1 and its door", (0.0, 0.0, 4000.0, 4000.0), "Rooms: Bed 1", "group", ["R1"])
    (entry,) = tiles.write_focus(tmp_path, _Blank(), index, [area], WALLS, READING, rules)
    assert (tmp_path / entry["plain"]).is_file() and (tmp_path / entry["overlay"]).is_file()
    assert entry["mm_per_px"] == rules.tiles.focus.px_mm  # a small area is cut at the close-up detail
    assert json.loads((tmp_path / "tiles.json").read_text())["focus"][0]["title"] == "Bed 1 and its door"


def test_a_close_up_finding_asks_the_close_up_question(rules):
    entry = {"name": "focus-1", "title": "Wet rooms", "reason": "Rooms: Bath", "box_mm": [0, 0, 8000, 8000]}
    (row,) = verify.finding_rows([(entry, {"findings": [FINDING], "notes": ""})], rules)
    assert row["question"] == rules.tiles.focus.llm_question
