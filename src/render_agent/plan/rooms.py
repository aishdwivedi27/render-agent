"""Rooms read by code (PLAN_VISUALS_SPEC section 3; local, no model, no cost).

Every opening and every post-closed edge of an outdoor space is closed on the wall mask; each enclosed
region of free space is then a space. Its outline is the region's contour, so its corners are the wall
faces (no snapping). A region is named from the room-name text inside it (page words grouped by line); a
region with several labels is one room named as labelled; one with none is "Unlabelled space" and the table
asks the designer. Rules (word list, label spacing, smallest room) are in knowledge/plan_trace.yaml, `rooms`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from render_agent.plan import vector_walls
from render_agent.plan.openings import Opening
from render_agent.plan.source import Word
from render_agent.plan.trace_rules import TraceRules

Rect = tuple[float, float, float, float]
Point = tuple[float, float]


@dataclass
class Label:
    text: str
    words: list[Word]

    @property
    def centre(self) -> Point:
        xs = [(w.x0 + w.x1) / 2 for w in self.words]
        ys = [(w.y0 + w.y1) / 2 for w in self.words]
        return sum(xs) / len(xs), sum(ys) / len(ys)


@dataclass
class Room:
    id: str
    outline: list[Point]
    area_m2: float
    labels: list[str]
    outdoor: bool
    rects: list[Rect] = field(repr=False, default_factory=list)

    @property
    def name(self) -> str | None:
        return ", ".join(self.labels) if self.labels else None

    def box(self) -> Rect:
        xs, ys = [p[0] for p in self.outline], [p[1] for p in self.outline]
        return min(xs), min(ys), max(xs), max(ys)

    def contains(self, x: float, y: float) -> bool:
        return any(r[0] <= x < r[2] and r[1] <= y < r[3] for r in self.rects)

    def as_dict(self) -> dict[str, Any]:
        x0, y0, x1, y1 = self.box()
        return {
            "id": self.id,
            "name": self.name,
            "labels": self.labels,
            "outline": [[round(x, 1), round(y, 1)] for x, y in self.outline],
            "area_m2": round(self.area_m2, 2),
            "width_mm": round(x1 - x0),
            "depth_mm": round(y1 - y0),
            "outdoor": self.outdoor,
        }


def labels(words: list[Word], rules: TraceRules) -> list[Label]:
    """Room-name labels: words from the room-name list (with a following 1-2 digit number, as in "BED 3")
    that sit on one line close together."""
    r = rules.rooms
    vocab = {w.upper() for w in r.words}
    keep = [w for w in words if w.text.upper() in vocab or (w.text.isdigit() and len(w.text) <= 2)]
    keep.sort(key=lambda w: (round(w.centre[1] / r.label_line_mm), w.x0))
    lines: list[list[Word]] = []
    for w in keep:
        if lines and abs(lines[-1][-1].centre[1] - w.centre[1]) < r.label_line_mm / 2:
            lines[-1].append(w)
        else:
            lines.append([w])
    out: list[Label] = []
    for line in lines:
        line.sort(key=lambda w: w.x0)
        group: list[Word] = []
        for w in line:
            if group and w.x0 - group[-1].x1 > r.label_gap_mm:
                _flush(group, out, vocab)
                group = []
            group.append(w)
        _flush(group, out, vocab)
    return [_expand(lb, rules) for lb in out]


def _flush(group: list[Word], out: list[Label], vocab: set[str]) -> None:
    named = [w for w in group if w.text.upper() in vocab]
    if not named:
        return  # numbers on their own are dimensions
    start = group.index(named[0])
    words = group[start:]
    while words and words[-1].text.isdigit() and (len(words) < 2 or words[-2].text.isdigit()):
        words.pop()  # a second number is a dimension, not part of the name
    while words and words[-1].text == "&":
        words.pop()  # "&" joins two names; it cannot end one
    if words:
        out.append(Label(" ".join(w.text.upper() for w in words), words))


def _expand(label: Label, rules: TraceRules) -> Label:
    table = {k.upper(): v.upper() for k, v in rules.rooms.expand.items()}
    return Label(" ".join(table.get(t, t) for t in label.text.split()), label.words)


def _mask(vp: vector_walls.VectorPage, closed: list[Rect], seal_mm: float) -> Image.Image:
    """The wall mask with every closed break painted as wall; free space is 0 (dark) for the run finder."""
    walls = Image.frombytes("L", vp.size, vp.raster)
    draw = ImageDraw.Draw(walls)
    px = vp.px_mm
    for x0, y0, x1, y1 in closed:
        draw.rectangle([x0 / px, y0 / px, x1 / px - 1, y1 / px - 1], fill=0)
    walls = ImageOps.invert(walls)  # wall (0) -> 255, free (255) -> 0: free runs are "dark"
    grow = 2 * round(seal_mm / px) + 1  # walls grow a little so a pinhole in a wall does not leak a room
    return walls.filter(ImageFilter.MaxFilter(grow)) if grow > 1 else walls


def _grown_back(
    members: list[Rect], free0: Image.Image, px: float, seal_mm: float, tidy_mm: float
) -> list[Rect]:
    """The region found on the sealed mask, grown back out to the real wall faces: it grows by the seal
    and keeps only pixels that are free on the unsealed mask."""
    pad = round(seal_mm / px) + 2
    x0 = max(0, int(min(r[0] for r in members) / px) - pad)
    y0 = max(0, int(min(r[1] for r in members) / px) - pad)
    x1 = min(free0.width, int(max(r[2] for r in members) / px) + pad)
    y1 = min(free0.height, int(max(r[3] for r in members) / px) + pad)
    region = Image.new("L", (x1 - x0, y1 - y0), 0)
    draw = ImageDraw.Draw(region)
    for r in members:
        draw.rectangle([r[0] / px - x0, r[1] / px - y0, r[2] / px - x0 - 1, r[3] / px - y0 - 1], fill=255)
    grown = region.filter(ImageFilter.MaxFilter(2 * round(seal_mm / px) + 1))
    free = ImageOps.invert(free0.crop((x0, y0, x1, y1)))  # 255 = free
    both = ImageChops.darker(grown, free)  # 255 = free and in the region
    size = int(tidy_mm / px) | 1  # opening and closing: noise smaller than this goes
    if size > 1:
        both = both.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))
        both = both.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))
    kept = ImageOps.invert(both)  # 0 = free and in the region: a "dark run"
    return [
        ((a + x0) * px, (b + y0) * px, (c + x0) * px, (d + y0) * px)
        for a, b, c, d in vector_walls._rectangles(kept.tobytes(), kept.width, kept.height)
    ]


def read(
    vp: vector_walls.VectorPage,
    openings: list[Opening],
    outdoor_edges: list[Rect],
    words: list[Word],
    rules: TraceRules,
) -> list[Room]:
    """The enclosed spaces of the page, named from the labels inside them, in reading order."""
    px = vp.px_mm
    closed = [o.gap.frame for o in openings] + outdoor_edges
    free = _mask(vp, closed, rules.rooms.seal_mm)
    free0 = _mask(vp, closed, 0)
    w, h = free.size
    rects = [
        (x0 * px, y0 * px, x1 * px, y1 * px)
        for x0, y0, x1, y1 in vector_walls._rectangles(free.tobytes(), w, h)
    ]
    roots = vector_walls._roots(rects, px * 0.5)
    groups: dict[int, list[Rect]] = {}
    for rect, root in zip(rects, roots, strict=True):
        groups.setdefault(root, []).append(rect)
    named = labels(words, rules)
    rooms: list[Room] = []
    for members in groups.values():
        x0 = min(r[0] for r in members)
        y0 = min(r[1] for r in members)
        x1 = max(r[2] for r in members)
        y1 = max(r[3] for r in members)
        if x0 <= px or y0 <= px or x1 >= w * px - px or y1 >= h * px - px:
            continue  # touches the page edge: the outside
        members = _grown_back(members, free0, px, rules.rooms.seal_mm, rules.rooms.tidy_mm)
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in members) / 1e6
        if area < rules.rooms.min_area_m2:
            continue
        loops = [
            pts
            for pts, hole in vector_walls.outlines(members, px, rules.walls.outline_min_jog_mm)
            if not hole
        ]
        if not loops:
            continue
        outline = max(loops, key=_area)
        zone_words = [m for z in ("alfresco", "porch") for m in rules.areas.room_words.get(z, [])]
        room = Room("", outline, area, [], False, members)
        mine = [lb for lb in named if room.contains(*lb.centre)]
        mine.sort(key=lambda lb: (round(lb.centre[1] / 500), lb.centre[0]))
        room.labels = [lb.text for lb in mine]
        room.outdoor = any(z in lb for lb in room.labels for z in zone_words)
        rooms.append(room)
    rooms.sort(key=lambda r: (round(r.box()[1] / 1000), r.box()[0]))
    for room in rooms:
        x0, y0, x1, y1 = room.box()
        room.id = f"R{round((x0 + x1) / 20)}_{round((y0 + y1) / 20)}"
    return rooms


def _area(pts: list[Point]) -> float:
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts))) / 2)


def room_at(rooms: list[Room], x: float, y: float) -> Room | None:
    return next((r for r in rooms if r.contains(x, y)), None)


def sides(opening: Opening, rooms: list[Room], px: float) -> tuple[Room | None, Room | None]:
    """The rooms on the two sides of an opening: (above or left, below or right)."""
    g = opening.gap
    cx, cy = g.centre
    reach = g.thickness / 2 + 3 * px
    if g.axis == "h":
        return room_at(rooms, cx, cy - reach), room_at(rooms, cx, cy + reach)
    return room_at(rooms, cx - reach, cy), room_at(rooms, cx + reach, cy)
