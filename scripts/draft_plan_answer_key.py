"""Draft the floor plan answer key for fixture_03 from the approved reference geometry.

    bash:        python scripts/draft_plan_answer_key.py
    PowerShell:  python scripts\\draft_plan_answer_key.py

Reads private/engine/gen_data_reference.py (the approved geometry from the Claude project: rooms, openings
with codes and kinds, robe fixtures) and writes private/fixtures/fixture_03/plan_answer_key.draft.yaml.
It never touches plan_answer_key.yaml: the owner checks the draft against the drawing and renames it, and
only then does tests/integration/test_trace_parity.py use it.

The reference lists no wall rectangles (those were extracted from the PDF), so the key's walls are the
wall FACES it implies: every room edge and the outer footprint. A trace passes when each face lies on a
traced wall edge. Values are in mm in the reference frame (origin: outer face of the garage west wall,
top of the alfresco post); the test aligns the trace to the key by the best wall offset.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from render_agent.util.fs import atomic_write_text  # noqa: E402

REFERENCE = ROOT / "private" / "engine" / "gen_data_reference.py"
OUT = ROOT / "private" / "fixtures" / "fixture_03" / "plan_answer_key.draft.yaml"
KINDS = {  # reference opening kind -> the trace's opening kind (schemas/plan_trace.json)
    "window": "window",
    "slider": "sliding_door",
    "glassdoor": "sliding_door",
    "door": "hinged_door",
    "front": "hinged_door",
    "cavity": "cavity_slider",
    "garage": "garage_door",
    "open": "open_walkway",
}


# kinds the trace may report for a reference kind: an arch, a cavity slider and an open walkway are all built
# as a framed opening, and a drawing of dashed lines is read as "cavity slider or arch"
ACCEPTED = {
    "open_walkway": ["open_walkway", "arch"],
    "cavity_slider": ["cavity_slider", "arch"],
}


def load_reference() -> dict[str, Any]:
    """The module-level data of the reference script (it is a script, so it is run without its output)."""
    source = REFERENCE.read_text(encoding="utf-8").split("m = lambda v")[0]
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "walls.json"
        empty.write_text(json.dumps({"rects": [], "other": []}), encoding="utf-8")
        namespace: dict[str, Any] = {"__name__": "reference"}
        argv, sys.argv = sys.argv, ["reference", str(empty), str(Path(tmp) / "out.js")]
        try:
            exec(compile(source, str(REFERENCE), "exec"), namespace)  # noqa: S102
        finally:
            sys.argv = argv
    return namespace


def contains(poly: list[list[float]], x: float, y: float) -> bool:
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def faces(poly: list[list[float]]) -> list[list[float]]:
    """Each edge of an orthogonal polygon as [x0, y0, x1, y1]."""
    out = []
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i - 1], poly[i]
        out.append([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)])
    return out


def opening(o: dict[str, Any], index: int) -> dict[str, Any]:
    x0, y0, x1, y1 = o["frame"]
    horizontal = (x1 - x0) >= (y1 - y0)
    item: dict[str, Any] = {
        "id": f"O{index}",
        "kind": KINDS[o["kind"]],
        "accepted_kinds": ACCEPTED.get(KINDS[o["kind"]], [KINDS[o["kind"]]]),
        "reference_kind": o["kind"],
        "frame": [x0, y0, x1, y1],
        "wall_axis": "h" if horizontal else "v",
        "width_mm": (x1 - x0) if horizontal else (y1 - y0),
        "position_mm": (x0 + x1) / 2 if horizontal else (y0 + y1) / 2,
        "wall_line_mm": (y0 + y1) / 2 if horizontal else (x0 + x1) / 2,
    }
    for key in ("code", "name", "hinge", "swing"):
        if key in o:
            item[key] = o[key]
    return item


def robe(fx: dict[str, Any], rooms: list[dict[str, Any]], index: int) -> dict[str, Any]:
    x0, y0, x1, y1 = fx["r"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    home = next((r for r in rooms if contains(r["poly"], cx, cy)), None)
    corners = {(x0, y0), (x1, y0), (x1, y1), (x0, y1)}
    on_outline = len(corners & {(p[0], p[1]) for r in rooms for p in r["poly"]})
    return {
        "id": f"R{index}",
        "kind": "robe",
        "room": home["name"] if home else "NOT FOUND",
        "rect": [x0, y0, x1, y1],
        "depth_mm": min(x1 - x0, y1 - y0),
        "width_mm": max(x1 - x0, y1 - y0),
        # a wall jog changes the room outline; a robe drawn as a rectangle inside a plain room does not
        "source": "wall_jog" if on_outline >= 2 else "drawn_rectangle",
    }


def main() -> None:
    ref = load_reference()
    rooms = ref["ROOMS"]
    key: dict[str, Any] = {
        "status": "draft: not checked against the drawing yet; owner renames to plan_answer_key.yaml",
        "source": "private/engine/gen_data_reference.py (approved geometry from the Claude project)",
        "frame": "mm; origin = outer face of the garage west wall (x), top of the alfresco post (y)",
        "tolerance_mm": 100,
        "footprint": [list(p) for p in ref["FOOT"]],
        "wall_faces": [f for r in rooms if not r.get("outdoor") for f in faces(r["poly"])],
        "rooms": [
            {
                "id": r["id"],
                "name": r["name"],
                "zone": r["zone"],
                "outdoor": bool(r.get("outdoor")),
                "outline": [list(p) for p in r["poly"]],
                "labels": [lab["t"] for lab in r.get("labels", [])] or [r.get("short") or r["name"]],
                "aliases": [r["short"]] if r.get("short") else [],
            }
            for r in rooms
        ],
        "openings": [opening(o, n + 1) for n, o in enumerate(ref["OPEN"])],
        "recesses": [robe(f, rooms, n + 1) for n, f in enumerate(f for f in ref["F"] if f["kind"] == "robe")],
        "notes": [
            "Rooms the plan prints no label for (e.g. WC, Vanity, Hall) are matched by their corners, not "
            "their names; the trace names them 'unlabelled' and asks the designer.",
            "Robes with source drawn_rectangle have no wall jog; the trace may only find them as an "
            "Orange row.",
            "Reference veneer (160 mm) is not part of the key: faces are the frame faces.",
        ],
    }
    atomic_write_text(OUT, yaml.safe_dump(key, sort_keys=False, allow_unicode=True, width=110))
    print(
        f"wrote {OUT.relative_to(ROOT)}: {len(key['rooms'])} rooms, {len(key['openings'])} openings, "
        f"{len(key['recesses'])} recesses, {len(key['wall_faces'])} wall faces"
    )


if __name__ == "__main__":
    main()
