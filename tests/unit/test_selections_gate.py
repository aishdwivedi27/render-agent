from pathlib import Path

from render_agent.pipeline.gate import parse_dwelling_choice
from render_agent.pipeline.selections import build_selections, load_materials

MATERIALS = load_materials(Path(__file__).resolve().parents[2] / "private" / "knowledge")
SPEC = {
    "PROJECT": {"construction_description": "CONSTRUCTION: HEBEL VENEER"},
    "EXTERNAL_ARCHITECTURE": {
        "roof_type": "ALL: COLORBOND ROOF",
        "doors": ["ALL: 2340MM HIGH FRONT DOOR"],
        "garage_doors": "ALL: PANEL LIFT DOOR AS SPECS",
        "windows": ["ALL: ALUM AWNING WINDOWS"],
        "cladding": ["LINEA/STRIA"],
    },
    "MATERIALS_AND_COLOURS": {
        "schedule_as_documented": [
            {"item": "ROOF", "colour": "GREY"},
            {"item": "ENTRY DOOR", "colour": "BROWN"},
            {"item": "GARAGE DOOR", "colour": "DARK"},
            {"item": "WINDOWS", "colour": "SILVER"},
        ]
    },
}


def _sel(**kw):
    return {i["key"]: i for i in build_selections(SPEC, {"Front day"}, MATERIALS, **kw)["items"]}


def test_each_item_gets_only_its_own_value():
    s = _sel()
    assert s["roof_colour"]["pdf_value"] == "ROOF: GREY"
    assert s["roof_profile"]["pdf_value"] == "NOT FOUND" and not s["roof_profile"]["needs_choice"]
    assert s["roof_profile"]["chosen"] == "Colorbond corrugated"  # standard default, labelled as such
    assert s["roof_profile"]["chosen_by"] == "standard default (not in the drawings)"
    assert s["entry_door_type"]["pdf_value"] == "NOT FOUND"  # a colour is not a door type
    assert not any(i["needs_choice"] for i in s.values())
    assert s["garage_door_type"]["pdf_value"] == "ALL: PANEL LIFT DOOR AS SPECS"
    assert s["garage_door_finish"]["pdf_value"] == "GARAGE DOOR: DARK"
    assert "WINDOWS: SILVER" in s["window_frames"]["pdf_value"]


def test_not_shown_items_need_no_choice():
    s = {i["key"]: i for i in build_selections(SPEC, {"Kitchen"}, MATERIALS)["items"]}
    assert not any(i["needs_choice"] for i in s.values())


def test_dwelling_choice():
    av = ["DWELLING 1", "DWELLING 2", "DWELLING 3"]
    assert parse_dwelling_choice("all", av) == (av, [])
    assert parse_dwelling_choice("2", av) == (["DWELLING 2"], [])
    assert parse_dwelling_choice("3,1", av) == (["DWELLING 1", "DWELLING 3"], [])
    assert parse_dwelling_choice("dwelling 2", av) == (["DWELLING 2"], [])
    assert parse_dwelling_choice("7", av)[1]


def test_schedule_on_sheet_but_missing_from_spec_warns():
    from render_agent.pipeline.stages.s5_checklist import schedule_warnings

    cat = [{"view_kind": "schedule", "label": "COLOUR SCHEDULE", "page": 3}]
    assert schedule_warnings(cat, {"MATERIALS_AND_COLOURS": {"schedule_as_documented": []}})
    assert not schedule_warnings(cat, SPEC)
    assert not schedule_warnings([], {"MATERIALS_AND_COLOURS": {"schedule_as_documented": []}})


def test_materials_report_lists_schedule_and_defaults():
    from render_agent.pipeline.selections import colour_schedule_status, materials_report

    sel = build_selections(SPEC, {"Front day"}, MATERIALS)
    sel["colour_schedule"] = colour_schedule_status(SPEC, [])
    text = materials_report(sel)
    assert "Colour schedule in the PDF: YES" in text and "ROOF: GREY" in text
    defaults = text.split("STANDARD DEFAULTS USED")[1].split("CHOSEN BY THE DESIGNER")[0]
    assert "Roof profile: Colorbond corrugated" in defaults
    assert "Roof colour" not in defaults  # scheduled, so not a default


def test_materials_report_without_schedule():
    from render_agent.pipeline.selections import colour_schedule_status, materials_report

    spec = {**SPEC, "MATERIALS_AND_COLOURS": {"schedule_as_documented": []}}
    sel = build_selections(spec, {"Front day"}, MATERIALS)
    sel["colour_schedule"] = colour_schedule_status(spec, [])
    assert "Colour schedule in the PDF: NO" in materials_report(sel)
    seen = [{"view_kind": "schedule", "label": "COLOUR SCHEDULE", "page": 3}]
    sel["colour_schedule"] = colour_schedule_status(spec, seen)
    assert "SCHEDULE SHEET WAS SEEN (COLOUR SCHEDULE (sheet 3))" in materials_report(sel)
