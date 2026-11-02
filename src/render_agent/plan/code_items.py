"""The code's reading in the shape the models answer in, so code is the first reader of the readout table
(PLAN_VISUALS_SPEC section 4). Rooms, openings and recesses come from the drawing (plan/code_reading.py);
fixtures, clearance zones and walls stay with the model that traced the page. Outlines are page fractions,
as in plan_trace.json, so readout.model_rows scores code and models the same way.
"""

from __future__ import annotations

from typing import Any

from render_agent.plan.trace_rules import TraceRules

Box = tuple[float, float, float, float]
LEVEL = {"CLEAR": "GREEN", "UNSURE": "ORANGE", "GREEN": "GREEN", "ORANGE": "ORANGE", "RED": "RED"}


def _frac(points: list[list[float]], page_mm: tuple[float, float]) -> list[list[float]]:
    return [[round(x / page_mm[0], 5), round(y / page_mm[1], 5)] for x, y in points]


def _rect_poly(r: list[float]) -> list[list[float]]:
    return [[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]]]


def _box(points: list[list[float]]) -> Box:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _near(rooms: list[dict[str, Any]], x: float, y: float) -> dict[str, Any] | None:
    """The room whose box is closest to a point (0 when the point is inside it)."""

    def d(r: dict[str, Any]) -> float:
        b = _box(r["outline"])
        dx, dy = max(b[0] - x, 0.0, x - b[2]), max(b[1] - y, 0.0, y - b[3])
        return dx * dx + dy * dy

    return min(rooms, key=d, default=None)


def _name(room: dict[str, Any] | None, rules: TraceRules) -> str:
    return (room["name"] if room and room["name"] else rules.rooms.unlabelled_name).upper()


def as_reading(
    code: dict[str, Any], base: dict[str, Any], page_mm: tuple[float, float], rules: TraceRules
) -> dict[str, Any]:
    """`base` (a model's reading) with its rooms, openings and recesses replaced by what code read."""
    tpl = rules.readout.templates
    names = {r["id"]: r for r in code["rooms"]}
    between = {u["room"]: u["between"] for u in code["unlabelled"]}
    off = {r["id"]: r["off_drawing_mm"] for r in code["room_offsets"]}
    out_rooms = []
    for r in code["rooms"]:
        label = r["name"]
        item: dict[str, Any] = {
            "id": r["id"],
            "dwelling": "",
            "name": _name(r, rules),
            "outline": _frac(r["outline"], page_mm),
            "evidence": tpl.evidence["room"].format(labels=label)
            if label
            else tpl.evidence["room_unlabelled"],
            "confidence": "GREEN" if label else "ORANGE",
            "alternative": None,
            "question": None
            if label
            else rules.readout.questions["unlabelled"].format(
                between=" and ".join(between.get(r["id"], [])) or "its neighbours"
            ),
            "off_drawing_mm": off.get(r["id"], 0),
        }
        out_rooms.append(item)
    by_opening = {c["opening"]: c for c in code["connections"]}
    out_open = []
    for o in code["openings"]:
        f = o["frame"]
        cx, cy = (f[0] + f[2]) / 2, (f[1] + f[3]) / 2
        con = by_opening.get(o["id"])
        host = _near(code["rooms"], cx, cy)
        side_names = tpl.sides[o["axis"]]
        hb = _box(host["outline"]) if host else (cx, cy, cx, cy)
        centre = (hb[0] + hb[2]) / 2 if o["axis"] == "v" else (hb[1] + hb[3]) / 2
        side = side_names[0 if (cx if o["axis"] == "v" else cy) < centre else 1]
        item = {
            "id": o["id"],
            "dwelling": "",
            "kind": o["kind"],
            "at": _frac([[cx, cy]], page_mm)[0],
            "width_mm": o["width_mm"],
            "code": o["code"],
            "swing": _name(names.get(con["a"]), rules) if con and o["kind"] == "hinged_door" else None,
            "rooms": [_name(names.get(con["a"]), rules), _name(names.get(con["b"]), rules)] if con else [],
            "evidence": o["evidence"] + (f". {o['code_note']}" if o.get("code_note") else ""),
            "confidence": LEVEL[o["confidence"].upper()],
            "alternative": o["alternative"],
            "question": None,
            "off_drawing_mm": o["off_drawing_mm"],
            "name": con["item"] if con else None,
            "host": _name(host, rules) if host and not con else None,
            "side": side,
            "hinge": tpl_end(rules, o) if o["kind"] == "hinged_door" else None,
        }
        if o["kind"] == "window" and not o["code"]:
            item["confidence"] = "ORANGE"
            item["question"] = "window_code"
        out_open.append(item)
    out_rec = []
    for r in code["recesses"]:
        item = {
            "id": r["id"],
            "dwelling": "",
            "kind": r["kind"],
            "room": _name(names.get(r["room"]), rules),
            "outline": _frac(_rect_poly(r["rect"]), page_mm),
            "evidence": r["evidence"],
            "confidence": LEVEL[r["confidence"].upper()],
            "alternative": r["alternative"],
            "question": None,
            "off_drawing_mm": r["off_drawing_mm"],
        }
        out_rec.append(item)
    return {**base, "rooms": out_rooms, "openings": out_open, "recesses": out_rec}


def tpl_end(rules: TraceRules, o: dict[str, Any]) -> str | None:
    """Which end of the break the door hinges on, in the words of the drawing rules ('left', 'top'...)."""
    ends = rules.openings.ends.get(o["axis"])
    end = o.get("hinge_end")
    return ends[end] if ends and end in (0, 1) else None
