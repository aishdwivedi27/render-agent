"""Recesses read by code (PLAN_VISUALS_SPEC section 3; local, no model, no cost).

Pockets are deduced from the shape of each room's outline, not from symbols: the room's main body is found
with an opening filter as big as most of the room, and what is left over (narrow enough to fall through it)
is a pocket. Each pocket is classified by depth AND position (rules in knowledge/plan_trace.yaml,
`recesses`): a deep narrow pocket along a bedroom wall is a robe, a small square one in a wet area a shower
recess, a shallow one off a hall a linen cupboard, a short return a nib. A depth outside every band, or a
band that does not fit the room, is Orange: the pocket is "other", the best-fitting kind its alternative.

A robe drawn as a plain rectangle against a bedroom wall (no jog in the wall) is read from the drawing's
lines with the same depth rules, and is never better than Orange: only the drawing's outline backs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image

from render_agent.plan.rooms import Room
from render_agent.plan.source import Source
from render_agent.plan.trace_rules import Recess, TraceRules

Rect = tuple[float, float, float, float]
CLEAR, UNSURE = "clear", "unsure"
OTHER = "other"  # a pocket whose size fits a kind but not the room it is in


@dataclass
class Found:
    id: str
    kind: str
    alternative: str | None
    confidence: str
    evidence: str
    rect: Rect
    room: Room
    source: str  # "wall_jog" or "drawn_rectangle"
    depth_mm: float
    length_mm: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "alternative": self.alternative,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "rect": [round(v, 1) for v in self.rect],
            "room": self.room.id,
            "room_name": self.room.name,
            "source": self.source,
            "depth_mm": round(self.depth_mm),
            "length_mm": round(self.length_mm),
        }


def _fits(kind: Recess, depth: float, length: float) -> bool:
    aspect = length / depth if depth else 0
    return kind.min_depth_mm <= depth <= kind.max_depth_mm and kind.min_aspect <= aspect <= kind.max_aspect


def _room_matches(kind: Recess, room: Room) -> bool:
    if not kind.rooms:  # a kind with no rooms listed (a nib) suits every room
        return True
    name = (room.name or "").upper()
    return any(w in name for w in kind.rooms)


def classify(depth: float, length: float, room: Room, rules: TraceRules) -> tuple[str, str | None, str, bool]:
    """(kind, alternative, confidence, fits a band) for a pocket of this size in this room."""
    kinds = rules.recesses
    fitting = [k for k, r in kinds.items() if _fits(r, depth, length)]
    middle = lambda k: abs(depth - (kinds[k].min_depth_mm + kinds[k].max_depth_mm) / 2)  # noqa: E731
    fitting.sort(key=middle)  # the kind whose depth band is centred nearest the pocket first
    here = [k for k in fitting if _room_matches(kinds[k], room)]
    if len(here) == 1:
        return here[0], None, CLEAR, True
    if here:  # several kinds fit this room: the first listed, the next as the alternative
        return here[0], here[1], UNSURE, True
    if fitting:  # the size fits but not the room (or the room has no name): not a kind of its own, Orange
        # (a "robe" in a living room or an ensuite is a guess code does not make; the best-fitting kind is
        # the alternative the designer is asked about)
        return OTHER, fitting[0], UNSURE, True
    nearest = sorted(
        kinds, key=lambda k: min(abs(depth - kinds[k].min_depth_mm), abs(depth - kinds[k].max_depth_mm))
    )
    return nearest[0], nearest[1] if len(nearest) > 1 else None, UNSURE, False


def _unit(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    length = max(abs(b[0] - a[0]) + abs(b[1] - a[1]), 1e-9)  # orthogonal edges: one of the two is zero
    return (b[0] - a[0]) / length, (b[1] - a[1]) / length


def _bbox(*points: tuple[float, float]) -> Rect:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def pockets(room: Room, rules: TraceRules) -> list[Rect]:
    """The pockets of one room's outline, read from its corners (counter-clockwise, y down):

    - a step in a wall line (one convex and one concave corner on an edge) leaves a lobe as deep as the
      step and as long as the edge that runs on from its convex corner;
    - a U-shaped alcove (two convex corners) is as wide as its back and as deep as its sides.

    Lobes longer than `max_share` of the room are the room's body (an L-shaped room), not pockets."""
    s = rules.recess_search
    deepest = max(r.max_depth_mm for r in rules.recesses.values())
    pts = list(room.outline)
    area2 = sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))
    if area2 > 0:  # make the corners turn the same way for every room
        pts.reverse()
    n = len(pts)
    out: list[Rect] = []
    for i in range(n):
        a, b, c, d = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        p, e, q = _unit(a, b), _unit(b, c), _unit(c, d)
        lp = abs(b[0] - a[0]) + abs(b[1] - a[1])
        lq = abs(d[0] - c[0]) + abs(d[1] - c[1])
        turn1 = p[0] * e[1] - p[1] * e[0]
        turn2 = e[0] * q[1] - e[1] * q[0]
        if turn1 * turn2 == 0:
            continue
        if turn1 > 0 and turn2 > 0:  # an alcove: both corners stick out
            depth = min(lp, lq)
            box = _bbox(
                b, c, (b[0] - p[0] * depth, b[1] - p[1] * depth), (c[0] + q[0] * depth, c[1] + q[1] * depth)
            )
        elif turn1 > 0:  # a step whose convex corner is the start of the edge
            box = _bbox(b, c, (b[0] - p[0] * lp, b[1] - p[1] * lp), (c[0] - p[0] * lp, c[1] - p[1] * lp))
        elif turn2 > 0:  # a step whose convex corner is the end of the edge
            box = _bbox(b, c, (c[0] + q[0] * lq, c[1] + q[1] * lq), (b[0] + q[0] * lq, b[1] + q[1] * lq))
        else:
            continue  # a wall return into the room (a nib) is not a pocket of the room
        depth = min(box[2] - box[0], box[3] - box[1])
        if (
            not s.min_pocket_mm <= depth <= deepest  # deeper than any recess: part of the room
            or (box[2] - box[0]) * (box[3] - box[1]) > s.max_share * room.area_m2 * 1e6
        ):
            continue
        if not any(_overlaps(box, o) and _same(box, o) for o in out):
            out.append(box)
    return out


def _same(a: Rect, b: Rect, tol: float = 50.0) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _drawn(room: Room, source: Source, rules: TraceRules, taken: list[Rect]) -> list[Rect]:
    """Rectangles drawn against a wall of the room: a line parallel to the wall at robe depth, with a
    line at each end back to the wall."""
    s = rules.recess_search
    px = s.drawn_raster_mm_per_px
    names = [k for k in s.drawn_rect_kinds if k in rules.recesses and _room_matches(rules.recesses[k], room)]
    if not names:
        return []
    kinds = [rules.recesses[k] for k in names]
    min_len = min(s.drawn_min_mm.get(k, s.drawn_rect_min_mm) for k in names)
    tol = s.front_line_tol_mm
    corner = any(k in s.drawn_corner_kinds for k in names)  # a tray may fill the wall between two walls
    m = s.front_line_margin_mm if any(k in s.drawn_corner_kinds for k in names) else 0.0
    lo = min(k.min_depth_mm for k in kinds)
    hi = max(k.max_depth_mm for k in kinds)
    found: list[Rect] = []
    pts = room.outline
    for i in range(len(pts)):
        (ax, ay), (bx, by) = pts[i - 1], pts[i]
        if abs(ax - bx) < 1 and abs(ay - by) < 1:
            continue
        horizontal = abs(ay - by) < abs(ax - bx)
        a0, a1 = sorted((ax, bx) if horizontal else (ay, by))
        if a1 - a0 < min_len:
            continue
        face = ay if horizontal else ax
        mid = (a0 + a1) / 2
        probe = (mid, face + 2 * px) if horizontal else (face + 2 * px, mid)
        inward = 1 if room.contains(*probe) else -1
        reach = hi + 60
        # the strip runs `m` beyond each end of the wall, so an end wall or a corner is inside it
        if horizontal:
            box = (a0 - m, min(face, face + inward * reach), a1 + m, max(face, face + inward * reach))
        else:
            box = (min(face, face + inward * reach), a0 - m, max(face, face + inward * reach), a1 + m)
        strip = source.crop(*box, px)
        if not horizontal:
            strip = strip.transpose(Image.Transpose.TRANSPOSE)  # rows = depth, columns run along the wall
        if inward < 0:
            strip = strip.transpose(Image.Transpose.FLIP_TOP_BOTTOM)  # row 0 is the wall face
        for hit in _rectangle_in(
            strip,
            px,
            lo,
            hi,
            min_len,
            s.drawn_rect_cover,
            s.drawn_dark_below,
            s.drawn_skip_mm,
            s.end_wall_mm,
        ):
            c0, c1, depth = hit
            along = (a0 - m + c0 * px, a0 - m + (c1 + 1) * px)
            if along[0] < a0 - tol or along[1] > a1 + tol:
                continue  # a rectangle beyond this wall, not against it
            if not corner and along[1] - along[0] > s.drawn_max_share * (a1 - a0):
                continue  # as long as the wall: a grid or dimension line, not a pocket
            rect = (
                (
                    along[0],
                    face if inward > 0 else face - depth,
                    along[1],
                    face + depth if inward > 0 else face,
                )
                if horizontal
                else (
                    face if inward > 0 else face - depth,
                    along[0],
                    face + depth if inward > 0 else face,
                    along[1],
                )
            )
            if not any(_overlaps(rect, t) for t in taken + found):
                found.append(rect)
    return found


def _rectangle_in(
    strip: Image.Image,
    px: float,
    lo: float,
    hi: float,
    min_len: float,
    cover: float,
    dark: int,
    skip: float,
    end_wall_mm: float,
) -> list[tuple[int, int, float]]:
    """Rectangles in a strip whose rows are depth from the wall and columns run along it: (first column,
    last column, depth mm)."""
    w, h = strip.size
    data = strip.tobytes()
    start = round(skip / px)
    wall = round(end_wall_mm / px)
    out: list[tuple[int, int, float]] = []
    for row in range(max(start, round(lo / px)), min(h, round(hi / px) + 1)):
        line = [data[row * w + c] < dark for c in range(w)]
        c = 0
        while c < w:
            if not line[c]:
                c += 1
                continue
            e = c
            while e < w and line[e]:
                e += 1
            ends_ok = _side(data, w, c, row, start, dark, cover) and _side(
                data, w, e - 1, row, start, dark, cover
            )
            first, last = _inner(data, w, c, e - 1, row, start, dark, cover, wall)
            if (last + 1 - first) * px >= min_len and ends_ok:
                out.append((first, last, row * px))
            c = e
    merged: list[tuple[int, int, float]] = []
    for hit in out:  # neighbouring rows of one thick line are one rectangle
        if not any(
            abs(m[0] - hit[0]) <= 2 and abs(m[1] - hit[1]) <= 2 and abs(m[2] - hit[2]) <= 3 * px
            for m in merged
        ):
            merged.append(hit)
    return merged


def _inner(
    data: bytes, w: int, first: int, last: int, row: int, start: int, dark: int, cover: float, wall: int
) -> tuple[int, int]:
    """The columns of a drawn line between its end walls. An end wall (full depth and at least `wall`
    columns thick) is not part of the pocket; a thin line back to the wall is its edge and stays."""
    lo = first
    while lo < last and _full(data, w, lo, row, start, dark, cover):
        lo += 1
    hi = last
    while hi > first and _full(data, w, hi, row, start, dark, cover):
        hi -= 1
    return (lo if lo - first >= wall else first), (hi if last - hi >= wall else last)


def _full(data: bytes, w: int, col: int, row: int, start: int, dark: int, cover: float) -> bool:
    """A column that is dark from the wall to `row`: an end wall or a line back to the wall."""
    if row <= start:
        return False
    return sum(1 for r in range(start, row) if data[r * w + col] < dark) / (row - start) >= cover


def _side(data: bytes, w: int, col: int, row: int, start: int, dark: int, cover: float) -> bool:
    """A line back to the wall at this column (or a neighbour): dark on most rows from the wall to `row`."""
    for c in (col - 1, col, col + 1):
        if 0 <= c < w:
            hits = sum(1 for r in range(start, row) if data[r * w + c] < dark)
            if row > start and hits / (row - start) >= cover:
                return True
    return False


def _refined(room: Room, box: Rect, source: Source, rules: TraceRules) -> Rect:
    """A wall jog's box with its depth and length read from the drawn rectangle when there is one: the
    room outline can be cut short by a door closure beside the pocket, but the drawing shows the front line
    of the pocket and its end walls."""
    s = rules.recess_search
    px = s.drawn_raster_mm_per_px
    kinds = [rules.recesses[k] for k in s.front_line_kinds if k in rules.recesses]
    if not kinds:
        return box
    hi = max(k.max_depth_mm for k in kinds)
    horizontal = (box[2] - box[0]) >= (box[3] - box[1])
    a0, a1 = (box[0], box[2]) if horizontal else (box[1], box[3])
    d0, d1 = (box[1], box[3]) if horizontal else (box[0], box[2])
    mid = (a0 + a1) / 2
    if horizontal:
        toward_high, toward_low = room.contains(mid, d1 + 2 * px), room.contains(mid, d0 - 2 * px)
    else:
        toward_high, toward_low = room.contains(d1 + 2 * px, mid), room.contains(d0 - 2 * px, mid)
    if toward_high == toward_low:
        return box
    inward = 1 if toward_high else -1  # the room's body is beyond the high edge (the pocket opens that way)
    face = d0 if inward > 0 else d1  # the back wall of the pocket
    reach = hi + 60
    m = s.front_line_margin_mm
    lo_d, hi_d = (face, face + reach) if inward > 0 else (face - reach, face)
    crop = (a0 - m, lo_d, a1 + m, hi_d) if horizontal else (lo_d, a0 - m, hi_d, a1 + m)
    strip = source.crop(*crop, px)
    if not horizontal:
        strip = strip.transpose(Image.Transpose.TRANSPOSE)
    if inward < 0:
        strip = strip.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    depth = d1 - d0
    hits = _rectangle_in(
        strip, px, max(s.drawn_skip_mm, depth - 60), hi, s.drawn_rect_min_mm, s.drawn_rect_cover,
        s.drawn_dark_below, s.drawn_skip_mm, s.end_wall_mm,
    )  # fmt: skip
    for c0, c1, line in sorted(hits, key=lambda h: h[2]):
        along = (a0 - m + c0 * px, a0 - m + (c1 + 1) * px)
        if abs(along[0] - a0) <= s.front_line_tol_mm and abs(along[1] - a1) <= s.front_line_tol_mm:
            front = face + inward * line
            lo_f, hi_f = sorted((face, front))
            return (along[0], lo_f, along[1], hi_f) if horizontal else (lo_f, along[0], hi_f, along[1])
    return box


def _ink(box: Rect, source: Source, rules: TraceRules) -> float:
    """The share of dark pixels inside a rectangle, read a little in from its lines."""
    s = rules.recess_search
    m = s.drawn_ink_inset_mm
    x0, y0, x1, y1 = box[0] + m, box[1] + m, box[2] - m, box[3] - m
    if x1 <= x0 or y1 <= y0:
        return 0.0
    data = source.crop(x0, y0, x1, y1, s.drawn_raster_mm_per_px).tobytes()
    return sum(1 for v in data if v < s.drawn_dark_below) / max(len(data), 1)


def _inside(a: Rect, b: Rect, tol: float) -> bool:
    """Box `a` lies within box `b` (give or take `tol`)."""
    return a[0] >= b[0] - tol and a[1] >= b[1] - tol and a[2] <= b[2] + tol and a[3] <= b[3] + tol


def _overlaps(a: Rect, b: Rect) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def read(rooms: list[Room], source: Source | None, rules: TraceRules) -> list[Found]:
    """Every recess of the page: wall jogs from the room outlines, then drawn rectangles."""
    out: list[Found] = []
    ev = rules.recess_search.evidence
    for room in rooms:
        if room.outdoor:
            continue
        first = len(out)  # this room's wall jogs start here
        taken: list[Rect] = []
        for box in pockets(room, rules):
            if source is not None:
                box = _refined(room, box, source, rules)
            depth = min(box[2] - box[0], box[3] - box[1])
            length = max(box[2] - box[0], box[3] - box[1])
            kind, alternative, confidence, fits = classify(depth, length, room, rules)
            template = ev["wall_jog"] if fits else ev["no_band"]
            fields = {
                "depth": str(round(depth)),
                "length": str(round(length)),
                "room": room.name or "an unlabelled space",
            }
            out.append(
                Found(
                    f"{room.id}_{kind}_{round((box[0] + box[2]) / 20)}_{round((box[1] + box[3]) / 20)}",
                    kind,
                    alternative,
                    confidence,
                    template.format_map(fields),
                    box,
                    room,
                    "wall_jog",
                    depth,
                    length,
                )
            )
            taken.append(box)
        if source is None:
            continue
        for box in _drawn(room, source, rules, []):
            depth = min(box[2] - box[0], box[3] - box[1])
            length = max(box[2] - box[0], box[3] - box[1])
            drawn_kind = next(
                (
                    k
                    for k in rules.recess_search.drawn_rect_kinds
                    if _fits(rules.recesses[k], depth, length) and _room_matches(rules.recesses[k], room)
                ),
                None,
            )
            if drawn_kind is None:
                continue
            if _ink(box, source, rules) > rules.recess_search.drawn_max_ink:
                continue  # hatched or filled: a clearance square or a fixture, not a recess
            kind = drawn_kind
            tol = rules.recess_search.front_line_tol_mm
            if kind in rules.recess_search.drawn_corner_kinds:
                # a tray drawn in a corner is the whole recess: a wall jog that is only a part of it (the
                # room outline follows the tray's edge) is replaced by it
                swallowed = [f for f in out[first:] if _inside(f.rect, box, tol)]
                out[first:] = [f for f in out[first:] if f not in swallowed]
            elif any(_overlaps(box, t) for t in taken):
                continue
            horizontal = (box[2] - box[0]) >= (box[3] - box[1])
            sides = rules.recess_search.sides
            rx0, ry0, rx1, ry1 = room.box()
            if horizontal:
                side = sides[0] if abs(box[1] - ry0) < abs(box[3] - ry1) else sides[1]
            else:
                side = sides[2] if abs(box[0] - rx0) < abs(box[2] - rx1) else sides[3]
            fields = {
                "depth": str(round(depth)),
                "length": str(round(length)),
                "room": room.name or "an unlabelled space",
                "side": side,
            }
            out.append(
                Found(
                    f"{room.id}_{kind}_{round((box[0] + box[2]) / 20)}_{round((box[1] + box[3]) / 20)}",
                    kind,
                    None,
                    UNSURE,
                    ev["drawn"].format_map(fields),
                    box,
                    room,
                    "drawn_rectangle",
                    depth,
                    length,
                )
            )
    return out
