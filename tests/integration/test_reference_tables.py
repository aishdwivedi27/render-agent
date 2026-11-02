"""The code's reading of the reference drawings against the tables people approved for them.

fixture4a and fixture4b each hold a drawing PDF and, in output/, the approved interpretation table (.md)
with its overlay picture (.png, .svg). The code reads each PDF with no model and no cost;
`scripts/compare_reference.py` counts what the table lists against what the code found and writes
docs/parity/<fixture>.md with a picture.

These tests fix today's agreement as a floor (a regression fails) and state the known gaps as ceilings (an
improvement passes), so the gaps are written down here and in the report, not hidden.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from render_agent.plan.trace_rules import load_trace_rules

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "private" / "fixtures"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "fixture4a" / "output").is_dir(), reason="reference fixtures are in the private submodule"
)


def _script():
    spec = importlib.util.spec_from_file_location(
        "compare_reference", ROOT / "scripts" / "compare_reference.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses looks its own module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def results():
    script = _script()
    rules = load_trace_rules(ROOT / "private" / "knowledge")
    return {name: script.compare(name, rules) for name in ("fixture4a", "fixture4b")}, script


def test_reference_files_have_a_table_a_picture_and_a_vector_drawing():
    for name in ("fixture4a", "fixture4b"):
        out = FIXTURES / name / "output"
        assert list(out.glob("*.md")) and list(out.glob("*.png")) and list(out.glob("*.svg"))
        assert list((FIXTURES / name).glob("*.pdf"))


def test_the_tables_are_complete(results):
    found, _ = results
    a, b = found["fixture4a"]["reference"], found["fixture4b"]["reference"]
    assert len(a.items) == a.stated_total == 63
    assert dict(a.stated) == {"Green": 53, "Orange": 8, "Red": 2}
    # the 4b table file stops at item 38; items 39-58 come from the supplement file beside it
    assert len(b.items) == b.stated_total == 58
    assert dict(b.stated) == {"Green": 47, "Orange": 10, "Red": 1}
    assert {i.number for i in b.items} == set(range(1, 59))


def test_4a_everything_the_table_lists_is_found(results):
    (found, _) = results
    want, got = found["fixture4a"]["expected"], found["fixture4a"]["found"]
    assert got["hinged"] >= want["hinged"] == 10
    assert got["cavity_arch"] >= want["cavity_arch"] == 6
    assert got["walkways"] >= want["walkways"] == 1
    assert got["garage"] == want["garage"] == 1
    assert got["robes"] >= want["robes"] == 3
    assert sum(want["codes"].values()) == 13
    for code, n in want["codes"].items():  # every window and sliding door, with its code
        assert got["codes"][code] == n, code


def test_4a_extras_stay_few(results):
    (found, _) = results
    want, got = found["fixture4a"]["expected"], found["fixture4a"]["found"]
    assert got["hinged"] - want["hinged"] <= 1
    assert got["robes"] - want["robes"] <= 2


def test_4a_the_garage_front_and_the_csd_door_are_read_from_the_drawing(results):
    (found, _) = results
    kinds = {o["kind"]: o for o in found["fixture4a"]["page"]["openings"]}
    assert kinds["garage_door"]["width_mm"] > 3000 and kinds["garage_door"]["confidence"] == "unsure"
    cavity = [
        o
        for o in found["fixture4a"]["page"]["openings"]
        if o["kind"] == "cavity_slider" and o["confidence"] == "clear"
    ]
    assert len(cavity) == 1 and "csd" in cavity[0]["evidence"]


def test_4b_everything_the_table_lists_is_found(results):
    (found, _) = results
    want, got = found["fixture4b"]["expected"], found["fixture4b"]["found"]
    assert got["hinged"] == want["hinged"] == 10
    assert got["cavity_arch"] >= want["cavity_arch"] == 1  # one line across a door-width gap is Orange
    assert got["walkways"] >= want["walkways"] == 2
    assert got["garage"] == want["garage"] == 1
    assert sum(want["codes"].values()) == 11
    for code, n in want["codes"].items():  # every window and the sliding door, with its code
        assert got["codes"][code] == n, code


def test_4b_robes_are_found_by_room(results):
    (found, _) = results
    robes = [r for r in found["fixture4b"]["page"]["recesses"] if r["kind"] == "robe"]
    assert sorted(r["room_name"] for r in robes if r["room_name"] and r["room_name"].startswith("BED")) == [
        "BED 1",
        "BED 1",
        "BED 2",
        "BED 3",
    ]
    # the fifth robe (1700 x 600 in the room the plan only calls "D2") is not called a robe in a room that
    # is not a bedroom: it is a recess of the right size, Orange, with robe as the alternative
    pockets = [r for r in found["fixture4b"]["page"]["recesses"] if r["room_name"] is None]
    assert any(
        r["kind"] == "other"
        and r["alternative"] == "robe"
        and r["confidence"] == "unsure"
        and abs(r["depth_mm"] - 600) <= 50
        and abs(r["length_mm"] - 1700) <= 100
        for r in pockets
    )
    # the ensuite pocket is not a robe either (the reference's item 48 is an Orange ensuite pocket)
    assert not any(
        r["kind"] == "robe" and r["room_name"] == "ENSUITE" for r in found["fixture4b"]["page"]["recesses"]
    )


def test_4a_robes_are_measured_between_the_end_walls(results):
    (found, _) = results
    robes = [r for r in found["fixture4a"]["page"]["recesses"] if r["kind"] == "robe"]
    assert sorted(r["length_mm"] for r in robes) == pytest.approx([1790, 1810, 1900], abs=50)
    assert all(abs(r["depth_mm"] - 600) <= 20 for r in robes)
    # nothing in the open-plan room is called a robe (the reference has none there)
    assert not any(r["room_name"] and r["room_name"].startswith("FAMILY") for r in robes)


def test_4a_shower_trays_are_found_where_the_table_lists_them(results):
    (found, _) = results
    trays = [
        r
        for r in found["fixture4a"]["page"]["recesses"]
        if r["kind"] == "shower_recess" and r["source"] == "drawn_rectangle"
    ]
    by_room = {r["room_name"]: sorted((r["depth_mm"], r["length_mm"])) for r in trays}
    # the table: Bath shower about 1.15 x 0.85 m, Ens shower about 0.98 x 1.51 m
    assert by_room["BATH"] == pytest.approx([850, 1150], abs=100)
    assert by_room["ENSUITE"] == pytest.approx([980, 1510], abs=100)
    assert len(trays) == 2  # the Ens hatched rectangle (item 50) is not a shower


def test_hatched_squares_are_not_showers(results):
    (found, _) = results
    drawn = [r for r in found["fixture4b"]["page"]["recesses"] if r["source"] == "drawn_rectangle"]
    assert not any(r["kind"] == "shower_recess" for r in drawn)  # item 24: hatched squares, no drain


def test_4b_extras_stay_few(results):
    (found, _) = results
    want, got = found["fixture4b"]["expected"], found["fixture4b"]["found"]
    assert got["hinged"] == want["hinged"]
    assert got["walkways"] - want["walkways"] <= 2


def test_4b_the_laundry_bifold_is_one_door_not_sliders(results):
    (found, _) = results
    page = found["fixture4b"]["page"]
    (bifold,) = [o for o in page["openings"] if o["kind"] == "bifold_door"]
    assert bifold["leaf_mm"] == 1020 and bifold["confidence"] == "clear"
    assert "2/1020 Bi-folds" in bifold["evidence"]
    assert "2 leaves of 1020 mm" in bifold["evidence"]  # the written size, not a measured gap
    # the bi-fold's leaves no longer count as sliders (before: 4 sliders besides the csd door)
    assert found["fixture4b"]["found"]["cavity_arch"] <= 3


def test_written_sizes_agree_with_the_measured_gaps_on_both_drawings(results):
    """Window codes (15-18 is 1800 wide) and door leaf sizes (870 gives ~930) were all read from the
    drawing and none disagrees with the gap measured from the walls: a disagreement would be Orange."""
    (found, _) = results
    for name in ("fixture4a", "fixture4b"):
        checks = found[name]["found"]["size_checks"]
        assert checks["matches"] >= 20 and checks["differs"] == 0, name


def test_no_angled_wall_is_invented_on_square_drawings(results):
    (found, _) = results
    for name in ("fixture4a", "fixture4b"):
        assert not [w for w in found[name]["page"]["wall_rows"] if w["kind"] == "angled"], name


def test_the_code_reading_still_fits_its_schema_with_the_new_fields(results):
    import json

    import jsonschema

    (found, _) = results
    schema = json.loads((ROOT / "schemas" / "plan_code_reading.json").read_text(encoding="utf-8"))
    for name in ("fixture4a", "fixture4b"):
        jsonschema.validate(found[name]["page"], schema)


def test_code_names_the_rooms_the_table_names(results):
    (found, _) = results
    names_a = {r["name"] for r in found["fixture4a"]["page"]["rooms"] if r["name"]}
    assert {"BED 1", "BED 2", "BED 3", "BED 4", "LAUNDRY", "BATH", "ENSUITE", "LOUNGE", "PDR"} <= names_a
    # the table lists Family, Kitchen and Dining as separate items, so the open plan is a zone per label
    assert {"FAMILY", "KITCHEN", "DINING"} <= names_a
    names_b = {r["name"] for r in found["fixture4b"]["page"]["rooms"] if r["name"]}
    assert {"BED 1", "BED 2", "BED 3", "LAUNDRY", "BATH", "ENSUITE"} <= names_b


def test_the_report_and_the_picture_are_written(results, tmp_path):
    found, script = results
    for name, result in found.items():
        text = script.report(result)
        assert f"# Reference table vs code reading: {name}" in text and "| Hinged doors |" in text
        result["picture"].save(tmp_path / f"{name}.png")
        assert (tmp_path / f"{name}.png").stat().st_size > 10_000


# --- fixture_03: the floor plan first-pass table the owner provided -----------------------------------------
F3 = FIXTURES / "fixture_03"


@pytest.fixture(scope="module")
def f3():
    if not (F3 / "output").is_dir():
        pytest.skip("fixture_03 reference table is in the private submodule")
    script = _script()
    rules = load_trace_rules(ROOT / "private" / "knowledge")
    return script.compare("fixture_03", rules)


def test_fixture_03_table_is_read_whole(f3):
    ref = f3["reference"]
    assert len(ref.items) == ref.stated_total == 55
    assert dict(ref.stated) == {"Green": 30, "Orange": 23, "Red": 2}


def test_fixture_03_everything_the_table_lists_is_found(f3):
    want, got = f3["expected"], f3["found"]
    assert got["codes"] == want["codes"]  # 13 windows and sliders, every code exact
    assert got["hinged"] >= want["hinged"] == 9
    assert got["cavity_arch"] >= want["cavity_arch"]
    assert got["garage"] == want["garage"] == 1


def test_fixture_03_robes_are_the_three_pockets_and_nothing_else(f3):
    want, got = f3["expected"], f3["found"]
    assert got["robes"] == want["robes"] == 3
    # no robe the length of a whole wall (a grid line), no robe outside a bedroom
    robes = [r for r in f3["page"]["recesses"] if r["kind"] == "robe"]
    assert all(r["length_mm"] < 2500 and (r["room_name"] or "").startswith("BED") for r in robes)


def test_fixture_03_bath_shower_tray_is_a_drawn_recess(f3):
    trays = [r for r in f3["page"]["recesses"] if r["kind"] == "shower_recess" and r["room_name"] == "BATH"]
    assert len(trays) == 1 and trays[0]["source"] == "drawn_rectangle"
    # the table: about 900 x 1500, thin-line enclosure with a drain dot
    assert sorted((trays[0]["depth_mm"], trays[0]["length_mm"])) == pytest.approx([900, 1500], abs=100)


def test_fixture_03_csd_belongs_to_the_dashed_gap_and_the_leaf_line_gap_is_orange(f3):
    ops = f3["page"]["openings"]
    csd = [o for o in ops if o["kind"] == "cavity_slider" and "csd" in o["evidence"]]
    assert len(csd) == 1 and csd[0]["confidence"] == "clear"  # the table's row 34: 876 gap with "870 csd"
    # the table's row 35: Mud to garage, one line across the gap: a door, a cavity slider or open (Orange)
    leaf = [
        o
        for o in ops
        if o["kind"] == "cavity_slider" and o["confidence"] == "unsure" and o["alternative"] == "hinged_door"
    ]
    assert any(abs(o["width_mm"] - 890) <= 60 for o in leaf)
    assert all("One line across" in o["evidence"] for o in leaf)


# --- kitchen detail: the open plan is split and the kitchen's joinery is read ------------------------------
def _zone(page, name):
    return next(r for r in page["rooms"] if r["name"] == name)


def test_4a_open_plan_zones_each_know_who_they_share_with_and_nothing_else_does(results):
    (found, _) = results
    page = found["fixture4a"]["page"]
    family, kitchen, dining = (_zone(page, n) for n in ("FAMILY", "KITCHEN", "DINING"))
    assert set(family["shares_with"]) == {"KITCHEN", "DINING"} and set(kitchen["shares_with"]) == {
        "FAMILY",
        "DINING",
    }
    assert dining["shares_with"] == ["FAMILY", "KITCHEN"] or set(dining["shares_with"]) == {
        "FAMILY",
        "KITCHEN",
    }
    walled = [r for r in page["rooms"] if r["name"] in ("BED 1", "BED 2", "BATH", "LAUNDRY", "ENSUITE")]
    assert walled and all(r["shares_with"] == [] for r in walled)  # a walled room is not a zone


def test_4a_kitchen_island_is_read_from_the_drawing(results):
    (found, _) = results
    (island,) = [k for k in found["fixture4a"]["page"]["kitchen"] if k["type"] == "ISLAND"]
    x0, y0, x1, y1 = island["rect"]
    # the table: "3.56 x 1.10 m island with sink"
    assert sorted((x1 - x0, y1 - y0)) == pytest.approx([1100, 3560], abs=120)
    assert "free-standing" in island["evidence"] and island["confidence"] == "clear"


def test_4b_fridge_and_dishwasher_are_marked_in_the_kitchen(results):
    (found, _) = results
    kitchen = found["fixture4b"]["page"]["kitchen"]
    # the table: "sink, DW provision, cooktop/UB oven, fridge space F" in D1's kitchen
    kinds = {k["type"] for k in kitchen}
    assert {"DISHWASHER PROVISION", "FRIDGE SPACE"} <= kinds
    fridge = next(k for k in kitchen if k["type"] == "FRIDGE SPACE")
    assert fridge["confidence"] == "unsure"  # a letter on the plan: the size of the space is not drawn
    assert "against" in fridge["evidence"] or "mm from" in fridge["evidence"]  # where it stands is measured


def test_4b_the_kitchens_are_zones_of_their_dwellings(results):
    (found, _) = results
    zones = [r for r in found["fixture4b"]["page"]["rooms"] if r["name"] in ("KITCHEN", "DINING")]
    assert len(zones) == 4 and all(r["shares_with"] for r in zones)  # a kitchen and a dining zone in each


def test_fixture_03_kitchen_is_split_from_family_and_the_pantry(f3):
    page = f3["page"]
    names = {r["name"] for r in page["rooms"] if r["name"]}
    assert {"FAMILY & MEALS", "KITCHEN", "WALK-IN PANTRY"} <= names
    assert "FAMILY & MEALS, KITCHEN, WALK-IN PANTRY" not in names
    assert set(_zone(page, "KITCHEN")["shares_with"]) == {"FAMILY & MEALS", "WALK-IN PANTRY"}


def test_fixture_03_island_fridge_and_dishwasher_match_the_table(f3):
    items = {k["type"]: k for k in f3["page"]["kitchen"]}
    x0, y0, x1, y1 = items["ISLAND"]["rect"]
    assert sorted((x1 - x0, y1 - y0)) == pytest.approx([900, 2300], abs=150)  # the table: island 2300 x ~900
    assert "DW PROV" in items["ISLAND"]["evidence"] and "DISHWASHER PROVISION" in items
    assert "FRIDGE SPACE" in items  # the table: 1000 fridge recess "F"
