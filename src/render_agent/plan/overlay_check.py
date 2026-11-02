"""The overlay check against the DRAWING (PLAN_VISUALS_SPEC section 3; local, no model, no cost).

The traced walls are compared with the drawing's own wall band (every wall-thick dark mark on the page), not
with our own trace:

- a wall band drawn but not traced, or a traced wall with no band under it, bigger than
  `readout.overlay_tolerance_mm` in any direction, becomes a WALL row with its box and a question. It is
  Orange, and Red when it sits between two rooms (so it changes how the plan is enclosed);
- rooms, openings and recesses are measured against the same band: a corner or an end of a break more than
  that tolerance from any drawn wall cannot be Green.

Rules (merge distance, row limit, wording) are in knowledge/plan_trace.yaml, `overlay_check`.
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageChops, ImageFilter

from render_agent.plan import vector_walls
from render_agent.plan.openings import Opening
from render_agent.plan.recesses import Found
from render_agent.plan.rooms import Room, room_at
from render_agent.plan.trace_rules import TraceRules

Rect = tuple[float, float, float, float]
ORANGE, RED = "orange", "red"


def _grow(img: Image.Image, mm: float, px: float) -> Image.Image:
    """White (wall) pixels grown by `mm`."""
    size = 2 * round(mm / px) + 1
    return img.filter(ImageFilter.MaxFilter(size)) if size > 1 else img


def _walls(data: bytes, size: tuple[int, int], shrink: int) -> Image.Image:
    """255 where the page has wall, 0 elsewhere (the page images store wall as dark), at 1/`shrink` size:
    a coarse cell is wall when a quarter of it is."""
    img = Image.frombytes("L", size, data).point(lambda v: 255 if v < 128 else 0)
    return img.reduce(shrink).point(lambda v: 255 if v >= 64 else 0) if shrink > 1 else img


def _blobs(diff: Image.Image, px: float, rules: TraceRules) -> list[Rect]:
    """Boxes of the connected differences, merged when close, bigger than the tolerance in some direction."""
    inverted = diff.point(lambda v: 0 if v else 255)  # differences are "dark runs" for the run finder
    rects = [
        (a * px, b * px, c * px, d * px)
        for a, b, c, d in vector_walls._rectangles(inverted.tobytes(), diff.width, diff.height)
    ]
    if not rects:
        return []
    roots = vector_walls._roots(rects, max(px * 1.5, rules.overlay_check.merge_mm))
    boxes: dict[int, list[float]] = {}
    for r, root in zip(rects, roots, strict=True):
        b = boxes.setdefault(root, [r[0], r[1], r[2], r[3]])
        b[0], b[1], b[2], b[3] = min(b[0], r[0]), min(b[1], r[1]), max(b[2], r[2]), max(b[3], r[3])
    tol = rules.readout.overlay_tolerance_mm
    out = [
        (b[0], b[1], b[2], b[3]) for b in boxes.values() if max(b[2] - b[0], b[3] - b[1]) - px > tol
    ]  # one cell of rounding
    out.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    return out[: rules.overlay_check.max_rows]


def _inside_plan(box: Rect, vp: vector_walls.VectorPage, margin: float) -> bool:
    if not vp.walls:
        return False
    x0 = min(r[0] for r in vp.walls) - margin
    y0 = min(r[1] for r in vp.walls) - margin
    x1 = max(r[2] for r in vp.walls) + margin
    y1 = max(r[3] for r in vp.walls) + margin
    return box[0] >= x0 and box[1] >= y0 and box[2] <= x1 and box[3] <= y1


def wall_rows(vp: vector_walls.VectorPage, rooms: list[Room], rules: TraceRules) -> list[dict[str, Any]]:
    """WALL rows: differences between the traced walls and the drawing's wall band."""
    if not vp.band:
        return []
    tol = rules.readout.overlay_tolerance_mm
    shrink = max(1, round(tol * 0.4 / vp.px_mm))  # the check only needs the tolerance's detail
    px = vp.px_mm * shrink
    band = _walls(vp.band, vp.size, shrink)
    traced = _walls(vp.raster, vp.size, shrink)
    q = rules.overlay_check.questions
    ev = rules.overlay_check.evidence
    rows: list[dict[str, Any]] = []
    for kind, diff in (
        ("missing", ImageChops.subtract(band, _grow(traced, tol, px))),
        ("extra", ImageChops.subtract(traced, _grow(band, tol, px))),
    ):
        for box in _blobs(diff, px, rules):
            if not _inside_plan(box, vp, rules.overlay_check.margin_mm + px):  # a cell of rounding
                continue  # title block, notes and logos around the plan are not walls
            w, d = round(box[2] - box[0]), round(box[3] - box[1])
            rows.append(
                {
                    "id": f"wall_{kind}_{round((box[0] + box[2]) / 20)}_{round((box[1] + box[3]) / 20)}",
                    "kind": kind,
                    "box": [round(v, 1) for v in box],
                    "confidence": RED if _separates_rooms(box, rooms, rules) else ORANGE,
                    "evidence": ev[kind].format(w=w, d=d),
                    "question": q[kind],
                }
            )
    return rows


def _separates_rooms(box: Rect, rooms: list[Room], rules: TraceRules) -> bool:
    """The box lies between two different rooms: a wall there changes how the plan is enclosed."""
    look = rules.overlay_check.look_mm
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    probes = [(box[0] - look, cy), (box[2] + look, cy), (cx, box[1] - look), (cx, box[3] + look)]
    hit = {r.id for p in probes if (r := room_at(rooms, *p)) is not None}
    return len(hit) >= 2


def off_mm(vp: vector_walls.VectorPage, x: float, y: float, reach: float) -> float:
    """Distance from a point to the nearest wall band on the drawing, up to `reach` (then `reach`)."""
    if not vp.band:
        return 0.0
    px = vp.px_mm
    w, h = vp.size
    cx, cy = round((x - vp.origin[0]) / px), round((y - vp.origin[1]) / px)
    for step in range(round(reach / px) + 1):
        for dx in range(-step, step + 1):
            for dy in (-step, step) if abs(dx) != step else range(-step, step + 1):
                ix, iy = cx + dx, cy + dy
                if 0 <= ix < w and 0 <= iy < h and vp.band[iy * w + ix] < 128:
                    return float(step * px)
    return reach


def item_offsets(
    vp: vector_walls.VectorPage,
    rooms: list[Room],
    openings: list[Opening],
    recesses: list[Found],
    rules: TraceRules,
) -> dict[str, float]:
    """How far each item's corners (rooms, recesses) or break ends (openings) are from a drawn wall, in mm
    (the worst corner). Above the overlay tolerance the item cannot be Green."""
    reach = rules.readout.overlay_tolerance_mm * 2
    out: dict[str, float] = {}
    for room in rooms:
        out[room.id] = max((off_mm(vp, x, y, reach) for x, y in room.outline), default=0.0)
    for op in openings:
        g = op.gap
        x0, y0, x1, y1 = g.frame
        ends = (
            [(x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2)]
            if g.axis == "h"
            else [((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1)]
        )
        out[op.id] = max(off_mm(vp, x, y, reach) for x, y in ends)
    for rec in recesses:
        x0, y0, x1, y1 = rec.rect
        out[rec.id] = max(off_mm(vp, x, y, reach) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
    return out
