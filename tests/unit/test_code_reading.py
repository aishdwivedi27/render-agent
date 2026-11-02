"""The code's own reading of a floor plan (PLAN_VISUALS_SPEC section 3): wall outlines, openings, rooms,
recesses and the overlay check against the drawing. Synthetic cases cover each rule; one shared reading of
the vector test drawing covers the whole path."""

from __future__ import annotations

from pathlib import Path

import pytest

from render_agent.plan import code_reading, overlay_check, recesses, rooms, vector_walls
from render_agent.plan.source import PdfSource, Word, page_words
from render_agent.plan.trace_rules import TraceRules, load_trace_rules

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_03 = ROOT / "private" / "fixtures" / "fixture_03" / "input"


@pytest.fixture(scope="module")
def rules() -> TraceRules:
    return load_trace_rules(ROOT / "private" / "knowledge")


def _room(outline: list[tuple[float, float]], labels: list[str], rects=None) -> rooms.Room:
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    area = (max(xs) - min(xs)) * (max(ys) - min(ys)) / 1e6
    r = rooms.Room("R1", outline, area, labels, False, rects or [(min(xs), min(ys), max(xs), max(ys))])
    return r


# --- wall outlines -------------------------------------------------------------------------------
def test_outline_keeps_every_jog_and_return():
    # an L: two rectangles that touch along an edge
    rects = [(0.0, 0.0, 300.0, 100.0), (0.0, 100.0, 100.0, 300.0)]
    ((points, hole),) = vector_walls.outlines(rects, 10.0, 0.0)
    assert not hole and len(points) == 6
    assert {(300.0, 100.0), (100.0, 100.0), (100.0, 300.0)} <= set(points)


def test_a_room_enclosed_by_walls_is_a_hole():
    ring = [
        (0.0, 0.0, 300.0, 100.0),
        (0.0, 200.0, 300.0, 300.0),
        (0.0, 100.0, 100.0, 200.0),
        (200.0, 100.0, 300.0, 200.0),
    ]
    found = vector_walls.outlines(ring, 10.0, 0.0)
    assert sorted(hole for _, hole in found) == [False, True]


def test_steps_shorter_than_the_jog_limit_are_noise():
    rects = [(0.0, 0.0, 500.0, 100.0), (500.0, 0.0, 510.0, 50.0)]  # a 10 mm stub on the end
    ((points, _),) = vector_walls.outlines(rects, 10.0, 30.0)
    assert len(points) == 4


# --- pockets and recess kinds ------------------------------------------------------------------
BED = [(0.0, 0.0), (5000.0, 0.0), (5000.0, 1900.0), (4400.0, 1900.0), (4400.0, 3500.0), (0.0, 3500.0)]


def test_a_step_in_a_bedroom_wall_is_a_robe(rules):
    room = _room(BED, ["BED 3"])
    (box,) = recesses.pockets(room, rules)
    assert box == (4400.0, 0.0, 5000.0, 1900.0)
    kind, alternative, confidence, fits = recesses.classify(600.0, 1900.0, room, rules)
    assert (kind, confidence, fits) == ("robe", recesses.CLEAR, True)


def test_the_same_pocket_in_an_unnamed_space_is_other_with_the_kind_as_alternative(rules):
    kind, alt, confidence, fits = recesses.classify(600.0, 1900.0, _room(BED, []), rules)
    assert (kind, alt, confidence, fits) == ("other", "robe", recesses.UNSURE, True)


def test_a_pocket_that_fits_no_kind_of_its_room_is_not_called_a_robe(rules):
    kind, alt, _c, _f = recesses.classify(620.0, 1150.0, _room(BED, ["ENSUITE"]), rules)
    assert kind == "other" and alt in rules.recesses


def test_a_nib_suits_every_room(rules):
    kind, alt, confidence, _f = recesses.classify(290.0, 1980.0, _room(BED, ["DINING", "KITCHEN"]), rules)
    assert (kind, alt, confidence) == ("nib", None, recesses.CLEAR)


def test_a_depth_outside_every_band_is_orange_with_two_nearest_kinds(rules):
    kind, alternative, confidence, fits = recesses.classify(2000.0, 2500.0, _room(BED, ["BED 1"]), rules)
    assert not fits and confidence == recesses.UNSURE and alternative and kind != alternative


def test_the_arm_of_an_l_shaped_room_is_its_body_not_a_pocket(rules):
    room = _room([(0, 0), (6000, 0), (6000, 1500), (1500, 1500), (1500, 6000), (0, 6000)], ["FAMILY"])
    assert recesses.pockets(room, rules) == []


# --- room names ---------------------------------------------------------------------------------
def _w(text: str, x: float, y: float = 1000.0) -> Word:
    return Word(x, y, x + 60 * len(text), y + 120, text)


def test_labels_join_words_on_a_line_and_expand_abbreviations(rules):
    found = rooms.labels(
        [_w("BED", 0), _w("3", 280), _w("KIT", 5000), _w("2800", 9000), _w("&", 9300)], rules
    )
    assert [lb.text for lb in found] == ["BED 3", "KITCHEN"]


def test_a_trailing_ampersand_is_not_part_of_a_name(rules):
    assert [lb.text for lb in rooms.labels([_w("WIR", 0), _w("&", 300)], rules)] == ["WALK-IN ROBE"]


# --- the overlay check against the drawing -----------------------------------------------------
def _page(band_boxes, trace_boxes, px=10.0, size=(200, 200)) -> vector_walls.VectorPage:
    def paint(boxes) -> bytes:
        data = bytearray(b"\xff" * (size[0] * size[1]))
        for x0, y0, x1, y1 in boxes:
            for y in range(int(y0 / px), int(y1 / px)):
                data[y * size[0] + int(x0 / px) : y * size[0] + int(x1 / px)] = b"\x00" * (
                    int(x1 / px) - int(x0 / px)
                )
        return bytes(data)

    vp = vector_walls.VectorPage(size[0] * px, size[1] * px, 35.0, list(trace_boxes), px_mm=px, size=size)
    vp.band, vp.raster = paint(band_boxes), paint(trace_boxes)
    return vp


def test_a_wall_band_that_was_not_traced_becomes_an_orange_wall_row(rules):
    wall = (200.0, 200.0, 1400.0, 300.0)
    extra = (700.0, 600.0, 1000.0, 700.0)  # drawn, not traced
    vp = _page([wall, extra], [wall, (200.0, 1400.0, 1400.0, 1500.0)])
    found = overlay_check.wall_rows(vp, [], rules)
    assert [(r["kind"], r["confidence"]) for r in found if r["kind"] == "missing"] == [("missing", "orange")]
    assert rules.overlay_check.questions["missing"] in found[0]["question"]


def test_a_trace_with_no_band_under_it_is_reported(rules):
    wall = (200.0, 200.0, 1400.0, 300.0)
    ghost = (700.0, 800.0, 1000.0, 900.0)
    far = (200.0, 1400.0, 1400.0, 1500.0)
    found = overlay_check.wall_rows(_page([wall, far], [wall, far, ghost]), [], rules)
    assert any(r["kind"] == "extra" for r in found)


def test_differences_within_the_tolerance_are_ignored(rules):
    wall = (200.0, 200.0, 1400.0, 300.0)
    assert overlay_check.wall_rows(_page([wall], [wall]), [], rules) == []


def test_a_wall_between_two_rooms_makes_the_row_red(rules):
    left = _room([(0, 0), (800, 0), (800, 1800), (0, 1800)], ["BED 1"])
    left.id = "A"
    right = _room([(1200, 0), (2000, 0), (2000, 1800), (1200, 1800)], ["BED 2"])
    right.id = "B"
    wall = (200.0, 200.0, 1400.0, 300.0)
    far = (200.0, 1400.0, 1400.0, 1500.0)
    vp = _page([wall, far, (900.0, 600.0, 1100.0, 1100.0)], [wall, far])
    red = [r for r in overlay_check.wall_rows(vp, [left, right], rules) if r["confidence"] == "red"]
    assert red


def test_distance_to_a_drawn_wall(rules):
    vp = _page([(500.0, 500.0, 600.0, 900.0)], [(500.0, 500.0, 600.0, 900.0)])
    assert overlay_check.off_mm(vp, 520.0, 700.0, 300.0) == 0.0
    assert 180.0 <= overlay_check.off_mm(vp, 800.0, 700.0, 300.0) <= 220.0
    assert overlay_check.off_mm(vp, 1500.0, 700.0, 300.0) == 300.0


# --- the whole path on the vector test drawing -------------------------------------------------
@pytest.fixture(scope="module")
def reading(rules):
    pdf = next(FIXTURE_03.glob("*.pdf"))
    vp = vector_walls.extract(pdf, 2, 100, rules.walls)
    with PdfSource(pdf, 2, 100, rules.walls) as source:
        return code_reading.read_page(vp, source, page_words(pdf, 2, 100), rules)


def test_openings_are_classified_from_the_drawing(reading):
    by = {k: [o for o in reading.openings if o.kind == k] for k in {o.kind for o in reading.openings}}
    coded = [o for o in by["window"] if o.code]
    assert len(coded) >= 9 and all(
        o.code_note and o.confidence == "unsure" for o in by["window"] if not o.code
    )
    assert len(by["hinged_door"]) >= 7
    assert all(o.features.arc >= 0.45 for o in by["hinged_door"])
    clear_cavity = [o for o in by["cavity_slider"] if o.confidence == "clear"]
    assert len(by["garage_door"]) == 1 and len(clear_cavity) == 1  # the "csd" one; a lone line is Orange
    assert all(o.alternative == "hinged_door" for o in by["cavity_slider"] if o.confidence != "clear")
    assert {o.code for o in by["sliding_door"]} == {"24-24", "24-15"}


def test_a_hinged_door_names_its_hinge_end_and_swing_side(reading):
    door = next(o for o in reading.openings if o.kind == "hinged_door")
    text = door.evidence
    assert "hinge at the" in text and door.features.arc_side in (-1, 1)


def test_ambiguous_breaks_are_orange_with_an_alternative(reading):
    for o in reading.openings:
        if o.confidence != "clear":
            assert o.alternative or o.code_note or o.gap.meets_wall or o.gap.across


def test_rooms_are_the_enclosed_spaces_named_from_their_labels(reading):
    names = {r.name for r in reading.rooms if r.name}
    assert {"BED 2", "BED 3", "BED 4", "LAUNDRY", "MUD", "GARAGE", "ALFRESCO", "PORCH"} <= names
    assert (
        len([r for r in reading.rooms if not r.name]) >= 1
    )  # unlabelled spaces are kept, for the table to ask
    assert all(len(r.outline) >= 4 and r.area_m2 >= 1.0 for r in reading.rooms)
    assert {u["room"] for u in reading.unlabelled} == {r.id for r in reading.rooms if not r.name}


def test_a_room_outline_follows_the_wall_faces(reading):
    # every corner of every room is within the overlay tolerance of a drawn wall
    worst = max(reading.offsets[r.id] for r in reading.rooms if not r.outdoor)  # posts, not walls, edge those
    assert worst <= 150.0


def test_robes_are_found_as_wall_jogs_and_as_a_drawn_rectangle(reading):
    robes = [r for r in reading.recesses if r.kind == "robe"]
    assert {r.source for r in robes} == {"wall_jog", "drawn_rectangle"}
    assert all(r.confidence == "unsure" for r in robes if r.source == "drawn_rectangle")


def test_connections_are_named_room_to_room(reading):
    assert reading.connections and all(" -> " in c["item"] for c in reading.connections)


def test_the_reading_is_json_and_a_region_keeps_only_its_part(reading):
    page = reading.as_dict()
    box = reading.rooms[0].box()
    part = code_reading.for_region(page, (box[0], box[1], box[2], box[3]))
    assert 0 < len(part["rooms"]) < len(page["rooms"])
    assert {c["opening"] for c in part["connections"]} <= {o["id"] for o in part["openings"]}
