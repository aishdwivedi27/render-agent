"""Kitchen detail read by code (PLAN_VISUALS_SPEC section 3b; local, no model, no cost).

An open-plan space is cut into a zone per label (`rooms.py`), so the kitchen is a zone of its own. In a
zone whose
name holds a kitchen word the drawing is read for:

- the island: a free-standing box of four drawn lines (the box's edges agree within `line_tol_mm`, and no
  wall is
  within `island_clear_mm` of any side), with the words written inside it;
- the fridge space: the word "F" (or FRIDGE) written in the zone;
- a dishwasher provision: "DW" (with "PROV" beside it).

Where each stands against the walls is measured on the wall trace. The cooktop and the sink are drawn symbols;
the readout models read those (prompts/plan_trace.md), and the table asks about whatever only one reader
found.
Rules are in knowledge/plan_trace.yaml, `kitchen`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops, ImageFilter

from render_agent.plan import vector_walls
from render_agent.plan.rooms import Room
from render_agent.plan.source import Source, Word
from render_agent.plan.trace_rules import TraceRules

Rect = tuple[float, float, float, float]
CLEAR, UNSURE = "clear", "unsure"
DIRECTIONS = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}
REACH_MM = 4000.0  # how far a wall is looked for from an item


@dataclass
class Item:
    id: str
    type: str
    rect: Rect
    room: str  # id of the zone it is in
    evidence: str
    confidence: str
    alternative: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "rect": [round(v, 1) for v in self.rect],
            "room": self.room,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "alternative": self.alternative,
        }


def _wall_distance(vp: vector_walls.VectorPage, x: float, y: float, d: tuple[int, int]) -> float:
    step = vp.px_mm * 2
    dist = step
    while dist <= REACH_MM:
        if vp.wall_at(x + d[0] * dist, y + d[1] * dist):
            return dist
        dist += step
    return REACH_MM


def where(vp: vector_walls.VectorPage, rect: Rect, rules: TraceRules) -> str:
    """Where a box stands against the walls: the nearest wall, from the middle of each side."""
    k = rules.kitchen
    mids = {
        "left": (rect[0], (rect[1] + rect[3]) / 2),
        "right": (rect[2], (rect[1] + rect[3]) / 2),
        "top": ((rect[0] + rect[2]) / 2, rect[1]),
        "bottom": ((rect[0] + rect[2]) / 2, rect[3]),
    }
    near = {side: _wall_distance(vp, *mids[side], DIRECTIONS[side]) for side in DIRECTIONS}
    side = min(near, key=near.__getitem__)
    name = k.sides[side]
    if near[side] <= k.against_wall_mm:
        return f"against the {name} wall"
    return (
        f"{round(near[side], -1):.0f} mm from the {name} wall"
        if near[side] < REACH_MM
        else "clear of the walls"
    )


def _segments(ink: Image.Image, min_px: int, px: float) -> list[tuple[float, float, float]]:
    """Straight drawn lines along the rows of `ink` (255 = ink): (row, start, end) in mm, rows of one
    thick line
    merged, runs at least `min_px` long."""
    w, h = ink.size
    data = ink.tobytes()
    runs: list[tuple[int, int, int]] = []
    for y in range(h):
        for m in re.finditer(rb"\xff{%d,}" % min_px, data[y * w : (y + 1) * w]):
            runs.append((y, m.start(), m.end()))
    merged: list[list[float]] = []
    for y, a, b in sorted(runs, key=lambda r: (r[1], r[0])):
        for g in merged:
            if abs(g[0] - y) <= 3 and abs(g[1] - a) <= 3 and abs(g[2] - b) <= 3:
                g[0] = (g[0] + y) / 2
                break
        else:
            merged.append([float(y), float(a), float(b)])
    return [(g[0] * px, g[1] * px, g[2] * px) for g in merged]


def _boxes(ink: Image.Image, px: float, rules: TraceRules) -> list[Rect]:
    """Closed boxes of four drawn lines in the ink, in the ink's own frame (mm)."""
    k = rules.kitchen
    min_px = max(3, round(min(k.island_depth_mm) / px))
    across = _segments(ink, min_px, px)  # horizontal edges: (y, x0, x1)
    down = _segments(ink.transpose(Image.Transpose.TRANSPOSE), min_px, px)  # vertical edges: (x, y0, y1)
    tol, found = k.line_tol_mm, []
    for i, (ty, tx0, tx1) in enumerate(across):
        for by, bx0, bx1 in across[i + 1 :]:
            top, bottom = (ty, by) if ty < by else (by, ty)
            if abs(tx0 - bx0) > tol or abs(tx1 - bx1) > tol:
                continue
            length, depth = tx1 - tx0, bottom - top
            long, short = sorted((length, depth), reverse=True)
            if not (k.island_length_mm[0] <= long <= k.island_length_mm[1]):
                continue
            if not (k.island_depth_mm[0] <= short <= k.island_depth_mm[1]):
                continue
            sides = [
                v
                for v in down
                if (abs(v[0] - tx0) <= tol or abs(v[0] - tx1) <= tol)
                and v[1] <= top + tol
                and v[2] >= bottom - tol
            ]
            if any(abs(v[0] - tx0) <= tol for v in sides) and any(abs(v[0] - tx1) <= tol for v in sides):
                found.append((tx0, top, tx1, bottom))
    return found


def _island_ink(vp: vector_walls.VectorPage, source: Source, box: Rect, rules: TraceRules) -> Image.Image:
    """The drawn ink of the zone's box with the walls (grown a little) taken out: joinery lines only."""
    k = rules.kitchen
    px = k.raster_mm_per_px
    ink = source.crop(*box, px).point(lambda v: 255 if v < k.dark_below else 0)
    walls = Image.frombytes("L", vp.size, vp.raster)  # 0 = wall
    c = (
        int(box[0] / vp.px_mm),
        int(box[1] / vp.px_mm),
        max(int(box[0] / vp.px_mm) + 1, int(box[2] / vp.px_mm)),
        max(int(box[1] / vp.px_mm) + 1, int(box[3] / vp.px_mm)),
    )
    solid = walls.crop(c).resize(ink.size).point(lambda v: 255 if v < 128 else 0)
    size = 2 * round(k.wall_clear_mm / px) + 1
    return ImageChops.subtract(ink, solid.filter(ImageFilter.MaxFilter(size)) if size > 1 else solid)


def _free_all_round(vp: vector_walls.VectorPage, rect: Rect, clear: float) -> bool:
    """No wall within `clear` of any side of the box."""
    step = vp.px_mm * 2
    n = 6
    for k in range(n + 1):
        t = k / n
        x, y = rect[0] + t * (rect[2] - rect[0]), rect[1] + t * (rect[3] - rect[1])
        for d in range(1, round(clear / step) + 1):
            out = d * step
            if any(
                vp.wall_at(px_, py_)
                for px_, py_ in (
                    (x, rect[1] - out),
                    (x, rect[3] + out),
                    (rect[0] - out, y),
                    (rect[2] + out, y),
                )
            ):
                return False
    return True


def _words_in(words: list[Word], rect: Rect) -> list[str]:
    return [
        w.text
        for w in words
        if rect[0] <= w.centre[0] <= rect[2] and rect[1] <= w.centre[1] <= rect[3] and not w.text.isdigit()
    ]


def read(
    vp: vector_walls.VectorPage, source: Source, spaces: list[Room], words: list[Word], rules: TraceRules
) -> list[Item]:
    """Island, fridge space and dishwasher provision in every kitchen zone."""
    k = rules.kitchen
    zones = [r for r in spaces if any(w in (r.name or "").upper() for w in k.zone_words)]
    found: dict[str, Item] = {}
    for zone in zones:
        box = zone.box()
        px = k.raster_mm_per_px
        ink = _island_ink(vp, source, box, rules)
        for x0, y0, x1, y1 in _boxes(ink, px, rules):
            rect = (x0 + box[0], y0 + box[1], x1 + box[0], y1 + box[1])
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            if not zone.contains(cx, cy) or not _free_all_round(vp, rect, k.island_clear_mm):
                continue
            length, depth = sorted((rect[2] - rect[0], rect[3] - rect[1]), reverse=True)
            inside = _words_in(words, rect)
            item = Item(
                f"K{round(cx / 10)}_{round(cy / 10)}",
                "ISLAND",
                rect,
                zone.id,
                k.evidence["island"].format(
                    length=round(length, -1),
                    depth=round(depth, -1),
                    where=where(vp, rect, rules),
                    inside=f"; written inside it: {' '.join(inside)}" if inside else "",
                ),
                CLEAR,
            )
            found.setdefault(item.id, item)
        taken: set[int] = set()
        for i, w in enumerate(words):
            cx, cy = w.centre
            if not zone.contains(cx, cy) or i in taken:
                continue
            text = w.text.upper()
            if text in {x.upper() for x in k.fridge_words}:
                rect = (
                    cx - k.fridge_half_mm,
                    cy - k.fridge_half_mm,
                    cx + k.fridge_half_mm,
                    cy + k.fridge_half_mm,
                )
                item = Item(
                    f"K{round(cx / 10)}_{round(cy / 10)}",
                    "FRIDGE SPACE",
                    rect,
                    zone.id,
                    k.evidence["fridge"].format(
                        word=w.text, where=where(vp, rect, rules), room=(zone.name or "the kitchen").title()
                    ),
                    UNSURE,
                )
                found.setdefault(item.id, item)
            elif text in {x.upper() for x in k.dishwasher_words}:
                mates = [
                    j
                    for j, o in enumerate(words)
                    if o.text.upper() in {x.upper() for x in k.provision_words}
                    and abs(o.centre[0] - cx) <= k.word_pair_mm
                    and abs(o.centre[1] - cy) <= k.word_pair_mm
                ]
                taken.update(mates)
                label = " ".join([w.text, *(words[j].text for j in mates)])
                half = k.word_pair_mm / 2
                rect = (cx - half, cy - half, cx + half, cy + half)
                item = Item(
                    f"K{round(cx / 10)}_{round(cy / 10)}",
                    "DISHWASHER PROVISION" if mates else "DISHWASHER",
                    rect,
                    zone.id,
                    k.evidence["dishwasher"].format(
                        word=label, where=where(vp, rect, rules), room=(zone.name or "the kitchen").title()
                    ),
                    CLEAR,
                )
                found.setdefault(item.id, item)
    return sorted(found.values(), key=lambda i: (round(i.rect[1] / 500), i.rect[0]))
