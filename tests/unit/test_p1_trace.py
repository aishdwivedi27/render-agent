"""PV2 floor plan trace (docs/PLAN_VISUALS_SPEC.md section 3): vector walls, the area schedule, measuring
to the outer faces, snapping, the mirror check, the rows code writes and the overlay disclaimer."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from render_agent.errors import PauseJobError, PermanentError
from render_agent.output import disclaimer
from render_agent.pipeline.job import new_job
from render_agent.pipeline.runner import RunContext
from render_agent.pipeline.stages import p1_trace
from render_agent.plan import areas, geometry, overlay, rows, vector_walls
from render_agent.plan.trace_rules import load_trace_rules
from tests.conftest import FIXTURE_01, ROOT, fixture_pdf

FIXTURE_03 = ROOT / "private" / "fixtures" / "fixture_03"


@pytest.fixture
def rules(install_settings):
    return load_trace_rules(install_settings.path("knowledge"))


def _pdf(fixture: Path) -> Path:
    pdf = fixture_pdf(fixture)
    if pdf is None:
        pytest.skip(f"{fixture.name} is not available")
    return pdf


def _words(pdf: Path, page: int) -> list:
    with pymupdf.open(pdf) as doc:
        return doc[page - 1].get_text("words")


# --- walls, scale and schedule from the drawing ------------------------------------------------
def test_scale_comes_from_the_sheet_reading_then_the_page_text():
    assert vector_walls.scale_from_text("NOT FOUND", "FLOOR PLAN 1:100 @ A3") == 100
    assert vector_walls.scale_from_text("1:200", "1:100") == 200
    assert vector_walls.scale_from_text("NOT FOUND", "no scale here") is None
    assert vector_walls.mm_per_pt(100) == pytest.approx(35.2778, rel=1e-4)


def test_vector_walls_of_a_house_are_traced_and_title_marks_dropped(rules):
    vp = vector_walls.extract(_pdf(FIXTURE_03), 2, 100, rules.walls)
    box = geometry.bounds(vp.walls)
    width, depth = box[2] - box[0], box[3] - box[1]
    assert 12000 < width < 14000 and 18500 < depth < 20500  # the house, not the page (logo dropped)
    assert len(vp.walls) > 50
    assert all(min(r[2] - r[0], r[3] - r[1]) >= rules.walls.raster_mm for r in vp.walls)


def test_footprint_matches_the_schedule_total(rules):
    pdf = _pdf(FIXTURE_03)
    vp = vector_walls.extract(pdf, 2, 100, rules.walls)
    closed = vector_walls.closures(vp, rules.areas.footprint_max_gap_mm, rules.walls.min_wall_mm)
    m = areas.measure([vector_walls.polygon(r) for r in vp.walls], closed, [], rules)
    total = areas.parse_schedule(_words(pdf, 2), rules)[""]["total"]
    assert abs(m.total_m2 - total) <= rules.areas.tolerance * total


def test_area_schedule_is_read_per_dwelling(rules):
    single = areas.parse_schedule(_words(_pdf(FIXTURE_03), 2), rules)
    assert set(single) == {""} and set(single[""]) == {"living", "garage", "alfresco", "porch", "total"}
    multi = areas.parse_schedule(_words(_pdf(FIXTURE_01), 2), rules)
    assert sorted(multi) == ["DWELLING 1", "DWELLING 2", "DWELLING 3"]
    assert multi["DWELLING 1"]["living"] != multi["DWELLING 2"]["living"]  # read from its own block


# --- measuring, snapping, mirror -----------------------------------------------------------------
def _box_walls(x0, y0, x1, y1, t=90):
    """Four walls of thickness t around the inside box (x0, y0, x1, y1)."""
    return [
        (x0 - t, y0 - t, x1 + t, y0),
        (x0 - t, y1, x1 + t, y1 + t),
        (x0 - t, y0, x0, y1),
        (x1, y0, x1 + t, y1),
    ]


def test_zones_are_measured_to_the_outer_faces(rules):
    walls = _box_walls(0, 0, 6000, 4000) + [(3000, 0, 3090, 4000)]  # two rooms, one shared wall
    polys = [vector_walls.polygon(r) for r in walls]
    rooms = [
        ("GARAGE", [(0, 0), (3000, 0), (3000, 4000), (0, 4000)]),
        ("BED 1", [(3090, 0), (6000, 0), (6000, 4000), (3090, 4000)]),
    ]
    m = areas.measure(polys, [], rooms, rules)
    veneer = rules.areas.veneer_mm
    outer = (6180 + 2 * veneer) * (4180 + 2 * veneer) / 1e6
    assert m.total_m2 == pytest.approx(outer, rel=0.02)
    garage = (3045 + 90 + veneer) * (4180 + 2 * veneer) / 1e6  # to the centre of the shared wall
    assert m.zones["garage"] == pytest.approx(garage, rel=0.03)
    assert m.zones["living"] == pytest.approx(m.total_m2 - m.zones["garage"], abs=0.01)


def test_outdoor_zones_are_measured_as_outlined(rules):
    walls = _box_walls(0, 0, 4000, 4000)
    alfresco = [(4090, 0), (7090, 0), (7090, 3000), (4090, 3000)]
    m = areas.measure([vector_walls.polygon(r) for r in walls], [], [("ALFRESCO", alfresco)], rules)
    assert m.zones["alfresco"] == pytest.approx(9.0, abs=0.01)


def test_compare_marks_differences_over_tolerance():
    measured = areas.Measured(100.0, {"garage": 30.0, "living": 60.0})
    out = {
        r["area"]: r for r in areas.compare({"garage": 30.5, "living": 70.0, "total": 101.0}, measured, 0.03)
    }
    assert out["garage"]["within"] and out["total"]["within"]
    assert not out["living"]["within"] and out["living"]["read"]


def test_snapping_keeps_edges_straight():
    walls = [(1000, 0, 1090, 3000), (0, 2000, 3000, 2090)]
    poly = [(1120, 100), (2500, 100), (2500, 1950), (1130, 1950)]
    out = geometry.snap(poly, walls, 150)
    assert out[0][0] == out[3][0] == 1090  # both corners on the wall face, one straight edge
    assert out[2][1] == out[3][1] == 2000


def test_mirrored_and_repeated_dwellings_are_found():
    left = [(0, 0, 90, 8000), (0, 0, 5000, 90), (0, 3000, 2000, 3090), (2000, 3000, 2090, 5000)]
    mirrored = [(10000 - r[2], r[1], 10000 - r[0], r[3]) for r in left]
    assert geometry.match(mirrored, left)[0] == "mirrored" and geometry.match(mirrored, left)[1] > 0.9
    shifted = [(r[0] + 20000, r[1], r[2] + 20000, r[3]) for r in left]
    assert geometry.match(shifted, left) == ("repeated", 1.0)
    different = [(0, 0, 90, 8000), (0, 4000, 5000, 4090)]
    assert geometry.match(different, left)[1] < 0.85


# --- rows code writes ----------------------------------------------------------------------------
def _geo(fixtures, clearance=()):
    return {
        "rooms": [{"id": "R1", "name": "BATH"}, {"id": "R2", "name": "ENS"}],
        "fixtures": [{"type": t, "room": room} for room, t in fixtures],
        "clearance_zones": [
            {"id": f"C{i}", "room": r, "evidence": "hatched"} for i, r in enumerate(clearance)
        ],
    }


def test_bath_without_a_shower_asks_bath_only_or_shower_over(rules):
    out = rows.shower_rows(_geo([("BATH", "BATH"), ("ENS", "SHOWER")]), rules)
    assert [(r["item"], r["confidence"], r["question"]) for r in out] == [
        ("BATH: shower", "ORANGE", rules.showers.question)
    ]
    assert rows.shower_rows(_geo([("BATH", "BATH"), ("BATH", "SHOWER")]), rules) == []


def test_clearance_zone_is_recorded_green_with_its_label(rules):
    (r,) = rows.clearance_rows(_geo([], clearance=["ENS"]), rules)
    assert (r["confidence"], r["reading"], r["question"]) == (
        "GREEN",
        "accessibility clearance (not drawn)",
        None,
    )


def test_orange_or_red_rows_need_a_question_and_green_rows_have_none():
    with pytest.raises(ValueError):
        rows.row("x", "X", "c", "r", "e", "RED")
    assert rows.row("x", "X", "c", "r", "e", "GREEN", "ignored?")["question"] is None
    assert (
        rows.scale_row(None, "")["confidence"] == "RED" and rows.scale_row(100, "text")["reading"] == "1:100"
    )


def test_area_rows_are_red_over_tolerance():
    results = [
        {"area": "garage", "traced_m2": 34.0, "schedule_m2": 32.0, "within": False, "read": True},
        {"area": "living", "traced_m2": 0.0, "schedule_m2": 100.0, "within": False, "read": False},
    ]
    (r,) = rows.area_rows(results, 0.03)
    assert r["confidence"] == "RED" and "+6.2%" in r["reading"] and r["question"]


# --- overlay and keys ----------------------------------------------------------------------------
def test_overlay_carries_a_readable_disclaimer(tmp_path, rules):
    page = tmp_path / "page.png"
    Image.new("RGB", (800, 1100), "white").save(page)
    geo = {
        "walls": [{"outline": [[1000, 1000], [9000, 1000], [9000, 1090], [1000, 1090]]}],
        "rooms": [{"name": "BED 1", "outline": [[1000, 1090], [9000, 1090], [9000, 6000], [1000, 6000]]}],
        "clearance_zones": [{"outline": [[2000, 2000], [3000, 2000], [3000, 3000], [2000, 3000]]}],
    }
    img = overlay.draw(page, 25.0, geo, (1000, 1000, 9000, 6000), rules)
    dest = tmp_path / "overlay.png"
    overlay.save(img, dest)
    with Image.open(dest) as saved:
        check = disclaimer.check_image(saved)
    assert check.ok and check.contrast >= 4.5


def test_overlay_that_cannot_carry_the_disclaimer_stops_plainly(tmp_path, monkeypatch):
    from render_agent.output.disclaimer import Check

    monkeypatch.setattr(disclaimer, "check_image", lambda img, placement=None: Check(False, 1.2, "faint"))
    with pytest.raises(PermanentError) as err:
        overlay.save(Image.new("RGB", (600, 600), "white"), tmp_path / "x.png")
    assert err.value.code == "DISCLAIMER_FAILED" and not (tmp_path / "x.png").exists()


def test_chosen_readout_model_without_a_key_pauses_naming_it(tmp_path, install_settings):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    ctx = RunContext(new_job(tmp_path / "jobs", [pdf]), install_settings)
    with pytest.raises(PauseJobError) as err:
        p1_trace._check_keys(ctx, ["sonnet"])
    assert "Claude" in err.value.what_happened and "Settings" in err.value.what_to_do
    p1_trace._check_keys(RunContext(ctx.job, install_settings, dry_run=True), ["sonnet"])  # practice: no key


def test_bad_trace_rules_file_is_a_plain_config_error(tmp_path):
    (tmp_path / "plan_trace.yaml").write_text("walls: {dark_below: 0}\n", encoding="utf-8")
    with pytest.raises(PermanentError) as err:
        load_trace_rules(tmp_path)
    assert err.value.code == "CONFIG_INVALID" and "plan_trace.yaml" in err.value.what_happened


def test_method_label_says_how_the_walls_were_traced_and_who_read_the_rooms():
    from render_agent.pipeline.plan_check import _method_label

    vector = {"method": "vector"}
    assert _method_label({"practice": True}, vector, []).endswith("rooms and fixtures are not shown")
    assert _method_label({}, vector, ["Claude", "OpenAI"]).endswith("Rooms read by Claude + OpenAI")
    assert _method_label({}, {"method": "raster_trace"}, ["OpenAI"]).startswith("Walls traced by OpenAI")
