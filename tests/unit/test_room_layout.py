"""Interior room layouts: schemas, camera-relative block, fallback, code stripping, interior check."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import yaml

from render_agent.llm.offline import minimal_instance
from render_agent.pipeline.room_layout import camera_wall, layout_block, strip_codes
from render_agent.pipeline.stages import s8_check
from render_agent.pipeline.stages.s4_spec import rooms_from_sheets
from render_agent.pipeline.stages.s6_prompts import assemble, default_scene, interior_layout
from render_agent.util.prompts import load_prompt

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
VIEW_SET = yaml.safe_load((ROOT / "private" / "knowledge" / "view_set.yaml").read_text(encoding="utf-8"))
BEDROOM_VIEW = next(v for v in VIEW_SET["views"] if v["id"] == "main_bedroom")
BBOX = {"x0": 0.2, "y0": 0.2, "x1": 0.4, "y1": 0.4}


def _item(kind, position, leads_to="", label=""):
    return {"kind": kind, "label": label, "leads_to": leads_to, "position": position, "beside": ""}


def _wall(side, *items):
    return {"side": side, "description": "", "items": list(items)}


def bedroom(name="BED 1", dwelling="D1"):
    """Window centred on the top wall; robe opening at the top end of the left wall; hall door and
    ensuite door at the left and right ends of the bottom wall."""
    return {
        "name": name,
        "dwelling": dwelling,
        "view_id": "S2-PLAN",
        "bbox": BBOX,
        "walls": [
            _wall("top", _item("window", "centre", label="15-15")),
            _wall("left", _item("opening", "left_end", "WIR", "2100 x 900")),
            _wall("right"),
            _wall(
                "bottom", _item("door", "left_end", "HALL", "870"), _item("door", "right_end", "ENS", "870")
            ),
        ],
    }


def spec_room(room):
    return {
        "dwelling_id": room["dwelling"],
        "name": room["name"],
        "source_sheet": "2",
        "view_id": room["view_id"],
        "page": 2,
        "bbox": room["bbox"],
        "walls": room["walls"],
        "contradictions": [],
    }


def _schema(name):
    return json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))


def test_room_layout_schema_round_trip(tmp_path):
    sheet = minimal_instance(_schema("sheet_analysis"))
    sheet.update(north_angle_deg="not shown", rooms=[bedroom()])
    jsonschema.validate(json.loads(json.dumps(sheet)), _schema("sheet_analysis"))
    (tmp_path / "sheets").mkdir()
    (tmp_path / "sheets" / "sheet-2.json").write_text(
        json.dumps({**sheet, "sheet_number": "2"}), encoding="utf-8"
    )
    rooms, contradictions = rooms_from_sheets(tmp_path)
    spec = minimal_instance(_schema("house_spec"))
    spec["ROOMS"] = rooms
    jsonschema.validate(spec, _schema("house_spec"))
    assert rooms == [spec_room(bedroom())] and contradictions == []


def test_different_layouts_on_two_sheets_are_kept_and_flagged(tmp_path):
    (tmp_path / "sheets").mkdir()
    other = bedroom()
    other["walls"][0]["items"][0]["position"] = "left_end"
    for n, room in ((2, bedroom()), (3, other)):
        sheet = {"sheet_number": str(n), "rooms": [room]}
        (tmp_path / "sheets" / f"sheet-{n}.json").write_text(json.dumps(sheet), encoding="utf-8")
    rooms, contradictions = rooms_from_sheets(tmp_path)
    assert len(rooms) == 2 and len(contradictions) == 1
    assert all(r["contradictions"] == contradictions for r in rooms)


def test_camera_relative_block_for_a_hand_built_room():
    room = spec_room(bedroom())
    face, why = camera_wall(room, BEDROOM_VIEW["camera"])
    assert face == "top" and "window" in why
    lines = layout_block(room, face, BEDROOM_VIEW["camera"], why).splitlines()
    assert "Facing you: window at the centre." in lines
    assert "Left wall: opening to WIR at the far end." in lines
    assert "Right wall: no window, door or fixture." in lines
    assert (
        "Behind you (not in view): door to HALL behind you to your left; "
        "door to ENS behind you to your right." in lines
    )


def test_facing_the_bottom_wall_mirrors_left_and_right():
    room = spec_room(bedroom())
    lines = layout_block(room, "bottom", {}, "faces the doors").splitlines()
    assert "Facing you: door to ENS at the left end; door to HALL at the right end." in lines
    assert "Right wall: opening to WIR at the near end, beside you." in lines


def test_room_without_layout_falls_back():
    render = {"render_id": "R1", "dwelling_id": "D1", "view_type": "Bedroom", "location": "BED 1"}
    room, block, note = interior_layout({"ROOMS": []}, render, BEDROOM_VIEW, ["D1"])
    assert room is None and block == "" and "no room" in note
    empty = {**spec_room(bedroom()), "walls": [_wall("top")]}
    room, block, note = interior_layout({"ROOMS": [empty]}, render, BEDROOM_VIEW, ["D1"])
    assert block == "" and "no openings or fixtures" in note


def test_size_codes_absent_from_image_prompt():
    room = spec_room(bedroom())
    render = {
        "render_id": "R1",
        "dwelling_id": "D1",
        "view_type": "Bedroom",
        "location": "BED 1 with the 2100 x 900 WIR opening",
        "architectural_facts": ["BED 1 has one window coded 15-15.", "BED 1 dimensions are 3540 and 3500."],
    }
    _, block, _ = interior_layout({"ROOMS": [room]}, render, BEDROOM_VIEW, ["D1"])
    scene = default_scene(render)
    scene.update(
        scene="Light through the 15-15 window; the 870 ENS door stays clear.", must_preserve=["870 door"]
    )
    text = assemble(
        "FIXED IDENTITY", [], scene, render, "GLOBAL RULES", block,
        VIEW_SET["image_prompt_strip_patterns"], VIEW_SET["image_prompt_drop_lines_with"],
    )  # fmt: skip
    for code in ("15-15", "2100", "900", "870", "3540", "3500"):
        assert code not in text, code
    assert "Facing you: window at the centre." in text and "dimensions" not in text
    assert strip_codes("a door marked 870.", VIEW_SET["image_prompt_strip_patterns"]) == "a door."


def _checker(entry, job_dir=None):
    c = object.__new__(s8_check.Checker)
    c.ctx = SimpleNamespace(settings=SimpleNamespace(drawing_check=SimpleNamespace(strictness="standard")))
    c.r = SimpleNamespace(entry=lambda _rid: entry)
    c.checklist, c.labels, c.spec, c.notes = {}, {}, {}, {}
    c.job_dir = job_dir or ROOT / "no-such-job"
    c.identities = {"D1": "FIXED IDENTITY"}
    c.template = load_prompt(ROOT / "private" / "prompts", s8_check.STAGE)
    return c


def test_interior_check_uses_wall_and_fixture_checks():
    block = layout_block(spec_room(bedroom()), "top", BEDROOM_VIEW["camera"], "faces the window")
    entry = {
        "view_type": "Bedroom", "dwelling_id": "D1", "reference_view_ids": [],
        "interior": True, "room_layout": block,
    }  # fmt: skip
    prompt = _checker(entry).prompt("R1")
    assert "opening_walls, fixture_positions" in prompt and "Facing you: window at the centre." in prompt
    assert "roof_form" not in prompt.split("Use only the checks listed for this view:")[1].splitlines()[0]
    exterior = _checker({**entry, "interior": False, "room_layout": ""}).prompt("R1")
    assert "storeys, roof_form" in exterior

    def answer(*checks):
        return {"render_id": "R1", "image_match": 0.9, "checks": [
            {"check": c, "result": "pass", "note": "", "confidence": 0.9} for c in checks
        ]}  # fmt: skip

    required = s8_check.INTERIOR_REQUIRED
    assert s8_check.decide(answer("extra_openings"), "R1", 0.85, required) == "unsure"
    assert s8_check.decide(answer("opening_walls", "fixture_positions"), "R1", 0.85, required) == "pass"
    failed = answer("opening_walls", "fixture_positions")
    failed["checks"][0]["result"] = "fail"
    assert s8_check.decide(failed, "R1", 0.85, required) == "fail"
    jsonschema.validate({**failed, "overall": "fail", "fix_instructions": []}, _schema("drawing_check"))


def test_grader_scores_wall_and_position():
    from tests.grade_extraction import grade_rooms

    key = {"room_layouts": {"status": "unverified", "D1": {"main_bedroom": {"room": "BED 1", "items": [
        {"kind": "window", "wall": "top", "position": "left_of_centre"},
        {"kind": "opening", "leads_to": "WIR", "wall": "right", "position": "left_end"},
        {"kind": "door", "leads_to": "ENS", "wall": "bottom", "position": "left_end"},
        {"kind": "robe", "wall": "top", "position": "centre"},
    ]}}}}  # fmt: skip
    spec = {"ROOMS": [{**spec_room(bedroom()), "dwelling_id": "DWELLING 1"}]}
    assert [i.status for i in grade_rooms(spec, key)] == [
        "right_wall",
        "wrong_wall",
        "wrong_position",
        "missing",
    ]


def test_invalid_room_bbox_gives_no_crop(tmp_path):
    from render_agent.pipeline.stages.s6_prompts import room_reference

    (tmp_path / "ingest.json").write_text(json.dumps({"pages": [{"page": 2, "image": "pages/page-2.png"}]}))
    ctx = SimpleNamespace(job=SimpleNamespace(dir=tmp_path))
    room = {**spec_room(bedroom()), "bbox": {"x0": 0.5, "y0": 0.5, "x1": 0.4, "y1": 0.6}}
    assert room_reference(ctx, room) is None
    assert room_reference(ctx, {**spec_room(bedroom()), "page": 9}) is None


def test_room_words_beat_a_neighbouring_room_named_in_the_location():
    from render_agent.pipeline.room_layout import find_room

    spec = {"ROOMS": [spec_room(bedroom("BED 1")), spec_room(bedroom("ENS")), spec_room(bedroom("KIT"))]}
    ens = next(v for v in VIEW_SET["views"] if v["id"] == "ensuite")["camera"]["room_words"]
    kit = next(v for v in VIEW_SET["views"] if v["id"] == "kitchen")["camera"]["room_words"]
    assert find_room(spec, {"location": "ENS off BED 1"}, ["D1"], ens)["name"] == "ENS"
    assert find_room(spec, {"location": "Kitchen facing FAMILY"}, ["D1"], kit)["name"] == "KIT"
    assert (
        find_room(spec, {"location": "BED 1 with WIR and ENS"}, ["D1"], BEDROOM_VIEW["camera"]["room_words"])[
            "name"
        ]
        == "BED 1"
    )


def test_check_prompt_carries_job_corrections_and_review_notes(tmp_path):
    from render_agent.pipeline import corrections

    corrections.add(tmp_path, "Kitchen", "island has no sink")
    entry = {"view_type": "Kitchen", "dwelling_id": "D1", "reference_view_ids": [], "interior": True,
             "room_layout": "ROOM LAYOUT: KIT"}  # fmt: skip
    c = _checker(entry, tmp_path)
    c.notes["R1"] = "DESIGNER_NOTE hallway visible"
    prompt = c.prompt("R1")
    assert "island has no sink" in prompt and "hallway visible" in prompt


def test_room_crop_is_turned_so_the_facing_wall_is_at_the_top(tmp_path):
    from PIL import Image

    from render_agent.pipeline.stages.s6_prompts import room_reference

    (tmp_path / "pages").mkdir()
    page = Image.new("RGB", (100, 200), "white")
    page.putpixel((50, 199), (255, 0, 0))  # a red mark on the bottom edge of the page
    page.save(tmp_path / "pages" / "page-2.png")
    (tmp_path / "ingest.json").write_text(json.dumps({"pages": [{"page": 2, "image": "pages/page-2.png"}]}))
    ctx = SimpleNamespace(job=SimpleNamespace(dir=tmp_path),
                          settings=SimpleNamespace(images=SimpleNamespace(room_crop_margin=0.0)))  # fmt: skip
    full = {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}
    ref = room_reference(ctx, {**spec_room(bedroom()), "bbox": full, "camera_face": "bottom"})
    crop = Image.open(tmp_path / ref["file"])
    assert crop.getpixel((49, 0))[1] < 50  # the bottom wall is now at the top (turned 180 degrees)
    ref = room_reference(ctx, {**spec_room(bedroom()), "bbox": full, "camera_face": "right"})
    assert Image.open(tmp_path / ref["file"]).size == (200, 100)


def test_size_codes_become_proportion_words_not_numbers():
    from render_agent.pipeline.room_layout import opening_shape

    shapes = VIEW_SET["opening_shapes"]
    assert opening_shape("06-12 obsc", shapes) == "small, high-set, wide and short, obscure glass"
    assert opening_shape("21-09 awn", shapes) == "tall and narrow, awning"
    assert opening_shape("24-15", shapes) == "full-height, tall and narrow"
    assert opening_shape("15-15", shapes) == ""
    room = spec_room(bedroom())
    room["walls"][0]["items"][0]["label"] = "06-12 obsc"
    block = layout_block(room, "top", {}, "faces the window", shapes)
    assert "Facing you: window (small, high-set, wide and short, obscure glass) at the centre." in block
    assert "06-12" not in block
