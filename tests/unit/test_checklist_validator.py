"""checklist_validator on hand-built checklists (no LLM)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from render_agent.validate.checklist_validator import (
    Options,
    coverage,
    declined_views,
    load_view_set,
    qualifying_dwellings,
    validate,
)

ROOT = Path(__file__).resolve().parents[2]
VIEW_SET = load_view_set(ROOT / "private" / "knowledge")
SCHEMA = json.loads((ROOT / "schemas" / "checklist.json").read_text(encoding="utf-8"))
D1, D2, D3 = "DWELLING 1", "DWELLING 2", "DWELLING 3"


def render(rid, dwelling, view_type, location="", readiness="READY"):
    return {
        "render_id": rid,
        "dwelling_id": dwelling,
        "priority": "KEY INTERIOR",
        "view_type": view_type,
        "location": location or view_type,
        "identical_in_dwellings": [],
        "selection_rationale": "x",
        "architectural_facts": [],
        "materials_and_colours": [],
        "site_context": [],
        "permitted_visual_variation": [],
        "uncertainties": [],
        "readiness": readiness,
        "blockers": [],
    }


def checklist(renders, dwellings=(D1,), reason="single house"):
    return {
        "PROJECT": {
            "project_number": "P1",
            "address": "x",
            "render_budget_per_dwelling": 16,
            "dwelling_count": len(dwellings),
            "overall_readiness": "READY",
        },
        "DWELLING_SUMMARY": [
            {
                "dwelling_id": d,
                "selected_render_count": 99,
                "ready_count": 0,
                "review_count": 0,
                "not_ready_count": 0,
                "overall_readiness": "READY",
                "reason": reason,
            }
            for d in dwellings
        ],
        "RENDER_REQUIREMENTS": renders,
        "OMITTED_VIEWS": [],
        "CRITICAL_ARCHITECTURAL_BLOCKERS": [],
        "NON_CRITICAL_MISSING_INFORMATION": [],
        "PROJECT_TOTALS": {
            "selected_render_count": 1,
            "ready_count": 0,
            "review_count": 0,
            "not_ready_count": 0,
            "omitted_view_count": 0,
            "maximum_allowed_render_count": 0,
        },
    }


def run(cl, dwellings=(D1,), limit=16, **kw):
    out, corr = validate(
        cl, VIEW_SET, Options(dwellings=list(dwellings), limit=limit, project_number="P1", **kw)
    )
    jsonschema.validate(out, SCHEMA)
    return out, corr


def ids(cl):
    return [r["render_id"] for r in cl["RENDER_REQUIREMENTS"]]


def rules(corr):
    return [c["rule"] for c in corr]


def assert_counts_add_up(cl):
    rs = cl["RENDER_REQUIREMENTS"]
    t = cl["PROJECT_TOTALS"]
    assert t["selected_render_count"] == len(rs)
    assert t["ready_count"] + t["review_count"] + t["not_ready_count"] == len(rs)
    assert sum(s["selected_render_count"] for s in cl["DWELLING_SUMMARY"]) == len(rs)
    for s in cl["DWELLING_SUMMARY"]:
        assert s["ready_count"] + s["review_count"] + s["not_ready_count"] == s["selected_render_count"]
    assert t["omitted_view_count"] == len(cl["OMITTED_VIEWS"])


SINGLE_HOUSE = [
    render("P1-D1-FRONT", D1, "Front day"),
    render("P1-D1-REAR", D1, "Rear elevation"),
    render("P1-D1-MBED", D1, "Bedroom", "Main bedroom (WIR and ensuite open off it)"),
    render("P1-D1-WIR", D1, "WIR"),
    render("P1-D1-BED2", D1, "Bedroom", "Bedroom 2"),
    render("P1-D1-ENS", D1, "Ensuite"),
    render("P1-D1-BATH", D1, "Bathroom"),
    render("P1-D1-LDY", D1, "Laundry"),
    render("P1-D1-KIT", D1, "Kitchen", "Kitchen, pantry opening visible"),
    render("P1-D1-LIV", D1, "Family and meals"),
]


def test_single_house_valid_plan_unchanged_except_counts():
    out, corr = run(checklist(SINGLE_HOUSE))
    assert ids(out) == [r["render_id"] for r in SINGLE_HOUSE]
    assert set(rules(corr)) == {"recount"}  # the LLM's counts were wrong (99, 1)
    assert_counts_add_up(out)
    assert out["DWELLING_SUMMARY"][0]["selected_render_count"] == 10


def test_four_bed_house_keeps_one_secondary_bedroom():
    extra = [
        render("P1-D1-BED3", D1, "Bedroom", "Bedroom 3"),
        render("P1-D1-BED4", D1, "Bedroom", "Bedroom 4"),
    ]
    out, corr = run(checklist(SINGLE_HOUSE + extra))
    assert "P1-D1-BED2" in ids(out) and "P1-D1-MBED" in ids(out)
    assert "P1-D1-BED3" not in ids(out) and "P1-D1-BED4" not in ids(out)
    assert rules(corr).count("max_count") == 2
    assert len(out["OMITTED_VIEWS"]) == 2
    assert_counts_add_up(out)


def test_pantry_night_and_outside_set_are_dropped():
    bad = [
        render("P1-D1-PANTRY", D1, "Other interior", "Walk-in pantry"),
        render("P1-D1-NIGHT", D1, "Front night"),
        render("P1-D1-AERIAL", D1, "Aerial/site context"),
        render("P1-D1-TANK", D1, "Other exterior", "Rainwater tanks"),
    ]
    out, corr = run(checklist(SINGLE_HOUSE + bad))
    assert ids(out) == [r["render_id"] for r in SINGLE_HOUSE]
    assert rules(corr).count("drop_view") == 4
    assert "P1-D1-KIT" in ids(out)  # a kitchen showing the pantry stays


def test_night_kept_when_requested():
    out, _ = run(checklist(SINGLE_HOUSE + [render("P1-D1-NIGHT", D1, "Front night")]), night_requested=True)
    assert "P1-D1-NIGHT" in ids(out)


def test_missing_front_inserted_not_ready_with_blocker():
    out, corr = run(checklist(SINGLE_HOUSE[1:]))
    front = [r for r in out["RENDER_REQUIREMENTS"] if r["view_type"] == "Front day"]
    assert len(front) == 1 and front[0]["readiness"] == "NOT READY" and front[0]["blockers"]
    assert out["CRITICAL_ARCHITECTURAL_BLOCKERS"][0]["dwelling_id"] == D1
    assert "insert_view" in rules(corr)
    assert out["PROJECT"]["overall_readiness"] == "NOT READY"
    assert_counts_add_up(out)


def test_wir_is_never_inserted_and_is_trimmed_first():
    """Decision 14 Oct 2026: WIR only when the plan is under the limit."""
    plan = [r for r in SINGLE_HOUSE if r["view_type"] != "WIR"]
    out, _ = run(checklist(plan), documented={"WIR": {D1}})
    assert not [r for r in out["RENDER_REQUIREMENTS"] if r["view_type"] == "WIR"]
    out, _ = run(checklist(SINGLE_HOUSE), limit=9)
    assert "P1-D1-WIR" not in ids(out) and len(ids(out)) == 9


def test_limit_trims_from_the_bottom_of_the_priority_list():
    out, corr = run(checklist(SINGLE_HOUSE), limit=6)
    assert ids(out) == ["P1-D1-FRONT", "P1-D1-REAR", "P1-D1-MBED", "P1-D1-BED2", "P1-D1-ENS", "P1-D1-BATH"]
    assert rules(corr).count("limit") == 4
    assert all(o["budget_limited"] for o in out["OMITTED_VIEWS"])
    assert out["PROJECT"]["render_budget_per_dwelling"] == 6
    assert_counts_add_up(out)


def test_limit_never_drops_the_front():
    out, _ = run(checklist(SINGLE_HOUSE), limit=1)
    assert ids(out) == ["P1-D1-FRONT"]


def _attached_mirrored():
    """Three attached dwellings; D1 mirrors D2, D3 repeats D2; rear and living differ for D1."""
    every = f"Represents {D1} (mirrored), {D2}, {D3}"
    shared = [
        render(f"P1-SHARED-{v.upper()}", D2, vt, f"{loc}. {every}")
        for v, vt, loc in [
            ("mbed", "Bedroom", "Main bedroom"),
            ("wir", "WIR", "WIR"),
            ("bed2", "Bedroom", "Bedroom 2"),
            ("ens", "Ensuite", "Ensuite"),
            ("bath", "Bathroom", "Bathroom"),
            ("ldy", "Laundry", "Laundry"),
            ("kit", "Kitchen", "Kitchen"),
            ("study", "Study", "Study"),
        ]
    ]
    return [
        render("P1-SHARED-STREET", D2, "Streetscape", f"All fronts. {every}"),
        render("P1-SHARED-FRONTCU", D2, "Front close-up", every),
        render("P1-D1-REAR", D1, "Rear elevation", "Rear, 4-panel slider"),
        render("P1-SHARED-REAR", D2, "Rear elevation", f"Rear with deck. Represents {D2}, {D3}"),
        render("P1-D1-FAM", D1, "Family and meals"),
        render("P1-SHARED-FAM", D2, "Family and meals", f"Represents {D2}, {D3}"),
        *shared,
    ]


def test_attached_mirrored_shares_views_and_counts_coverage():
    dw = (D1, D2, D3)
    plan = _attached_mirrored()
    out, corr = run(checklist(plan, dw, "attached: shared walls"), dw)
    assert len(out["RENDER_REQUIREMENTS"]) == 14
    assert not [c for c in corr if c["rule"] != "recount"]
    cov = coverage(out, VIEW_SET, list(dw))
    assert len(cov[D1]) == 12 and len(cov[D2]) == 12 and len(cov[D3]) == 12
    assert out["DWELLING_SUMMARY"][1]["selected_render_count"] == 12  # D2 holds the shared masters
    assert_counts_add_up(out)


def test_attached_limit_counts_shared_renders_for_every_dwelling():
    dw = (D1, D2, D3)
    out, _ = run(checklist(_attached_mirrored(), dw, "attached"), dw, limit=10)
    cov = coverage(out, VIEW_SET, list(dw))
    assert all(len(v) <= 10 for v in cov.values())
    assert ids(out)[:2] == ["P1-SHARED-STREET", "P1-SHARED-FRONTCU"]  # priority 1, never trimmed


def test_separate_subdivision_splits_shared_renders():
    dw = (D1, D2)
    plan = [
        render("P1-SHARED-FRONT", D1, "Front day", f"Front. Represents {D1}, {D2}"),
        render("P1-SHARED-KIT", D1, "Kitchen", f"Kitchen. Represents {D1}, {D2}"),
    ]
    out, corr = run(checklist(plan, dw, "separate dwellings with open space between"), dw)
    assert sorted(ids(out)) == [
        "P1-D1-FRONT",
        "P1-D1-KIT",
        "P1-D2-FRONT",
        "P1-D2-KIT",
        "P1-SHARED-STREETSCAPE",
    ]
    street = next(r for r in out["RENDER_REQUIREMENTS"] if r["view_type"] == "Streetscape")
    cov = coverage({"RENDER_REQUIREMENTS": [street]}, VIEW_SET, list(dw))
    assert cov == {D1: [street["render_id"]], D2: [street["render_id"]]}  # one render shows both
    assert rules(corr).count("separate_subdivision") == 2
    for r in out["RENDER_REQUIREMENTS"]:
        if r["view_type"] == "Streetscape":
            continue
        assert coverage({"RENDER_REQUIREMENTS": [r]}, VIEW_SET, list(dw))[r["dwelling_id"]] == [
            r["render_id"]
        ]
    assert_counts_add_up(out)


@pytest.mark.parametrize("views", [["Front day", "Kitchen"], ["front elevation, day", "kitchen"]])
def test_views_directive_keeps_front_and_named_views(views):
    out, _ = run(checklist(SINGLE_HOUSE), views_only=views)
    assert ids(out) == ["P1-D1-FRONT", "P1-D1-KIT"]


def test_dwellings_directive():
    dw = (D1, D2)
    plan = [render("P1-D1-FRONT", D1, "Front day"), render("P1-D2-FRONT", D2, "Front day")]
    out, _ = run(checklist(plan, dw, "attached"), dw, dwellings_only=[D2])
    assert ids(out) == ["P1-D2-FRONT"]


def test_duplicate_render_ids_renamed():
    plan = [render("P1-D1-FRONT", D1, "Front day"), render("P1-D1-FRONT", D1, "Kitchen")]
    out, corr = run(checklist(plan))
    assert len(set(ids(out))) == 2 and "duplicate_id" in rules(corr)


def test_input_not_modified():
    cl = checklist(SINGLE_HOUSE[1:])
    before = json.dumps(cl, sort_keys=True)
    run(cl)
    assert json.dumps(cl, sort_keys=True) == before


def test_identical_views_are_merged_for_attached_dwellings():
    dw = (D1, D2, D3)
    rear = [render(f"P1-D{n}-REAR", d, "Rear elevation") for n, d in ((1, D1), (2, D2), (3, D3))]
    rear[1]["identical_in_dwellings"] = [D3]  # D2 and D3 look the same; D1 differs
    plan = [
        render("P1-SHARED-STREET", D1, "Streetscape", f"{D1}, {D2}, {D3}"),
        render("P1-SHARED-FRONTCU", D1, "Front close-up", f"{D1}, {D2}, {D3}"),
        *rear,
    ]
    out, corr = run(checklist(plan, dw, "attached"), dw)
    assert ids(out) == ["P1-SHARED-STREET", "P1-SHARED-FRONTCU", "P1-D1-REAR", "P1-SHARED-REAR"]
    merged = out["RENDER_REQUIREMENTS"][3]
    assert merged["dwelling_id"] == D2 and merged["identical_in_dwellings"] == [D3]
    assert "Represents DWELLING 2, DWELLING 3" in merged["location"]
    assert "share_identical" in rules(corr)
    assert_counts_add_up(out)


def test_identical_ignored_for_separate_dwellings():
    dw = (D1, D2)
    a, b = render("P1-D1-KIT", D1, "Kitchen"), render("P1-D2-KIT", D2, "Kitchen")
    a["identical_in_dwellings"] = [D2]
    out, _ = run(
        checklist(
            [render("P1-D1-F", D1, "Front day"), render("P1-D2-F", D2, "Front day"), a, b],
            dw,
            "separate dwellings",
        ),
        dw,
    )
    assert {"P1-D1-KIT", "P1-D2-KIT"} <= set(ids(out))


def test_main_bedroom_recognised_from_render_id():
    dw = (D1, D2)
    every = f"Represents {D1}, {D2}"
    plan = [
        render("P1-SHARED-FRONT", D1, "Front close-up", every),
        render("P1-D1-MAIN-BEDROOM", D1, "Bedroom", every),
        render("P1-D1-SECONDARY-BEDROOM", D1, "Bedroom", every),
    ]
    out, _ = run(checklist(plan, dw, "attached"), dw)
    assert len(out["RENDER_REQUIREMENTS"]) == 4  # + the inserted streetscape


def _spec_alike(differences):
    return {
        "DWELLINGS": [
            {"dwelling_id": d, "lot_id": f"D{n} / LOT {n} / PROPOSED DWELLING {n}"}
            for n, d in ((1, D1), (2, D2), (3, D3))
        ],
        "RELATIONSHIPS": {
            "mirrored_relationships": ["DWELLING 1 is a mirror of DWELLING 2"],
            "repeated_elements": ["DWELLING 3 repeats DWELLING 2"],
            "similarities": [],
            "differences": differences,
        },
    }


def test_share_groups_from_spec_relationships():
    from render_agent.validate.checklist_validator import share_groups

    g = share_groups(
        _spec_alike(
            [
                "LOT 1 and LOT 3 front faces show two sashes; LOT 2 front face shows a four-panel opening.",
                # mirror only:
                "ELEVATION 1 shows differing front-door/window ordering between LOT 1 and LOTS 2-3.",
                "DWELLING 2 and DWELLING 3 have a TIMBER DECK at the rear; no deck for DWELLING 1.",
                "DWELLING 1 BED 1 is 3530 by 3300; DWELLING 2 BED 1 is 3530 by 3000.",  # dimension only
            ]
        ),
        VIEW_SET,
    )
    assert g["front_day"] == [{D1, D2, D3}]  # panel counts are styling only
    assert g["rear_day"] == [{D2, D3}] and g["living"] == [{D2, D3}]
    assert g["main_bedroom"] == [{D1, D2, D3}] and g["study"] == [{D1, D2, D3}]


def test_unrelated_dwellings_are_never_grouped():
    from render_agent.validate.checklist_validator import share_groups

    spec = _spec_alike([])
    spec["RELATIONSHIPS"]["mirrored_relationships"] = []
    spec["RELATIONSHIPS"]["repeated_elements"] = []
    assert all(v == [] for v in share_groups(spec, VIEW_SET).values())


def test_validator_merges_by_spec_groups():
    dw = (D1, D2, D3)
    plan = [
        render("P1-SHARED-STREET", D1, "Streetscape", f"{D1}, {D2}, {D3}"),
        *[render(f"P1-D{n}-CLOSE", d, "Front close-up") for n, d in ((1, D1), (2, D2), (3, D3))],
        render("P1-D1-STUDY", D1, "Study"),
        render("P1-D2-STUDY", D2, "Study"),
    ]
    groups = {"front_day": [{D1, D3}], "study": [{D1, D2, D3}]}
    out, _ = run(checklist(plan, dw, "attached"), dw, share_groups=groups)
    assert sorted(ids(out)) == ["P1-D2-CLOSE", "P1-SHARED-CLOSE", "P1-SHARED-STREET", "P1-SHARED-STUDY"]


def test_difference_split_on_contrast_words():
    from render_agent.validate.checklist_validator import share_groups

    line = (
        "At the front face, DWELLING 2 has timber cladding, "
        "while DWELLING 1 and DWELLING 3 have rendered walls."
    )
    assert share_groups(_spec_alike([line]), VIEW_SET)["front_day"] == [{D1, D3}]


def test_window_styling_never_splits_views():
    from render_agent.validate.checklist_validator import share_groups

    line = "At the front LIVING/STUDY face, DWELLING 2's window is drawn as four stacked panels"
    g = share_groups(_spec_alike([line]), VIEW_SET)
    assert g["front_day"] == [{D1, D2, D3}] and g["study"] == [{D1, D2, D3}]
    no_window = "DWELLING 2 STUDY has no window, unlike DWELLING 1 and DWELLING 3"
    g = share_groups(_spec_alike([no_window]), VIEW_SET)
    assert g["study"] == [{D1, D3}]


def test_streetscape_priority_one_for_two_or_more_dwellings():
    single = [*SINGLE_HOUSE, render("P1-D1-STREET", D1, "Streetscape")]
    out, _ = run(checklist(single))
    assert "P1-D1-STREET" not in ids(out)  # never for a single house
    dw = (D1, D2)
    plan = [render("P1-D1-FRONT", D1, "Front day"), render("P1-D2-FRONT", D2, "Front day")]
    out, corr = run(checklist(plan, dw, "attached"), dw)
    assert ids(out)[0] == "P1-SHARED-STREETSCAPE" and "insert_view" in rules(corr)
    out, _ = run(checklist(plan, dw, "attached"), dw, limit=1)
    assert "P1-SHARED-STREETSCAPE" in ids(out)  # never trimmed
    out, _ = run(checklist(plan, dw, "attached"), dw, declined={"streetscape"})
    assert "P1-SHARED-STREETSCAPE" not in ids(out)
    assert declined_views("@streetscape=false", VIEW_SET) == {"streetscape"}
    assert declined_views("@streetscape=true", VIEW_SET) == set()


def test_streetscape_alone_does_not_satisfy_the_front_view():
    out, corr = run(checklist([render("P1-D1-STREET", D1, "Streetscape")]))
    fronts = [r for r in out["RENDER_REQUIREMENTS"] if r["view_type"] == "Front day"]
    assert len(fronts) == 1 and fronts[0]["readiness"] == "NOT READY"


def test_hallway_is_a_standard_view():
    out, _ = run(checklist([*SINGLE_HOUSE, render("P1-D1-HALL", D1, "Hallway", "Entry hall to living")]))
    assert "P1-D1-HALL" in ids(out)


def test_rear_needs_a_documented_outdoor_structure():
    spec = {
        "DWELLINGS": [{"dwelling_id": d, "lot_id": f"LOT {n}"} for n, d in ((1, D1), (2, D2), (3, D3))],
        "FLOOR_PLAN": [
            {"dwelling_id": D1, "deck": "NOT FOUND"},
            {"dwelling_id": D2, "deck": "24-24 TIMBER DECK"},
            {"dwelling_id": D3, "deck": "NOT FOUND"},
        ],
        "EXTERNAL_ARCHITECTURE": {
            "other_explicit_external_features": ["LOT 3: PERGOLA over the rear slider"]
        },
        "SITE": {"other_site_features": ["Rainwater tank"]},
    }
    q = qualifying_dwellings(spec, VIEW_SET)
    assert q["rear_day"] == {D2, D3}
    dw = (D1, D2, D3)
    plan = [render(f"P1-D{n}-REAR", d, "Rear elevation") for n, d in ((1, D1), (2, D2), (3, D3))]
    out, corr = run(checklist(plan, dw, "attached"), dw, qualifying=q)
    assert {"P1-D2-REAR", "P1-D3-REAR"} <= set(ids(out)) and "P1-D1-REAR" not in ids(out)
    assert any("deck" in c["detail"] for c in corr if c["rule"] == "drop_view")
    assert qualifying_dwellings(
        {**spec, "EXTERNAL_ARCHITECTURE": {"other_explicit_external_features": ["ALL: ALFRESCO"]}}, VIEW_SET
    )["rear_day"] == {D1, D2, D3}


def test_rear_view_kept_when_outdoor_area_is_a_floor_plan_space():
    """An alfresco recorded only as a floor-plan space or room connection still qualifies."""
    for key in ("other_identifiable_spaces", "room_connections"):
        spec = {
            "DWELLINGS": [{"dwelling_id": D1}],
            "FLOOR_PLAN": [{"dwelling_id": D1, "deck": "NOT FOUND", key: ["ALFRESCO: 3440 by 3090"]}],
        }
        assert qualifying_dwellings(spec, VIEW_SET)["rear_day"] == {D1}
