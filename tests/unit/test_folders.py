"""output/folders.py: <folder>/FINAL APPROVED from approved clean renders; Draft/draft-final copies."""

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
    assert (out / "Dwelling 1" / "FINAL APPROVED" / "front_day_D1.png").read_bytes() == b"P-D1-FRONT"
    assert not (out / "Common" / "FINAL APPROVED" / "front_day_D1.png").exists()
    assert (
        out / "Common" / "FINAL APPROVED" / "rear_day_D2-D3.png"
    ).read_bytes() == b"rear v2"  # approved version, clean
    for n in (2, 3):
        assert (out / f"Dwelling {n}" / "FINAL APPROVED" / "rear_day_D2-D3.png").read_bytes() == b"rear v2"
    assert not (out / "Dwelling 1" / "FINAL APPROVED" / "rear_day_D2-D3.png").exists()
    for n in (1, 2, 3):
        assert (out / f"Dwelling {n}" / "FINAL APPROVED" / "kitchen_D1-D2-D3.png").is_file()
    assert not list(out.rglob("study_*"))  # unapproved never exported
    assert not any(b"DRAFT" in p.read_bytes() for p in out.glob("*/*.png") if p.parent.name != "clean")
    rear = next(e for e in m if e["render_id"] == "P-SHARED-REAR")
    assert rear["master"] == "output/Common/FINAL APPROVED/rear_day_D2-D3.png" and len(rear["copies"]) == 2


def test_single_house_has_no_common(tmp_path):
    renders = [_r("P-D1-FRONT", D1, "Front day"), _r("P-D1-KIT", D1, "Kitchen")]
    for r in renders:
        _clean(tmp_path, r["render_id"], 1, b"x")
    export_approved(tmp_path, renders, {"P-D1-FRONT": 1, "P-D1-KIT": 1}, [D1], VS)
    assert not (tmp_path / "output" / "Common").exists()
    assert sorted(p.name for p in (tmp_path / "output" / "Dwelling 1" / "FINAL APPROVED").iterdir()) == [
        "front_day_D1.png",
        "kitchen_D1.png",
    ]


def test_streetscape_goes_to_common_and_every_dwelling(tmp_path):
    r = _r("P-SHARED-STREETSCAPE", D1, "Streetscape", f"Streetscape: {D1}, {D2}")
    _clean(tmp_path, r["render_id"], 1, b"s")
    export_approved(tmp_path, [r], {r["render_id"]: 1}, [D1, D2], VS)
    out = tmp_path / "output"
    assert (out / "Common" / "FINAL APPROVED" / "streetscape_D1-D2.png").is_file()
    assert (out / "Dwelling 1" / "FINAL APPROVED" / "streetscape_D1-D2.png").is_file()
    assert (out / "Dwelling 2" / "FINAL APPROVED" / "streetscape_D1-D2.png").is_file()


def test_names_list_dwellings_in_order(tmp_path):
    r = _r("P-SHARED-ENS", D3, "Ensuite", same=(D1, D2))  # representative dwelling is D3
    _clean(tmp_path, r["render_id"], 1, b"e")
    m = export_approved(tmp_path, [r], {r["render_id"]: 1}, [D1, D2, D3], VS)
    assert m[0]["master"] == "output/Common/FINAL APPROVED/ensuite_D1-D2-D3.png" and m[0]["covers"] == [
        D1,
        D2,
        D3,
    ]


def _png(w, h):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_final_images_delivered_at_1920x1080(tmp_path):
    import io

    from PIL import Image

    _clean(tmp_path, "P-D1-FRONT", 1, _png(2560, 1440))
    entries = export_approved(
        tmp_path, [_r("P-D1-FRONT", D1, "Front day")], {"P-D1-FRONT": 1}, [D1], VS, "1920x1080"
    )
    out = Image.open(io.BytesIO((tmp_path / entries[0]["copies"][0]).read_bytes()))
    assert out.size == (1920, 1080) and out.mode == "RGB" and out.format == "PNG"
    # the clean source in the job is untouched
    assert Image.open(tmp_path / "output" / "clean" / "P-D1-FRONT_v1.png").size == (2560, 1440)


def test_delivery_crops_other_aspects_and_keeps_portrait():
    import io

    from PIL import Image

    from render_agent.output.folders import deliver_png

    assert Image.open(io.BytesIO(deliver_png(_png(1536, 1024), "1920x1080"))).size == (1920, 1080)
    portrait = _png(1440, 2560)
    assert deliver_png(portrait, "1920x1080") == portrait


def test_draft_final_holds_the_approved_overlay_copy(tmp_path):
    _clean(tmp_path, "P-D1-FRONT", 1, b"clean")
    (tmp_path / "output" / "Dwelling 1" / "Draft").mkdir(parents=True)
    (tmp_path / "output" / "Dwelling 1" / "Draft" / "P-D1-FRONT_v1.png").write_bytes(b"with overlay")
    entries = export_approved(
        tmp_path, [_r("P-D1-FRONT", D1, "Front day")], {"P-D1-FRONT": 1}, [D1], VS,
        drafts={"P-D1-FRONT": "output/Dwelling 1/Draft/P-D1-FRONT_v1.png"},
    )  # fmt: skip
    assert entries[0]["draft_final"] == "output/Dwelling 1/Draft/draft-final/front_day_D1.png"
    assert (tmp_path / entries[0]["draft_final"]).read_bytes() == b"with overlay"
    assert (
        tmp_path / "output" / "Dwelling 1" / "FINAL APPROVED" / "front_day_D1.png"
    ).read_bytes() == b"clean"
    # delivered again after the clean copy was removed: the delivered file is reused
    (tmp_path / "output" / "clean" / "P-D1-FRONT_v1.png").unlink()
    again = export_approved(tmp_path, [_r("P-D1-FRONT", D1, "Front day")], {"P-D1-FRONT": 1}, [D1], VS)
    assert (tmp_path / again[0]["copies"][0]).read_bytes() == b"clean"
