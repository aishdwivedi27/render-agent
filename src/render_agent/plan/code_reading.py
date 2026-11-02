"""The code's reading of one floor plan page: walls, openings, rooms, recesses and the overlay check.

This is what the trace reads from the drawing itself, with no model (PLAN_VISUALS_SPEC section 3). The models
only confirm and name it (plan/readout.py). `scripts/make_trace_kit.py` bundles this reading for the Claude
project, so both read a drawing with the same code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from render_agent.plan import openings, overlay_check, recesses, rooms, vector_walls
from render_agent.plan.source import Source, Word
from render_agent.plan.trace_rules import TraceRules

Box = tuple[float, float, float, float]
DOOR_KINDS = ("hinged_door", "cavity_slider", "open_walkway", "arch", "sliding_door")


@dataclass
class CodeReading:
    vp: vector_walls.VectorPage
    openings: list[openings.Opening]
    rooms: list[rooms.Room]
    recesses: list[recesses.Found]
    wall_rows: list[dict[str, Any]]
    offsets: dict[str, float]
    connections: list[dict[str, str]] = field(default_factory=list)
    unlabelled: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """The reading as plan/<dwelling>/code_reading.json (for the whole page; see `for_region`)."""
        by_opening = {c["opening"]: c for c in self.connections}
        return {
            "rooms": [r.as_dict() for r in self.rooms],
            "openings": [
                {**o.as_dict(), "rooms": _sides(o, by_opening), "off_drawing_mm": round(self.offsets[o.id])}
                for o in self.openings
            ],
            "recesses": [{**r.as_dict(), "off_drawing_mm": round(self.offsets[r.id])} for r in self.recesses],
            "wall_rows": self.wall_rows,
            "connections": self.connections,
            "unlabelled": self.unlabelled,
            "room_offsets": [{"id": r.id, "off_drawing_mm": round(self.offsets[r.id])} for r in self.rooms],
        }


def for_region(reading: dict[str, Any], region: Box | None) -> dict[str, Any]:
    """The part of a page's reading inside `region` (one dwelling of several on the page)."""
    if region is None:
        return reading

    def inside(points: list[Any]) -> bool:
        xs = [p[0] for p in points] if points and isinstance(points[0], list) else points[0::2]
        ys = [p[1] for p in points] if points and isinstance(points[0], list) else points[1::2]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]

    rooms_ = [r for r in reading["rooms"] if inside(r["outline"])]
    ids = {r["id"] for r in rooms_}
    gaps = [o for o in reading["openings"] if inside(o["frame"])]
    keep = {o["id"] for o in gaps}
    return {
        "rooms": rooms_,
        "openings": gaps,
        "recesses": [r for r in reading["recesses"] if inside(r["rect"])],
        "wall_rows": [w for w in reading["wall_rows"] if inside(w["box"])],
        "connections": [c for c in reading["connections"] if c["opening"] in keep],
        "unlabelled": [u for u in reading["unlabelled"] if u["room"] in ids],
        "room_offsets": [r for r in reading["room_offsets"] if r["id"] in ids],
    }


def _inside_one_room(op: openings.Opening, spaces: list[rooms.Room], px: float) -> bool:
    """An open walkway or arch with the same room (or no room) on both sides: a gap between a wall and a
    fitting, not a way between two rooms."""
    if op.kind not in ("open_walkway", "arch") or op.gap.exterior:
        return False
    low, high = rooms.sides(op, spaces, px)
    return low is None or high is None or low.id == high.id


def _sides(op: openings.Opening, by_opening: dict[str, dict[str, str]]) -> list[str]:
    c = by_opening.get(op.id)
    return [c["a"], c["b"]] if c else []


def read_page(
    vp: vector_walls.VectorPage, source: Source, words: list[Word], rules: TraceRules
) -> CodeReading:
    """Openings, then rooms (every break closed), then recesses, connections and the overlay check."""
    outdoor: list[Box] = []
    found = openings.read(vp, source, words, rules, outdoor)
    edges = outdoor + vector_walls.pier_closures(vp, rules.areas.footprint_max_gap_mm, rules.walls)
    spaces = rooms.read(vp, found, edges, words, rules)
    kept = [o for o in found if not _inside_one_room(o, spaces, vp.px_mm)]
    if len(kept) < len(found):  # a gap with the same room on both sides is not a way between rooms
        found = kept
        spaces = rooms.read(vp, found, edges, words, rules)
    pockets = recesses.read(spaces, source, rules)
    reading = CodeReading(
        vp,
        found,
        spaces,
        pockets,
        overlay_check.wall_rows(vp, spaces, rules),
        overlay_check.item_offsets(vp, spaces, found, pockets, rules),
    )
    reading.connections = _connections(found, spaces, vp.px_mm, rules)
    reading.unlabelled = _unlabelled(spaces, reading.connections, rules)
    return reading


def _label(room: rooms.Room | None, rules: TraceRules) -> str:
    return (room.name or rules.rooms.unlabelled_name).title() if room else ""


def _connections(
    found: list[openings.Opening], spaces: list[rooms.Room], px: float, rules: TraceRules
) -> list[dict[str, str]]:
    """Doors, sliders, arches and walkways between two rooms, named "<room A> -> <room B>": a door is
    named from the room it opens into."""
    out = []
    for op in found:
        if op.kind not in DOOR_KINDS:
            continue
        low, high = rooms.sides(op, spaces, px)
        if low is None or high is None or low.id == high.id:
            continue
        a, b = low, high
        if op.kind == "hinged_door" and op.features.arc_side > 0:
            a, b = high, low  # the leaf swings to the high side: the door opens into that room
        out.append(
            {
                "opening": op.id,
                "item": f"{_label(a, rules)} -> {_label(b, rules)}",
                "a": a.id,
                "b": b.id,
            }
        )
    return out


def _unlabelled(
    spaces: list[rooms.Room], connections: list[dict[str, str]], rules: TraceRules
) -> list[dict[str, Any]]:
    names = {r.id: r for r in spaces}
    out = []
    for room in spaces:
        if room.name:
            continue
        near = sorted(
            {
                _label(names.get(c["b" if c["a"] == room.id else "a"]), rules)
                for c in connections
                if room.id in (c["a"], c["b"])
            }
        )
        out.append({"room": room.id, "between": near})
    return out
