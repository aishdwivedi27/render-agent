"""output/folders.py: Common + Dwelling N folders from approved clean renders only."""

from __future__ import annotations

from render_agent.output.folders import export_approved
from render_agent.validate.checklist_validator import load_view_set
from tests.conftest import ROOT

VS = load_view_set(ROOT / "private" / "knowledge")
D1, D2, D3 = "DWELLING 1", "DWELLING 2", "DWELLING 3"


def _r(rid, d, vt, loc="", same=()):
    return {
        "render_id": rid,
        "dwelling_id": d,
        "view_type": vt,
        "location": loc or vt,
        "identical_in_dwellings": list(same),
    }


def _clean(job, rid, v, data):
    p = job / "output" / "clean" / f"{rid}_v{v}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    (job / "output" / f"{rid}_v{v}.png").write_bytes(b"DRAFT" + data)


def test_attached_shared_render_master_and_copies(tmp_path):
    renders = [
        _r("P-D1-FRONT", D1, "Front day"),
        _r("P-SHARED-REAR", D2, "Rear elevation", f"Rear. Represents {D2}, {D3}"),
        _r("P-SHARED-KIT", D1, "Kitchen", same=(D2, D3)),
        _r("P-D1-STUDY", D1, "Study"),  # not approved
    ]
    for r in renders:
        _clean(tmp_path, r["render_id"], 1, r["render_id"].encode())
    _clean(tmp_path, "P-SHARED-REAR", 2, b"rear v2")
    approved = {"P-D1-FRONT": 1, "P-SHARED-REAR": 2, "P-SHARED-KIT": 1}
    m = export_approved(tmp_path, renders, approved, [D1, D2, D3], VS)
    out = tmp_path / "output"
    assert (out / "Dwelling 1" / "front_day_D1.png").read_bytes() == b"P-D1-FRONT"
    assert not (out / "Common" / "front_day_D1.png").exists()
    assert (out / "Common" / "rear_day_D2-D3.png").read_bytes() == b"rear v2"  # approved version, clean
    for n in (2, 3):
        assert (out / f"Dwelling {n}" / "rear_day_D2-D3.png").read_bytes() == b"rear v2"
    assert not (out / "Dwelling 1" / "rear_day_D2-D3.png").exists()
    for n in (1, 2, 3):
        assert (out / f"Dwelling {n}" / "kitchen_D1-D2-D3.png").is_file()
    assert not list(out.rglob("study_*"))  # unapproved never exported
    assert not any(b"DRAFT" in p.read_bytes() for p in out.glob("*/*.png") if p.parent.name != "clean")
    rear = next(e for e in m if e["render_id"] == "P-SHARED-REAR")
    assert rear["master"] == "output/Common/rear_day_D2-D3.png" and len(rear["copies"]) == 2


def test_single_house_has_no_common(tmp_path):
    renders = [_r("P-D1-FRONT", D1, "Front day"), _r("P-D1-KIT", D1, "Kitchen")]
    for r in renders:
        _clean(tmp_path, r["render_id"], 1, b"x")
    export_approved(tmp_path, renders, {"P-D1-FRONT": 1, "P-D1-KIT": 1}, [D1], VS)
    assert not (tmp_path / "output" / "Common").exists()
    assert sorted(p.name for p in (tmp_path / "output" / "Dwelling 1").iterdir()) == [
        "front_day_D1.png",
        "kitchen_D1.png",
    ]


def test_streetscape_goes_to_common_and_every_dwelling(tmp_path):
    r = _r("P-SHARED-STREETSCAPE", D1, "Streetscape", f"Streetscape: {D1}, {D2}")
    _clean(tmp_path, r["render_id"], 1, b"s")
    export_approved(tmp_path, [r], {r["render_id"]: 1}, [D1, D2], VS)
    out = tmp_path / "output"
    assert (out / "Common" / "streetscape_D1-D2.png").is_file()
    assert (out / "Dwelling 1" / "streetscape_D1-D2.png").is_file()
    assert (out / "Dwelling 2" / "streetscape_D1-D2.png").is_file()


def test_names_list_dwellings_in_order(tmp_path):
    r = _r("P-SHARED-ENS", D3, "Ensuite", same=(D1, D2))  # representative dwelling is D3
    _clean(tmp_path, r["render_id"], 1, b"e")
    m = export_approved(tmp_path, [r], {r["render_id"]: 1}, [D1, D2, D3], VS)
    assert m[0]["master"] == "output/Common/ensuite_D1-D2-D3.png" and m[0]["covers"] == [D1, D2, D3]
