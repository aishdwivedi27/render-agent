"""Draft a fixture's answer key from a job's house_specification.json (M5.5 fixture intake).

The draft is a starting point for a person, never ground truth: every value is written as
{value: ..., verified: false} and the file carries status: unverified. The owner or a designer
checks each value against the PDF, sets verified: true (or corrects/deletes it), fills
verified_by / verified_on, sets status: verified, and saves it as answer_key.yaml.
Values the model did not find ("NOT FOUND") are left out. must_not_appear starts empty: invented
content can only be listed by a person who has read the drawings.

Writes <fixture_dir>/answer_key.draft.yaml and never touches an existing answer_key.yaml.

Usage: python scripts/draft_answer_key.py <job_dir> <fixture_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from render_agent.util.fs import atomic_write_text  # noqa: E402

DRAFT = "answer_key.draft.yaml"
NOT_FOUND = "NOT FOUND"
ROOM_FIELDS = ("ensuite", "WIR", "kitchen", "pantry", "study", "laundry", "garage", "porch", "deck")


def _keep(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != "" and v.strip().upper() != NOT_FOUND


def _leaf(v: str) -> dict[str, Any]:
    return {"value": v, "verified": False}


def _vals(v: Any) -> Any:
    """Wraps a string or a list of strings; returns None when nothing was found."""
    if isinstance(v, list):
        items = [_leaf(x) for x in v if _keep(x)]
        return items or None
    return _leaf(v) if _keep(v) else None


def _section(pairs: dict[str, Any]) -> dict[str, Any]:
    return {k: w for k, v in pairs.items() if (w := _vals(v)) is not None}


def _pdf_name(job_dir: Path) -> str:
    info = job_dir / "ingest.json"
    if not info.is_file():
        return ""
    pages = json.loads(info.read_text(encoding="utf-8")).get("pages", [])
    names = sorted({str(pg.get("source_file", "")) for pg in pages if pg.get("source_file")})
    return ", ".join(names)


def draft(spec: dict[str, Any], fixture: str, pdf: str = "") -> dict[str, Any]:
    p = spec.get("PROJECT", {})
    s = spec.get("SITE", {})
    e = spec.get("EXTERNAL_ARCHITECTURE", {})
    r = spec.get("RELATIONSHIPS", {})
    dwellings: dict[str, Any] = _section({"count": r.get("number_of_dwellings")})
    for d in spec.get("DWELLINGS", []):
        sec = _section(
            {
                "lot": d.get("lot_id"),
                "fl_living": d.get("living_area"),
                "garage": d.get("garage_area"),
                "porch": d.get("porch_area"),
                "total": d.get("overall_area"),
                "storeys": d.get("number_of_storeys"),
            }
        )
        if sec and _keep(d.get("dwelling_id")):
            dwellings[d["dwelling_id"]] = sec
    rel = [*r.get("mirrored_relationships", []), *r.get("similarities", []), *r.get("differences", [])]
    if (rel_vals := _vals(rel)) is not None:
        dwellings["relationships"] = rel_vals
    rooms: dict[str, Any] = {}
    for fp in spec.get("FLOOR_PLAN", []):
        labels = [*fp.get("bedrooms", []), *fp.get("bathrooms", []), *fp.get("living_areas", [])]
        labels += [fp.get(k) for k in ROOM_FIELDS]
        if (vals := _vals([x for x in labels if isinstance(x, str)])) and _keep(fp.get("dwelling_id")):
            rooms[fp["dwelling_id"]] = vals
    colours = {
        c["item"]: _leaf(c["colour"])
        for c in spec.get("MATERIALS_AND_COLOURS", {}).get("schedule_as_documented", [])
        if _keep(c.get("item")) and _keep(c.get("colour"))
    }
    key: dict[str, Any] = {
        "status": "unverified",
        "verified_by": "",
        "verified_on": "",
        "fixture": fixture,
        "pdf": pdf,
        "project": _section(
            {
                "project_number": p.get("project_number"),
                "address": p.get("address"),
                "client": p.get("client"),
                "drawing_date": p.get("drawing_date"),
                "drawn_by": p.get("drawn_by"),
                "status": p.get("drawing_status_purpose"),
                "construction": p.get("construction_description"),
                "revisions": p.get("revision_information", []),
            }
        ),
        "site": _section(
            {
                "total_site_area": s.get("total_site_area"),
                "lots": s.get("lots", []),
                "fencing": s.get("fencing", []),
                "stormwater": s.get("stormwater", []),
            }
        ),
        "dwellings": dwellings,
        "rooms": rooms,
        "external": _section(
            {
                "roof": e.get("roof_type"),
                "roof_pitch": e.get("roof_pitch"),
                "eaves": e.get("eaves", []),
                "windows": e.get("windows", []),
                "doors": e.get("doors", []),
                "garage_door": e.get("garage_doors"),
                "cladding": e.get("cladding", []),
            }
        ),
        "colour_schedule": colours,
        "expected_contradictions": [_leaf(u) for u in spec.get("UNCERTAINTIES", []) if _keep(u)],
        "must_not_appear": [],
    }
    return {k: v for k, v in key.items() if v not in ({}, None) or k in ("must_not_appear",)}


HEADER = (
    "# DRAFT answer key, written by scripts/draft_answer_key.py from the agent's own extraction.\n"
    "# Every value is unverified. Check each one against the PDF, set verified: true (or fix or delete\n"
    "# it), add must_not_appear terms, fill verified_by / verified_on, set status: verified and save\n"
    "# as answer_key.yaml. Until then this file is not an answer key.\n"
)


def write_draft(job_dir: Path, fixture_dir: Path) -> Path:
    spec = json.loads((job_dir / "house_specification.json").read_text(encoding="utf-8"))
    key = draft(spec, fixture_dir.name, _pdf_name(job_dir))
    out = fixture_dir / DRAFT
    body = yaml.safe_dump(key, sort_keys=False, allow_unicode=True, width=110)
    atomic_write_text(out, HEADER + body)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = write_draft(Path(argv[0]), Path(argv[1]))
    print(f"Wrote {out} (every value unverified)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
