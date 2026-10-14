"""Stages 5-6 and the approval gate with FakeLLM (no network)."""

from __future__ import annotations

import json

import jsonschema
from PIL import Image

from render_agent.cli import main
from render_agent.pipeline import gate
from render_agent.pipeline.job import load_job, new_job
from render_agent.pipeline.runner import RunContext, run_job
from tests.conftest import ROOT, make_pdf
from tests.fakes import FakeLLM, minimal_instance
from tests.unit.test_checklist_validator import D1, D2, D3, _attached_mirrored, checklist

SCHEMAS = ROOT / "schemas"


def _schema(name):
    return json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))


def _view(view_id, kind, facing, bbox, label):
    return {
        "view_id": view_id,
        "label": label,
        "view_kind": kind,
        "facing": facing,
        "dwellings_shown": ["ALL"],
        "bbox": dict(zip(("x0", "y0", "x1", "y1"), bbox, strict=True)),
        "description": "x",
    }


def _sheet(views):
    s = minimal_instance(_schema("sheet_analysis"))
    s["views"] = views
    return s


def _spec():
    s = minimal_instance(_schema("house_spec"))
    s["PROJECT"]["project_number"] = "P1"
    s["PROJECT"]["construction_description"] = "CONSTRUCTION: RENDERED PANEL"
    s["DWELLINGS"] = [
        {
            **minimal_instance(_schema("house_spec")["properties"]["DWELLINGS"]["items"]),
            "dwelling_id": d,
            "number_of_storeys": "Single storey",
        }
        for d in (D1, D2, D3)
    ]
    fp = minimal_instance(_schema("house_spec")["properties"]["FLOOR_PLAN"]["items"])
    s["FLOOR_PLAN"] = [
        {**fp, "dwelling_id": d, "WIR": "WIR - dimensions NOT FOUND", "deck": "TIMBER DECK at the rear"}
        for d in (D1, D2, D3)
    ]
    s["EXTERNAL_ARCHITECTURE"]["roof_type"] = "ALL: METAL ROOF"
    s["EXTERNAL_ARCHITECTURE"]["roof_pitch"] = "ALL: 20 DEGREE PITCH"
    s["EXTERNAL_ARCHITECTURE"]["eaves"] = ["ALL: 450 EAVES"]
    s["EXTERNAL_ARCHITECTURE"]["windows"] = ["ALL: Window colour scheduled as BLACK"]
    s["EXTERNAL_ARCHITECTURE"]["garage_doors"] = "ALL: PANEL LIFT DOOR"
    s["MATERIALS_AND_COLOURS"]["schedule_as_documented"] = [
        {"item": "ROOF", "colour": "DARK GREY"},
        {"item": "WALL PANELS", "colour": "WHITE"},
        {"item": "GARAGE DOOR", "colour": "DARK GREY"},
    ]
    return s


def _scene(rid, refs):
    p = minimal_instance(_schema("render_prompts")["properties"]["prompts"]["items"])
    p.update(
        render_id=rid, scene=f"scene for {rid}", reference_view_ids=refs, must_avoid=["no second storey"]
    )
    return p


def _llm():
    dw = (D1, D2, D3)
    cl = checklist(_attached_mirrored(), dw, "attached: shared side walls")
    scenes = [_scene("P1-SHARED-STREET", ["E1", "NOPE"]), _scene("P1-D1-REAR", [])]
    return FakeLLM(
        {
            "sheet_analysis.page-1": _sheet(
                [
                    _view("E1", "elevation", "front_street", (0.1, 0.1, 0.6, 0.4), "ELEVATION 1"),
                    _view("E2", "elevation", "rear", (0.1, 0.5, 0.6, 0.9), "ELEVATION 2"),
                    _view("CS", "schedule", "not_applicable", (0.7, 0.1, 0.9, 0.3), "COLOUR SCHEDULE"),
                ]
            ),
            "sheet_analysis.page-2": _sheet(
                [_view("FP", "floor_plan", "top_down", (0.9, 0.9, 0.1, 0.1), "FLOOR PLAN")]  # bad bbox
            ),
            "spec": _spec(),
            "checklist": cl,
            "render_prompts": {"prompts": scenes},
        }
    )


def _no_defaults(settings, keys=("entry_door_type", "entry_door_material")):
    """Removes standard defaults so these items need a designer choice at the gate."""
    p = settings.path("knowledge") / "materials_library.yaml"
    out, skip = [], None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("  ") and not line.startswith("    "):
            skip = line.strip().rstrip(":") in keys
        if skip and line.strip().startswith("default:"):
            continue
        out.append(line)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def _start(settings, instructions=""):
    pdf = make_pdf(settings.root / "drop" / "set" / "a.pdf", ["ELEVATIONS", "FLOOR PLAN"])
    inputs = [pdf]
    if instructions:
        txt = pdf.parent / "instructions.txt"
        txt.write_text(instructions, encoding="utf-8")
        inputs.append(txt)
    job = new_job(settings.path("jobs"), inputs)
    llm = _llm()
    return job, llm, run_job(RunContext(job, settings, extras={"llm": llm}))


def test_s5_writes_validated_plan_and_waits_at_gate(install_settings):
    job, llm, res = _start(install_settings)
    assert res.status == "AWAITING_APPROVAL" and res.stopped_at == "gate_approval", res.message
    cl = json.loads((job.dir / "checklist.json").read_text(encoding="utf-8"))
    jsonschema.validate(cl, _schema("checklist"))
    assert cl["PROJECT_TOTALS"]["selected_render_count"] == 14
    prompt = next(c for c in llm.calls if c["stage"] == "checklist")["prompt"]
    assert "{{" not in prompt and '"view_id": "E1"' in prompt and "<<<BEGIN HOUSE_SPECIFICATION" in prompt

    sel = {i["key"]: i for i in json.loads((job.dir / "selections.json").read_text())["items"]}
    assert "DARK GREY" in sel["roof_colour"]["pdf_value"] and not sel["roof_colour"]["needs_choice"]
    assert sel["garage_door_type"]["pdf_value"] != "NOT FOUND"
    door = sel["entry_door_type"]
    assert door["pdf_value"] == "NOT FOUND" and not door["needs_choice"]  # standard default fills it
    assert door["chosen"] == "Hinged, solid" and door["chosen_by"].startswith("standard default")

    approval = json.loads((job.dir / "approval.json").read_text())
    assert approval["status"] == "pending" and approval["needs_choice"] == []
    assert "entry_door_type" in {d["key"] for d in approval["standard_defaults"]}
    assert len(approval["plan"]["renders"]) == 14
    assert load_job(job.dir).state["status"] == "AWAITING_APPROVAL"


def test_no_instruction_file_approves_with_drawing_colours_and_defaults(install_settings):
    """A PDF alone: drawing colours used, gaps take standard defaults, a plain approve is enough."""
    job, _, _ = _start(install_settings)
    report = (job.dir / "MATERIALS_AND_DEFAULTS.txt").read_text(encoding="utf-8")
    assert "Colour schedule in the PDF: YES" in report and "ROOF: DARK GREY" in report
    assert "Entry door type: Hinged, solid" in report.split("STANDARD DEFAULTS USED")[1]
    assert gate.approve(job, {"entry_door_type": "Pivot"}, by="test") == []
    report = (job.dir / "MATERIALS_AND_DEFAULTS.txt").read_text(encoding="utf-8")
    assert "Entry door type: Pivot [test]" in report.split("CHOSEN BY THE DESIGNER")[1]
    llm = _llm()
    res = run_job(
        RunContext(load_job(job.dir), install_settings, extras={"llm": llm}), stages={"s7_render": None}
    )
    assert res.stopped_at == "s7_render", res.message
    index = json.loads((job.dir / "prompts" / "index.json").read_text(encoding="utf-8"))
    ident = index["identity_blocks"][D2]
    assert "Colour schedule: ROOF: DARK GREY" in ident and "Colour schedule: WALL PANELS: WHITE" in ident
    assert "Roof profile: Colorbond corrugated (standard default, not in the drawings)" in ident
    assert "Entry door type: Pivot (designer choice)" in ident


def test_gate_refuses_unresolved_then_approves_and_s6_builds_prompts(install_settings):
    _no_defaults(install_settings)
    job, _, _ = _start(install_settings)
    problems = gate.approve(job, {}, by="test")
    assert problems and any("entry_door_type" in p for p in problems)
    assert not gate.is_approved(job)
    res = run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": _llm()}))
    assert res.status == "AWAITING_APPROVAL" and "entry_door_type" in res.message

    need = [n["key"] for n in json.loads((job.dir / "approval.json").read_text())["needs_choice"]]
    choices = {k: "Pivot" if k == "entry_door_type" else "Timber" for k in need}
    choices["roof_colour"] = "Basalt"  # changes a PDF value -> logged override
    assert gate.approve(job, choices, by="test") == []
    approval = json.loads((job.dir / "approval.json").read_text())
    assert approval["status"] == "approved"
    assert [o["key"] for o in approval["overrides"]] == ["roof_colour"]

    llm = _llm()
    res = run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": llm}))
    assert res.stopped_at == "s7_render", res.message  # s7 is M3
    _check_prompts(job.dir, llm)


def _check_prompts(job_dir, llm):
    index = json.loads((job_dir / "prompts" / "index.json").read_text(encoding="utf-8"))
    renders = index["renders"]
    assert len(renders) == 14

    # crops: valid bbox cropped, bad bbox -> full sheet, schedules are not references
    page = Image.open(job_dir / "pages" / "page-1.png")
    e1 = Image.open(job_dir / "references" / "E1.png")
    assert e1.width < page.width * 0.6 and e1.height < page.height * 0.4
    assert (
        Image.open(job_dir / "references" / "FP.png").size
        == Image.open(job_dir / "pages" / "page-2.png").size
    )
    assert not (job_dir / "references" / "CS.png").exists()

    street = next(r for r in renders if r["render_id"] == "P1-SHARED-STREET")
    assert street["reference_view_ids"] == ["E1"] and street["scene_source"] == "llm"  # unknown id dropped
    rear = next(r for r in renders if r["render_id"] == "P1-D1-REAR")
    assert rear["reference_view_ids"][0] == "E2"  # rule fallback when the LLM named none
    kit = next(r for r in renders if r["render_id"] == "P1-SHARED-KIT")
    assert kit["reference_view_ids"] == ["FP"] and kit["scene_source"] == "default"
    assert all(len(r["references"]) <= 3 for r in renders)

    # identity block identical in every prompt of a dwelling, and first
    by_dwelling: dict[str, set[str]] = {}
    for r in renders:
        text = (job_dir / r["prompt_file"]).read_text(encoding="utf-8")
        ident = text.split("\n\nREFERENCE IMAGES")[0]
        by_dwelling.setdefault(r["dwelling_id"], set()).add(ident)
        assert ident.startswith(f"FIXED IDENTITY: {r['dwelling_id']}")
        order = [
            "FIXED IDENTITY",
            "REFERENCE IMAGES",
            "VIEW:",
            "MUST PRESERVE",
            "MUST AVOID",
            "GLOBAL RULES",
            "VISUALISATION CHOICES",
        ]
        assert [text.index(s) for s in order] == sorted(text.index(s) for s in order)
    assert all(len(v) == 1 for v in by_dwelling.values())
    ident = next(iter(by_dwelling[D2]))
    assert "Colour schedule: ROOF: DARK GREY" in ident and "Roof colour: Basalt (designer choice)" in ident
    assert "Entry door type: Pivot (designer choice)" in ident
    street_txt = (job_dir / street["prompt_file"]).read_text(encoding="utf-8")
    assert "Image 1 is ELEVATION 1 (front street), sheet 1" in street_txt and "no second storey" in street_txt
    assert "full sheet" in (job_dir / kit["prompt_file"]).read_text(encoding="utf-8")
    assert "{{" not in next(c for c in llm.calls if c["stage"] == "render_prompts")["prompt"]


def test_approve_file_passes_gate(install_settings):
    job, _, _ = _start(install_settings, "@select entry_door=pivot, timber")
    sel = {i["key"]: i for i in json.loads((job.dir / "selections.json").read_text())["items"]}
    assert sel["entry_door_type"]["chosen"] == "pivot" and sel["entry_door_material"]["chosen"] == "timber"
    need = [n["key"] for n in json.loads((job.dir / "approval.json").read_text())["needs_choice"]]
    (job.dir / "APPROVE").write_text("\n".join(f"{k}=As drawn" for k in need), encoding="utf-8")
    res = run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": _llm()}))
    assert res.stopped_at == "s7_render", res.message
    assert json.loads((job.dir / "approval.json").read_text())["approved_by"] == "APPROVE file"


def test_night_and_ignored_directives_reach_the_gate(install_settings):
    job, _, _ = _start(install_settings, "Please include night views.\n@colour=red")
    assert json.loads((job.dir / "checklist_validation.json").read_text())["night_requested"] is True
    assert json.loads((job.dir / "approval.json").read_text())["ignored_directives"]


def test_max_images_lowers_the_limit(install_settings):
    job, _, _ = _start(install_settings, "@max_images=6")
    cl = json.loads((job.dir / "checklist.json").read_text())
    assert cl["PROJECT"]["render_budget_per_dwelling"] == 6
    assert any(o["budget_limited"] for o in cl["OMITTED_VIEWS"])


def test_cli_approve(install, monkeypatch, capsys):
    monkeypatch.setenv("RENDER_AGENT_HOME", str(install))
    from render_agent.config import load_config

    settings = load_config(install)
    _no_defaults(settings)
    job, _, _ = _start(settings)
    assert main(["approve", str(job.dir), "--no-resume"]) == 1
    assert "entry_door_type" in capsys.readouterr().out
    need = [n["key"] for n in json.loads((job.dir / "approval.json").read_text())["needs_choice"]]
    args = ["approve", str(job.dir), "--no-resume"] + [a for k in need for a in ("--select", f"{k}=X")]
    assert main(args) == 0
    assert gate.is_approved(load_job(job.dir))


def test_dwelling_choice_at_gate_limits_prompts(install_settings):
    job, _, _ = _start(install_settings, "@select entry_door=pivot, timber")
    need = [n["key"] for n in json.loads((job.dir / "approval.json").read_text())["needs_choice"]]
    assert json.loads((job.dir / "approval.json").read_text())["dwellings_available"] == [D1, D2, D3]
    assert gate.approve(job, {k: "X" for k in need}, by="test", dwellings="1") == []
    run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": _llm()}))
    index = json.loads((job.dir / "prompts" / "index.json").read_text())
    kept = {r["render_id"] for r in index["renders"]}
    assert "P1-D1-REAR" in kept and "P1-SHARED-STREET" in kept and "P1-SHARED-KIT" in kept
    assert "P1-SHARED-REAR" in index["skipped_by_dwelling_choice"]  # stands for D2 and D3 only


def test_streetscape_opt_out_and_rear_only_with_deck(install_settings):
    import copy

    job, llm, _ = _start(install_settings, "@streetscape=false")
    cl = json.loads((job.dir / "checklist.json").read_text())
    kept = {r["render_id"] for r in cl["RENDER_REQUIREMENTS"]}
    assert "P1-SHARED-STREET" not in kept and "P1-SHARED-FRONTCU" in kept
    assert len(kept) == 13

    spec = copy.deepcopy(llm.canned["spec"])
    for fp in spec["FLOOR_PLAN"]:
        fp["deck"] = "NOT FOUND" if fp["dwelling_id"] == D1 else fp["deck"]
    llm2 = _llm()
    llm2.canned["spec"] = spec
    pdf = make_pdf(install_settings.root / "drop2" / "a.pdf", ["ELEVATIONS", "FLOOR PLAN"])
    job2 = new_job(install_settings.path("jobs"), [pdf])
    run_job(RunContext(job2, install_settings, extras={"llm": llm2}))
    cl2 = json.loads((job2.dir / "checklist.json").read_text())
    kept2 = {r["render_id"] for r in cl2["RENDER_REQUIREMENTS"]}
    assert "P1-D1-REAR" not in kept2 and "P1-SHARED-REAR" in kept2  # D1 has no deck
    omitted = [o["reason_omitted"] for o in cl2["OMITTED_VIEWS"]]
    assert any("deck" in o for o in omitted)


def _labelled_pdf(path):
    """Page 1: 'DWELLING n' printed under Elevation 1's bbox (0.1-0.6 wide, bottom at 0.4)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    w, h = page.rect.width, page.rect.height
    page.insert_text((72, 72), "ELEVATIONS")
    for n, x in ((1, 0.15), (2, 0.32), (3, 0.49)):
        page.insert_text((x * w, 0.42 * h), f"DWELLING {n}", fontsize=8)
    doc.new_page().insert_text((72, 72), "FLOOR PLAN")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


def test_stage6_crops_each_dwelling_and_lists_its_facade(install_settings):
    pdf = _labelled_pdf(install_settings.root / "drop3" / "a.pdf")
    job = new_job(install_settings.path("jobs"), [pdf])
    llm = _llm()
    e1 = llm.canned["sheet_analysis.page-1"]["views"][0]
    e1["facade_left_to_right"] = [
        {"dwelling": "DWELLING 1", "items": ["garage door", "window", "entry door"]},
        {"dwelling": "DWELLING 2", "items": ["entry door", "stacked window", "garage door"]},
    ]
    run_job(RunContext(job, install_settings, extras={"llm": llm}))
    need = [n["key"] for n in json.loads((job.dir / "approval.json").read_text())["needs_choice"]]
    assert gate.approve(job, {k: "X" for k in need}, by="test") == []
    res = run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": _llm_with(e1)}))
    assert res.stopped_at == "s7_render", res.message

    whole = Image.open(job.dir / "references" / "E1.png")
    parts = [Image.open(job.dir / "references" / f"E1-DWELLING {n}.png") for n in (1, 2, 3)]
    assert all(p.width < whole.width * 0.45 and p.height == whole.height for p in parts)

    index = {
        r["render_id"]: r for r in json.loads((job.dir / "prompts" / "index.json").read_text())["renders"]
    }
    close = index["P1-SHARED-FRONTCU"]  # representative dwelling 2
    assert close["reference_view_ids"][0] == "E1@DWELLING 2"
    assert close["references"][0] == "references/E1-DWELLING 2.png"
    assert close["facade_left_to_right"] == ["entry door", "stacked window", "garage door"]
    text = (job.dir / close["prompt_file"]).read_text(encoding="utf-8")
    assert "FAÇADE, LEFT TO RIGHT" in text and "1. entry door\n2. stacked window\n3. garage door" in text
    assert "Image 1 is ELEVATION 1, DWELLING 2 only" in text
    street = index["P1-SHARED-STREET"]  # shows every dwelling: whole elevation, no façade list
    assert street["reference_view_ids"][0] == "E1" and street["facade_left_to_right"] == []


def _llm_with(e1):
    llm = _llm()
    llm.canned["sheet_analysis.page-1"]["views"][0] = e1
    return llm
