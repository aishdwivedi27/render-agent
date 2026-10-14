"""Per-dwelling elevation crops from lot labels, and the left-to-right façade block (stage 6)."""

from __future__ import annotations

from render_agent.pipeline.stages.s6_prompts import dwelling_regions, facade_block, facade_for, localise

ALIASES = {
    "DWELLING 1": ["DWELLING 1", "LOT 1", "D1"],
    "DWELLING 2": ["DWELLING 2", "LOT 2", "D2"],
    "DWELLING 3": ["DWELLING 3", "LOT 3", "D3"],
}
BBOX = {"x0": 0.18, "y0": 0.03, "x1": 0.84, "y1": 0.28}


def _w(x, y, text, width=0.02):
    return (x, y, x + width, y + 0.01, text)


def _labels(y=0.267):
    out = []
    for n, x in ((1, 0.404), (2, 0.556), (3, 0.709)):
        out += [_w(x, y, "LOT"), _w(x + 0.022, y, str(n), 0.006)]
    return out


def test_regions_split_at_midpoints_between_lot_labels():
    r = dwelling_regions(_labels(), BBOX, ALIASES)
    assert list(r) == ["DWELLING 1", "DWELLING 2", "DWELLING 3"]
    (a0, a1), (b0, b1), (c0, c1) = r.values()
    assert a1 == b0 and b1 == c0  # contiguous
    assert abs((a1 - a0) - (b1 - b0)) < 0.005 and abs((c1 - c0) - (b1 - b0)) < 0.005  # equal lots
    assert BBOX["x0"] <= a0 and c1 <= BBOX["x1"]


def test_regions_need_two_labelled_dwellings_inside_the_view():
    assert dwelling_regions(_labels()[:2], BBOX, ALIASES) == {}  # one label only
    assert dwelling_regions(_labels(y=0.6), BBOX, ALIASES) == {}  # labels belong to another view
    door_codes = [_w(0.4, 0.2, "D1"), _w(0.6, 0.2, "D2")]  # bare codes never count
    assert dwelling_regions(door_codes, BBOX, ALIASES) == {}


def test_localise_and_facade():
    refs = {
        "E1": {
            "label": "ELEVATION 1",
            "facade_left_to_right": [
                {"dwelling": "LOT 1", "items": ["garage door", "window", "entry door"]},
                {"dwelling": "LOT 2", "items": ["entry door", "window", "garage door"]},
            ],
        },
        "E1@DWELLING 2": {
            "label": "ELEVATION 1, DWELLING 2 only",
            "facade_left_to_right": [
                {"dwelling": "LOT 2", "items": ["entry door", "window", "garage door"]},
            ],
        },
        "P1": {"label": "FLOOR PLAN", "facade_left_to_right": []},
    }
    assert localise(["E1", "P1"], "DWELLING 2", refs) == ["E1@DWELLING 2", "P1"]
    assert localise(["E1"], "DWELLING 3", refs) == ["E1"]  # no crop for it: whole view kept
    items, src = facade_for(["E1@DWELLING 2"], "DWELLING 2", refs, ALIASES)
    assert items == ["entry door", "window", "garage door"] and "LOT 2" in src
    assert facade_for(["P1"], "DWELLING 2", refs, ALIASES) == ([], "")
    block = facade_block(items, src)
    assert block.splitlines()[1:] == ["1. entry door", "2. window", "3. garage door"]
    assert "no other windows or doors" in block.splitlines()[0]
    assert facade_block([], "") == ""


def test_aliases_read_labels_from_free_wording():
    from render_agent.validate.checklist_validator import dwelling_aliases

    spec = {
        "DWELLINGS": [
            {"dwelling_id": "DWELLING 1", "lot_id": "D1 on site plan; LOT 1 on ELEVATION 1"},
            {"dwelling_id": "DWELLING 2", "lot_id": "D2 / LOT 2 / PROPOSED DWELLING 2"},
        ]
    }
    al = dwelling_aliases(spec)
    assert "LOT 1" in al["DWELLING 1"] and "LOT 2" in al["DWELLING 2"]
    assert "ELEVATION 1" not in al["DWELLING 1"]


def test_scheduled_colorbond_names_get_their_real_tone():
    from render_agent.pipeline.stages.s6_prompts import identity_block, load_swatches, swatch_note
    from tests.conftest import ROOT

    sw = load_swatches(ROOT / "private" / "knowledge")
    assert sw["COLORBOND Monument"]["hex"].startswith("#")
    assert "warm light beige" in swatch_note("ENTRY DOOR: PAPERBARK", sw)
    assert swatch_note("ROOF: DARK GREY", sw) == ""
    spec = {
        "MATERIALS_AND_COLOURS": {"schedule_as_documented": [{"item": "ROOF", "colour": "WOODLAND GREY"}]}
    }
    ident = identity_block(spec, "DWELLING 1", {"items": []}, sw)
    assert "- Colour schedule: ROOF: WOODLAND GREY [COLORBOND Woodland Grey: dark grey" in ident
