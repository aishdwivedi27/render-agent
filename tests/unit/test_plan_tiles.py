"""Cropped plan views, the model's overlay check, angled walls, bi-folds and written-size checks
(PLAN_VISUALS_SPEC section 3a). Synthetic drawings cover each rule; nothing here calls a model or a vendor."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from PIL import Image, ImageDraw

from render_agent.errors import PauseJobError, PermanentError
from render_agent.plan import angled, openings, rows, tiles, vector_walls, verify
from render_agent.plan.openings import Features, Gap, Opening
from render_agent.plan.source import Word
from render_agent.plan.trace_rules import load_trace_rules
from tests.fakes import FakeLLM

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rules():
    return load_trace_rules(ROOT / "private" / "knowledge")


# --- angled walls --------------------------------------------------------------------------------------
def _room_with(draw_extra) -> Image.Image:
    img = Image.new("L", (700, 500), 255)  # 10 mm per pixel
    d = ImageDraw.Draw(img)
    d.rectangle([50, 50, 650, 70], fill=0)
    d.rectangle([50, 50, 70, 450], fill=0)
    d.rectangle([50, 430, 300, 450], fill=0)
    d.rectangle([640, 50, 660, 250], fill=0)
    draw_extra(d)
    return img


def test_a_splayed_wall_is_found_with_its_angle(rules):
    img = _room_with(lambda d: d.line([(300, 440), (640, 200)], fill=0, width=22))
    vp = vector_walls.from_image(img, 10.0, 100, rules.walls)
    (wall,) = angled.find(vp, rules)
    assert wall.angle_deg == pytest.approx(35, abs=4)  # atan(240 / 340)
    assert wall.length_mm > 1000
    (row,) = angled.rows([wall], rules)
    assert row["kind"] == "angled" and row["confidence"] == "orange" and row["question"]


def test_square_walls_and_a_wall_corner_are_not_angled(rules):
    vp = vector_walls.from_image(_room_with(lambda d: None), 10.0, 100, rules.walls)
    assert angled.find(vp, rules) == []


def test_an_angled_row_becomes_an_orange_wall_row_with_a_question(rules):
    img = _room_with(lambda d: d.line([(300, 440), (640, 200)], fill=0, width=22))
    vp = vector_walls.from_image(img, 10.0, 100, rules.walls)
    (row,) = rows.wall_rows(angled.rows(angled.find(vp, rules), rules), rules)
    assert row["confidence"] == "ORANGE" and row["category"] == "wall" and row["question"]


# --- sizes written on the drawing ----------------------------------------------------------------------
def _opening(kind: str, width: float, frame=None, axis="v", code=None, conf="clear") -> Opening:
    frame = frame or (1000.0, 1000.0, 1100.0, 1000.0 + width)
    return Opening("O1", kind, None, conf, "evidence", Gap(frame, axis), Features(), code=code)


def _word(text: str, x: float, y: float) -> Word:
    return Word(x - 100, y - 60, x + 100, y + 60, text)


def test_a_door_leaf_size_that_fits_the_gap_agrees(rules):
    door = _opening("hinged_door", 930)  # 870 leaf: about 930 opening
    openings.check_leaf_sizes([door], [_word("870", 1500, 1400)], rules)
    assert (door.leaf_mm, door.size_check, door.confidence) == (870, "matches", "clear")


def test_a_door_leaf_size_far_from_the_gap_is_orange_and_says_so(rules):
    door = _opening("hinged_door", 1300)
    openings.check_leaf_sizes([door], [_word("870", 1500, 1400)], rules)
    assert door.size_check == "differs" and door.confidence == "unsure"
    assert "870" in door.evidence and "1300" in door.evidence


def test_two_different_leaf_sizes_beside_one_door_compare_nothing(rules):
    door = _opening("hinged_door", 930)
    openings.check_leaf_sizes([door], [_word("870", 1500, 1400), _word("720", 1500, 1500)], rules)
    assert door.size_check is None and door.leaf_mm is None


def test_a_dimension_number_is_not_a_leaf_size(rules):
    door = _opening("hinged_door", 930)
    openings.check_leaf_sizes([door], [_word("3000", 1500, 1400)], rules)  # a room dimension
    assert door.leaf_mm is None


def test_a_window_code_is_checked_against_its_gap(rules):
    good, bad = _opening("window", 1780, code="15-18"), _opening("window", 1000, code="15-18")
    openings._check_code_width(good, rules)
    openings._check_code_width(bad, rules)
    assert good.size_check == "matches" and good.confidence == "clear"
    assert bad.size_check == "differs" and bad.confidence == "unsure" and "1800" in bad.evidence


# --- bi-folds ------------------------------------------------------------------------------------------
def test_leaves_beside_a_bifold_label_become_one_bifold_door(rules):
    a = _opening("cavity_slider", 650, (1000, 1000, 1650, 1115), "h", conf="unsure")
    b = _opening("cavity_slider", 650, (1000, 1700, 1650, 1815), "h", conf="unsure")
    words = [_word("2/1020", 500, 1400), _word("Bi-folds", 500, 1000)]
    (merged,) = openings.merge_bifolds([a, b], words, rules)
    assert merged.kind == "bifold_door" and merged.confidence == "clear" and merged.leaf_mm == 1020
    assert merged.gap.frame == (1000, 1000, 1650, 1815)
    assert "2/1020 Bi-folds" in merged.evidence


def test_a_clear_door_beside_a_bifold_label_stays_a_door(rules):
    door = _opening("hinged_door", 930, (1000, 1000, 1100, 1930))
    leaf = _opening("cavity_slider", 650, (1000, 2200, 1650, 2315), "h", conf="unsure")
    words = [_word("2/1020", 500, 1400), _word("Bi-folds", 500, 1000)]
    assert openings.merge_bifolds([door, leaf], words, rules) == [door, leaf]


def test_a_bifold_word_without_a_label_changes_nothing(rules):
    a = _opening("cavity_slider", 650, (1000, 1000, 1650, 1115), "h", conf="unsure")
    b = _opening("cavity_slider", 650, (1000, 1700, 1650, 1815), "h", conf="unsure")
    assert len(openings.merge_bifolds([a, b], [_word("Bi-folds", 500, 1000)], rules)) == 2


# --- tiles ---------------------------------------------------------------------------------------------
WALLS = [(0.0, 0.0, 20000.0, 200.0), (0.0, 0.0, 200.0, 12000.0), (0.0, 11800.0, 20000.0, 12000.0)]


def test_the_plan_box_is_the_walls_plus_the_margin_inside_the_page(rules):
    box = tiles.plan_box(WALLS, rules, (21000.0, 12300.0))
    assert box == (0.0, 0.0, 20600.0, 12300.0)
    assert tiles.plan_box([], rules) is None


def test_tiles_cover_the_plan_and_overlap(rules):
    box = tiles.plan_box(WALLS, rules)
    cut = tiles.zone_tiles(box, WALLS, rules)
    assert 1 < len(cut) <= rules.tiles.max_tiles
    assert min(t.box[0] for t in cut) == box[0] and max(t.box[2] for t in cut) == box[2]
    assert min(t.box[1] for t in cut) == box[1] and max(t.box[3] for t in cut) == box[3]
    xs = sorted({t.box[0] for t in cut})
    width = cut[0].box[2] - cut[0].box[0]
    assert all(b - a < width for a, b in zip(xs, xs[1:], strict=False))  # neighbours overlap


def test_a_tile_with_almost_no_wall_is_not_cut(rules):
    walls = [(0.0, 0.0, 20000.0, 200.0), (19000.0, 0.0, 19200.0, 300.0)]
    box = (0.0, 0.0, 20000.0, 30000.0)
    cut = tiles.zone_tiles(box, walls, rules)
    assert cut and all(t.box[1] < 8000 for t in cut)  # nothing is cut down in the empty half


def test_a_small_plan_is_one_tile(rules):
    walls = [(0.0, 0.0, 4000.0, 200.0), (0.0, 0.0, 200.0, 4000.0)]
    assert len(tiles.zone_tiles((0.0, 0.0, 4000.0, 4000.0), walls, rules)) == 1


def test_a_position_in_a_tile_converts_back_to_the_page():
    assert tiles.to_page((1000.0, 2000.0, 3000.0, 6000.0), (0.5, 0.25)) == (2000.0, 3000.0)
    assert tiles.to_page((1000.0, 2000.0, 3000.0, 6000.0), (1.4, -0.2)) == (3000.0, 2000.0)  # clamped


class _Blank:
    """A source that draws a white page: what a tile is cut from."""

    def crop(self, x0, y0, x1, y1, px_mm):
        return Image.new("L", (max(1, round((x1 - x0) / px_mm)), max(1, round((y1 - y0) / px_mm))), 255)


READING = {
    "rooms": [
        {
            "id": "R1",
            "name": "BED 1",
            "area_m2": 9.0,
            "outline": [[400, 400], [3400, 400], [3400, 3400], [400, 3400]],
        }
    ],
    "openings": [
        {
            "id": "O1",
            "kind": "hinged_door",
            "alternative": None,
            "frame": [3400, 1000, 3500, 1900],
            "width_mm": 900,
        }
    ],
}


def test_write_saves_each_tile_plain_and_with_the_overlay(tmp_path, rules):
    box = tiles.plan_box(WALLS, rules)
    index = tiles.write(tmp_path / "tiles", _Blank(), box, WALLS, READING, rules)
    assert index["tiles"]
    for t in index["tiles"]:
        plain, over = tmp_path / "tiles" / t["plain"], tmp_path / "tiles" / t["overlay"]
        assert max(Image.open(plain).size) <= rules.tiles.max_edge_px
        assert plain.read_bytes() != over.read_bytes()  # the overlay drew something
    assert json.loads((tmp_path / "tiles" / "tiles.json").read_text())["box_mm"] == index["box_mm"]


def test_the_overlay_draws_walls_rooms_and_openings(rules):
    plain = Image.new("RGB", (400, 400), (255, 255, 255))
    out = tiles.overlay(plain, (0.0, 0.0, 4000.0, 4000.0), 10.0, [(0.0, 0.0, 4000.0, 200.0)], READING, rules)
    wall_colour = out.getpixel((200, 10))
    assert (
        wall_colour != (255, 255, 255) and wall_colour[0] > wall_colour[2]
    )  # warm wall colour, half transparent
    assert out.getpixel((340, 130)) != (255, 255, 255)  # the opening's box edge


def test_items_in_a_tile_are_the_rooms_and_openings_inside_it():
    inside = tiles.items_in(READING, (0.0, 0.0, 4000.0, 4000.0))
    assert [i["id"] for i in inside] == ["R1", "O1"]
    assert tiles.items_in(READING, (5000.0, 5000.0, 9000.0, 9000.0)) == []


# --- the model's check of a tile -----------------------------------------------------------------------
@pytest.fixture
def schema():
    return json.loads((ROOT / "schemas" / "plan_verify.json").read_text(encoding="utf-8"))


@pytest.fixture
def tile_files(tmp_path, rules):
    box = tiles.plan_box(WALLS, rules)
    index = tiles.write(tmp_path, _Blank(), box, WALLS, READING, rules)
    return tmp_path, index["tiles"][0]


FINDING = {
    "kind": "wall_missing",
    "at": [0.5, 0.25],
    "item_id": None,
    "what": "A wall band is drawn here with no overlay.",
}


def _prompt(entry, rules):
    return verify.build_prompt(
        ROOT / "private" / "prompts", ROOT / "private" / "knowledge", entry, 100, [], rules
    )


def test_the_check_prompt_labels_the_overlay_items_and_rules_as_data(tile_files, rules):
    _, entry = tile_files
    prompt = _prompt(entry, rules)
    assert "<<<BEGIN OVERLAY_ITEMS" in prompt and "<<<BEGIN DRAWING_RULES" in prompt
    assert entry["name"] in prompt and "wall_missing" in prompt


def test_a_finding_is_an_orange_wall_row_with_a_question_and_a_box(tile_files, rules, schema):
    folder, entry = tile_files
    llm = FakeLLM({f"plan_verify.claude.{entry['name']}": {"findings": [FINDING], "notes": ""}})
    got = verify.check_tile(llm, "claude", schema, folder, entry, _prompt(entry, rules))
    jsonschema.validate(got, schema)
    (row,) = verify.finding_rows([(entry, got)], rules)
    assert row["confidence"] == "ORANGE" and row["category"] == "wall" and row["question"]
    box = row["target_mm"]
    x0, y0, x1, y1 = entry["box_mm"]
    assert box[0] < x0 + 0.5 * (x1 - x0) < box[2] and box[1] < y0 + 0.25 * (y1 - y0) < box[3]
    assert "wall band is drawn here" in row["evidence"]
    assert [i["key"] for i in row["options"]] == ["confirm", "not_there"]


def test_the_same_finding_seen_from_two_overlapping_tiles_is_one_row(rules):
    a = {"name": "tile-1", "box_mm": [0, 0, 8000, 8000]}
    b = {"name": "tile-2", "box_mm": [4000, 0, 12000, 8000]}
    # (0.75, 0.5) of tile 1 and (0.25, 0.5) of tile 2 are both the point x = 6000, y = 4000
    one = {"findings": [{**FINDING, "at": [0.75, 0.5]}], "notes": ""}
    two = {"findings": [{**FINDING, "at": [0.25, 0.5]}], "notes": ""}
    assert len(verify.finding_rows([(a, one), (b, two)], rules)) == 1


def test_an_unknown_finding_kind_is_other_and_long_text_is_cut(rules):
    entry = {"name": "tile-1", "box_mm": [0, 0, 8000, 8000]}
    reading = {"findings": [{**FINDING, "kind": "banana", "what": "x " * 400}], "notes": ""}
    (row,) = verify.finding_rows([(entry, reading)], rules)
    assert row["id"].startswith("check_other_")
    assert len(row["evidence"]) < len(rules.tiles.finding_kinds["other"]) + rules.tiles.max_what_chars + 5


def test_a_model_can_only_add_questions_never_green_rows(rules):
    entry = {"name": "tile-1", "box_mm": [0, 0, 8000, 8000]}
    reading = {"findings": [FINDING] * 20, "notes": ""}
    out = verify.finding_rows([(entry, reading)], rules)
    assert len(out) == 1  # the same finding twenty times is one row
    assert {r["confidence"] for r in out} == {"ORANGE"}


def test_findings_per_tile_are_capped(rules):
    entry = {"name": "tile-1", "box_mm": [0, 0, 80000, 80000]}
    many = [{**FINDING, "at": [i / 20, 0.5]} for i in range(20)]
    out = verify.finding_rows([(entry, {"findings": many, "notes": ""})], rules)
    assert len(out) == rules.tiles.max_findings


def test_a_checked_tile_is_cached_so_retry_never_pays_twice(tile_files, rules, schema):
    folder, entry = tile_files
    llm = FakeLLM({f"plan_verify.claude.{entry['name']}": {"findings": [FINDING], "notes": ""}})
    prompt = _prompt(entry, rules)
    first = verify.check_tile(llm, "claude", schema, folder, entry, prompt)
    second = verify.check_tile(llm, "claude", schema, folder, entry, prompt)
    assert first == second and len(llm.calls) == 1


def test_a_changed_overlay_is_checked_again(tile_files, rules, schema):
    folder, entry = tile_files
    llm = FakeLLM()
    prompt = _prompt(entry, rules)
    verify.check_tile(llm, "claude", schema, folder, entry, prompt)
    (folder / entry["overlay"]).write_bytes((folder / entry["overlay"]).read_bytes() + b"x")
    verify.check_tile(llm, "claude", schema, folder, entry, prompt)
    assert len(llm.calls) == 2


class _Raises:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def call_json(self, *args):
        raise self.error


def test_a_tile_the_model_cannot_read_is_skipped_not_fatal(tile_files, rules, schema):
    folder, entry = tile_files
    err = PermanentError(code="LLM_SCHEMA_INVALID", what_happened="bad")
    assert verify.check_tile(_Raises(err), "claude", schema, folder, entry, _prompt(entry, rules)) is None


def test_a_credit_or_key_problem_pauses_the_job(tile_files, rules, schema):
    folder, entry = tile_files
    err = PauseJobError(code="OUT_OF_CREDIT", what_happened="x", likely_cause="y", what_to_do="z")
    with pytest.raises(PauseJobError):
        verify.check_tile(_Raises(err), "claude", schema, folder, entry, _prompt(entry, rules))


def test_the_default_answer_of_a_model_with_nothing_to_report_is_valid_and_empty(schema):
    from render_agent.llm.offline import minimal_instance

    empty = minimal_instance(schema)
    jsonschema.validate(empty, schema)
    assert empty["findings"] == []
