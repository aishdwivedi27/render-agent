"""Openings read by code (PLAN_VISUALS_SPEC section 3; local, no model, no cost).

Every break in the wall line is an opening. The breaks come from `vector_walls.closures` (two free wall ends
that face each other). Each one is classified from what is drawn inside and beside it, read from a crop of
the page (the rules are in knowledge/plan_trace.yaml, `openings`):

- a quarter-circle swing arc about as big as the break, starting at one end of it: a hinged door (the hinge
  is the end the arc starts from);
- two or more continuous thin lines across an exterior break: a window (a code taller than a door height
  with one line is a sliding door);
- the text "csd" beside a break with no arc: a cavity slider;
- an exterior break as wide as a garage door: a garage door;
- nothing across it: an open walkway (a dashed line only makes it an arch candidate, so Orange).

A break that matches none of these, or more than one, is Orange and carries both readings. Window codes
are the nearest code-shaped words on the outside; two or none is Orange.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import ImageFilter

from render_agent.plan import areas, vector_walls
from render_agent.plan.source import Source, Word, distance_to_rect
from render_agent.plan.trace_rules import TraceRules

Rect = tuple[float, float, float, float]
CLEAR, UNSURE = "clear", "unsure"


@dataclass
class Gap:
    frame: Rect
    axis: str  # "h": a horizontal wall (the break runs along x), "v": a vertical wall
    exterior: bool = False
    across: bool = False  # the line between two wall ends side by side (a garage front)
    meets_wall: bool = False  # one side is a free wall end, the other a wall running across it

    @property
    def width(self) -> float:
        return (self.frame[2] - self.frame[0]) if self.axis == "h" else (self.frame[3] - self.frame[1])

    @property
    def thickness(self) -> float:
        return (self.frame[3] - self.frame[1]) if self.axis == "h" else (self.frame[2] - self.frame[0])

    @property
    def centre(self) -> tuple[float, float]:
        return (self.frame[0] + self.frame[2]) / 2, (self.frame[1] + self.frame[3]) / 2


@dataclass
class Features:
    arc: float = 0.0
    arc_end: int = 0  # 0: the low end (left or top) is the hinge
    leaf: float = 0.0  # share of the door leaf drawn from the hinge
    arc_side: int = 1  # -1: above or left of the wall, 1: below or right
    lines: int = 0
    dashed: bool = False
    words: list[str] = field(default_factory=list)  # cavity or garage words found beside it


@dataclass
class Opening:
    id: str
    kind: str
    alternative: str | None
    confidence: str  # CLEAR or UNSURE
    evidence: str
    gap: Gap
    features: Features
    code: str | None = None
    code_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "alternative": self.alternative,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "frame": [round(v, 1) for v in self.gap.frame],
            "axis": self.gap.axis,
            "width_mm": round(self.gap.width),
            "position_mm": round(self.gap.centre[0] if self.gap.axis == "h" else self.gap.centre[1]),
            "wall_line_mm": round(self.gap.centre[1] if self.gap.axis == "h" else self.gap.centre[0]),
            "exterior": self.gap.exterior,
            "hinge_end": self.features.arc_end if self.kind == "hinged_door" else None,
            "swing_side": self.features.arc_side if self.kind == "hinged_door" else None,
            "code": self.code,
            "code_note": self.code_note,
        }


def _overlap(a: Gap, b: Gap) -> bool:
    """Two breaks on the same wall line that cover mostly the same stretch."""
    if a.axis != b.axis:
        return False
    i = 0 if a.axis == "h" else 1
    lo, hi = max(a.frame[i], b.frame[i]), min(a.frame[i + 2], b.frame[i + 2])
    across = abs((a.centre[1 - i]) - (b.centre[1 - i]))
    return hi - lo >= 0.5 * min(a.width, b.width) and across <= max(a.thickness, b.thickness)


def _thin_enough(gap: Gap, rules: TraceRules) -> bool:
    return rules.walls.min_wall_mm <= gap.thickness <= rules.openings.max_thickness_mm


def _wall_like(gap: Gap, rules: TraceRules) -> bool:
    """Wide enough to be an opening and cut in a wall as thick as a single wall."""
    return gap.width >= rules.openings.min_gap_mm and _thin_enough(gap, rules)


def _at_post(gap: Gap, posts: list[Rect], reach: float) -> bool:
    """A post stands at either end of the break."""
    x0, y0, x1, y1 = gap.frame
    ends = [(x0, y0, x0, y1), (x1, y0, x1, y1)] if gap.axis == "h" else [(x0, y0, x1, y0), (x0, y1, x1, y1)]
    return any(
        distance_to_rect(((e[0] + e[2]) / 2, (e[1] + e[3]) / 2), p) <= reach / 2 for e in ends for p in posts
    )


def candidate_gaps(
    vp: vector_walls.VectorPage, rules: TraceRules, outdoor: list[Rect] | None = None
) -> list[Gap]:
    """The breaks in the wall line, one per opening: widths in range, the tightest of any overlapping pair.
    Breaks closed by posts (the open side of an alfresco or porch) are not openings; they are added to
    `outdoor` so the rooms module can close them."""
    found = vector_walls.closures(vp, rules.areas.footprint_max_gap_mm, rules.walls.min_wall_mm)
    posts = vector_walls.piers(vp, rules.walls)
    gaps = []
    for r in found:
        axis = "h" if (r[2] - r[0]) >= (r[3] - r[1]) else "v"
        g = Gap(r, axis)
        if g.width > rules.openings.max_gap_mm and _at_post(g, posts, vp.end_max_mm):
            if outdoor is not None:
                outdoor.append(g.frame)
            continue  # the open side of an alfresco or porch, closed by its posts: not a door
        if _wall_like(g, rules):
            gaps.append(g)
    for r in vector_walls.across_ends(
        vp,
        rules.openings.min_gap_mm,
        rules.areas.footprint_max_gap_mm,
        rules.walls.min_wall_mm,
        rules.openings.across_max_thickness_mm,
    ):
        g = Gap(r, "h" if (r[2] - r[0]) >= (r[3] - r[1]) else "v", across=True)
        narrow = g.width <= rules.openings.walkway_max_mm  # a walkway between rooms, else a garage front
        wide = g.width >= rules.openings.garage_min_mm
        if (narrow or wide) and _thin_enough(g, rules) and not _at_post(g, posts, vp.end_max_mm):
            gaps.append(g)
    for r in vector_walls.end_to_wall(vp, rules.openings.walkway_max_mm, rules.walls.min_wall_mm):
        axis = "h" if (r[2] - r[0]) >= (r[3] - r[1]) else "v"
        g = Gap(r, axis, meets_wall=True)
        if _wall_like(g, rules):
            gaps.append(g)
    gaps.sort(key=lambda g: (g.meets_wall, g.width))  # breaks between two ends win over end-to-wall ones
    kept: list[Gap] = []
    for g in gaps:
        if not any(_overlap(g, k) for k in kept):
            kept.append(g)
    return kept


def mark_exterior(gaps: list[Gap], vp: vector_walls.VectorPage, rules: TraceRules) -> None:
    """A break is exterior when the footprint's outside touches either side of it."""
    if not gaps or not vp.walls:
        return
    polys = [vector_walls.polygon(r) for r in vp.walls]
    # posts are not closed here: an alfresco or porch is outside the building
    closed = vector_walls.closures(vp, rules.areas.footprint_max_gap_mm, rules.walls.min_wall_mm)
    grid, img = areas.footprint(
        [p for poly in polys for p in poly], polys, closed, rules.areas.raster_mm_per_px
    )
    pix = img.load()
    assert pix is not None
    step = grid.px * 3
    for g in gaps:
        cx, cy = g.centre
        reach = g.thickness / 2 + step
        probes = (
            [(cx, cy - reach), (cx, cy + reach)] if g.axis == "h" else [(cx - reach, cy), (cx + reach, cy)]
        )
        for p in probes:
            gx, gy = grid.pt(p)
            if 0 <= int(gx) < grid.w and 0 <= int(gy) < grid.h and pix[int(gx), int(gy)] == areas.OUTSIDE:
                g.exterior = True


def _rows(source: Source, gap: Gap, rules: TraceRules) -> tuple[int, bool]:
    """Continuous lines and whether a dashed line runs along the break (rows of a crop of the break)."""
    o = rules.openings
    x0, y0, x1, y1 = gap.frame
    im = source.crop(x0, y0, x1, y1, o.raster_mm_per_px)
    w, h = im.size
    data = im.tobytes()
    along = range(int(w * 0.1), int(w * 0.9)) if gap.axis == "h" else range(int(h * 0.1), int(h * 0.9))
    count = h if gap.axis == "h" else w
    lines, previous, dashed = 0, False, False
    for k in range(count):
        row = [(data[k * w + a] if gap.axis == "h" else data[a * w + k]) < o.dark_below for a in along]
        cover = sum(row) / max(1, len(row))
        on = cover >= o.line_cover
        lines += on and not previous
        previous = on
        flips = sum(1 for a, b in zip(row, row[1:], strict=False) if a != b)
        if o.dash_cover[0] <= cover < o.dash_cover[1] and flips >= o.dash_transitions:
            dashed = True
    return lines, dashed


def _arc(source: Source, gap: Gap, rules: TraceRules) -> tuple[float, int, int, float]:
    """Best share of a quarter circle drawn around either end of the break, on either side of the wall:
    (share, hinge end, side, share of the door leaf drawn from that hinge)."""
    o = rules.openings
    width = gap.width
    reach = width + 60
    x0, y0, x1, y1 = gap.frame
    if gap.axis == "h":
        box = (x0 - 60, y0 - reach, x1 + 60, y1 + reach)
        ends = [(x0, gap.centre[1]), (x1, gap.centre[1])]
    else:
        box = (x0 - reach, y0 - 60, x1 + reach, y1 + 60)
        ends = [(gap.centre[0], y0), (gap.centre[0], y1)]
    px = o.raster_mm_per_px
    grown = 2 * round(o.arc_tolerance_mm / px) + 1  # dark pixels grow by the tolerance
    im = source.crop(*box, px).filter(ImageFilter.MinFilter(max(3, grown)))
    pix = im.load()
    assert pix is not None
    iw, ih = im.size
    best = (0.0, 0, 1, 0.0)
    for end, (ex, ey) in enumerate(ends):
        inward = 1 if end == 0 else -1
        for side in (-1, 1):
            for share in o.arc_radii:
                r = width * share
                ring = _ring(pix, iw, ih, box, px, (ex, ey), gap.axis, inward, side, r, o.dark_below)
                if ring > best[0]:
                    control = max(
                        _ring(pix, iw, ih, box, px, (ex, ey), gap.axis, inward, side, r * k, o.dark_below)
                        for k in (0.7, 1.3)
                    )
                    if ring - control >= o.arc_contrast:  # a line, not hatching or a filled area
                        leaf = _leaf(pix, iw, ih, box, px, (ex, ey), gap.axis, side, r, o.dark_below)
                        best = (ring, end, side, leaf)
    return best


def _ring(
    pix: Any,
    iw: int,
    ih: int,
    box: Rect,
    px: float,
    centre: tuple[float, float],
    axis: str,
    inward: int,
    side: int,
    r: float,
    dark: int,
) -> float:
    """Share of a quarter circle of radius `r` about `centre` that is drawn."""
    hit = total = 0
    for k in range(3, 30):  # 3 degree steps, clear of the wall line at both ends
        a = math.radians(k * 3)
        if axis == "h":
            x, y = centre[0] + inward * r * math.cos(a), centre[1] + side * r * math.sin(a)
        else:
            x, y = centre[0] + side * r * math.sin(a), centre[1] + inward * r * math.cos(a)
        ix, iy = int((x - box[0]) / px), int((y - box[1]) / px)
        total += 1
        if 0 <= ix < iw and 0 <= iy < ih and int(pix[ix, iy]) < dark:
            hit += 1
    return hit / total


def _leaf(
    pix: Any,
    iw: int,
    ih: int,
    box: Rect,
    px: float,
    hinge: tuple[float, float],
    axis: str,
    side: int,
    r: float,
    dark: int,
) -> float:
    """Share of the line from the hinge straight out across the wall (the open door leaf) that is drawn."""
    hit = total = 0
    for k in range(3, 20):
        t = r * (0.15 + 0.8 * k / 19)
        x, y = (hinge[0], hinge[1] + side * t) if axis == "h" else (hinge[0] + side * t, hinge[1])
        ix, iy = int((x - box[0]) / px), int((y - box[1]) / px)
        total += 1
        if 0 <= ix < iw and 0 <= iy < ih and int(pix[ix, iy]) < dark:
            hit += 1
    return hit / total


def _phrase_near(words: list[Word], gap: Gap, phrases: list[str], reach: float) -> list[str]:
    """Phrases (upper case) found in the words within `reach` of the break, matched across word breaks."""
    near = [w for w in words if distance_to_rect(w.centre, gap.frame) <= reach]
    near.sort(key=lambda w: (round(w.centre[1] / 100), w.x0))
    text = " ".join(w.text.upper() for w in near)
    return [p for p in phrases if p.upper() in text]


def read_gap(
    gap: Gap,
    source: Source,
    words: list[Word],
    rules: TraceRules,
    cavity: str | None = None,
    codes: list[str] | None = None,
) -> tuple[str, str | None, str, Features, dict[str, str]]:
    """(kind, alternative, confidence, features, evidence fields) for one break."""
    o = rules.openings
    f = Features()
    garage_size = gap.exterior and gap.width >= o.garage_min_mm
    f.lines, f.dashed = _rows(source, gap, rules)
    if codes and (gap.exterior or f.lines >= o.window_lines_min) and not garage_size:
        # a window code (height-width) written beside an exterior break: a window or, for a door height,
        # a sliding door. Doors carry no such code, so no swing arc is looked for.
        height = int(codes[0][:2])
        door = height >= o.door_code_height
        fields = {"gap_mm": str(round(gap.width)), "lines": str(f.lines), "code": codes[0]}
        return (
            "sliding_door" if door else "window",
            "window" if door else None,
            CLEAR if len(codes) == 1 else UNSURE,
            f,
            fields,
        )
    if o.door_min_mm <= gap.width <= o.door_max_mm:
        f.arc, f.arc_end, f.arc_side, f.leaf = _arc(source, gap, rules)
    f.words = ([cavity] if cavity else []) + _phrase_near(words, gap, o.garage_words, o.text_search_mm)
    if cavity and f.arc < o.arc_over_cavity:
        f.arc = 0.0  # "csd" written beside the break: a cavity slider, not a door (a clear arc still wins)
    marks = {
        "swing arc": f.arc >= o.arc_min and f.leaf >= o.leaf_min,
        "lines": f.lines >= o.window_lines_min,
        "garage": garage_size,
    }
    fields = {"gap_mm": str(round(gap.width))}
    if garage_size:
        fields["words"] = f" ({', '.join(w.lower() for w in f.words)} written beside it)" if f.words else ""
        return "garage_door", (None if f.words else "open_walkway"), (CLEAR if f.words else UNSURE), f, fields
    if marks["swing arc"] and (gap.meets_wall or gap.across) and f.arc < o.arc_over_lines:
        marks["swing arc"] = False  # a neighbouring door's arc passes close; only a clear one counts here
    if marks["swing arc"] and marks["lines"] and gap.exterior and f.arc < o.arc_over_lines:
        marks["swing arc"] = False  # a window's lines also trace part of a circle; its lines win
    if sum(marks.values()) > 1:
        fields["marks"] = " and ".join(k for k, v in marks.items() if v)
        first = "hinged_door" if marks["swing arc"] else "window"
        second = "window" if first == "hinged_door" else "cavity_slider"
        return first, second, UNSURE, f, fields
    if marks["swing arc"]:
        ends = o.ends[gap.axis]
        sides = o.sides[gap.axis]
        fields["hinge"] = ends[f.arc_end]
        fields["side"] = sides[0 if f.arc_side < 0 else 1]
        return "hinged_door", None, CLEAR, f, fields
    if marks["lines"]:
        fields["lines"] = str(f.lines)
        return "window", None, CLEAR, f, fields
    if gap.exterior and f.lines == 1:
        return "sliding_door", "window", CLEAR, f, fields  # decided with its code, see `attach_codes`
    if cavity:
        fields["word"] = cavity.lower()
        return "cavity_slider", None, CLEAR, f, fields
    if f.lines == 1 and not gap.exterior and o.door_min_mm <= gap.width <= o.door_max_mm:
        # one line across an interior break of door width: a door leaf drawn edge-on, so a cavity slider (or a
        # door), never "nothing drawn"
        return "cavity_slider", "hinged_door", UNSURE, f, fields
    if gap.exterior:
        return "open_walkway", "window", UNSURE, f, fields
    if f.dashed:  # dashed parallel lines: a cavity slider or an arch (built the same way as a framed opening)
        return "arch", "cavity_slider", CLEAR, f, fields
    return "open_walkway", None, (UNSURE if gap.meets_wall or gap.across else CLEAR), f, fields


def _cavity_words(gaps: list[Gap], words: list[Word], source: Source, rules: TraceRules) -> dict[int, str]:
    """Each cavity word ("csd") belongs to a break within reach of it: the nearest one drawn with dashed
    lines (a cavity slider is drawn dashed) within `cavity_dash_search_mm`, else the nearest within
    `cavity_search_mm`."""
    o = rules.openings
    wanted = {w.upper() for w in o.cavity_words}
    out: dict[int, str] = {}
    for w in words:
        if w.text.upper() not in wanted or not gaps:
            continue
        dist = {i: distance_to_rect(w.centre, g.frame) for i, g in enumerate(gaps)}
        dashed = [i for i in dist if dist[i] <= o.cavity_dash_search_mm and _rows(source, gaps[i], rules)[1]]
        if dashed:
            out[min(dashed, key=lambda i: dist[i])] = w.text
            continue
        n = min(dist, key=lambda i: dist[i])
        if dist[n] <= o.cavity_search_mm:
            out[n] = w.text
    return out


def _kind_key(kind: str, gap: Gap, f: Features) -> str:
    if kind == "cavity_slider" and not f.words:
        return "cavity_line"
    if kind == "open_walkway" and gap.exterior:
        return "exterior_nothing"
    if kind == "open_walkway" and gap.across:
        return "open_walkway_ends"
    if kind == "open_walkway" and gap.meets_wall:
        return "open_walkway_t"
    return kind


def code_words(words: list[Word], rules: TraceRules) -> list[tuple[Word, str]]:
    """Window codes on the page: height-width words (15-18), with a suffix word on the same line (awn)."""
    o = rules.openings
    pattern = re.compile(o.code_pattern)
    suffixes = {s.upper() for s in o.code_suffixes}
    out = []
    for w in words:
        if not pattern.match(w.text):
            continue
        text = w.text
        for other in words:
            height = w.y1 - w.y0
            if (
                other.text.upper() in suffixes
                and abs(other.centre[1] - w.centre[1]) < height * 0.6
                and 0 <= other.x0 - w.x1 <= height * 2
            ):
                text = f"{w.text} {other.text.lower()}"
        out.append((w, text))
    return out


def assign_codes(gaps: list[Gap], words: list[Word], rules: TraceRules) -> dict[int, list[str]]:
    """Each window code goes to the nearest exterior break within reach (a garage-width break takes none).
    A break with two codes is ambiguous; one with none is read from its lines."""
    o = rules.openings
    reach = [n for n, g in enumerate(gaps) if g.width < o.garage_min_mm]
    found: dict[int, list[str]] = {}
    for w, text in code_words(words, rules):
        best = min(reach, key=lambda n: distance_to_rect(w.centre, gaps[n].frame), default=None)
        if best is not None and distance_to_rect(w.centre, gaps[best].frame) <= o.code_search_mm:
            found.setdefault(best, []).append(text)
    return found


def read(
    vp: vector_walls.VectorPage,
    source: Source,
    words: list[Word],
    rules: TraceRules,
    outdoor: list[Rect] | None = None,
) -> list[Opening]:
    """Every opening of the page, in reading order (top to bottom, left to right) with stable ids."""
    gaps = candidate_gaps(vp, rules, outdoor)
    mark_exterior(gaps, vp, rules)
    gaps.sort(key=lambda g: (round(g.centre[1] / 100), g.centre[0]))
    # a garage-width line between two wall ends is a garage front: it is on the outside
    gaps = [g for g in gaps if g.exterior or not g.across or g.width < rules.openings.garage_min_mm]
    cavity = _cavity_words(gaps, words, source, rules)
    coded = assign_codes(gaps, words, rules)
    found: list[Opening] = []
    for n, g in enumerate(gaps):
        kind, alternative, confidence, features, fields = read_gap(
            g, source, words, rules, cavity.get(n), coded.get(n)
        )
        if kind == "open_walkway" and not g.exterior and g.width > rules.openings.walkway_max_mm:
            continue  # a wide gap with nothing across it is open plan, not an opening
        if kind == "open_walkway" and g.width < rules.openings.walkway_min_mm:
            continue  # too narrow to walk through: a gap between a wall and a fitting
        key = _kind_key(kind, g, features) if kind != "hinged_door" or not alternative else "conflict"
        if alternative and kind in ("hinged_door", "window") and "marks" in fields:
            key = "conflict"
        template = rules.openings.evidence.get(key) or rules.openings.evidence["conflict"]
        fields.setdefault("marks", "")
        fields.setdefault("words", "")
        found.append(
            Opening(
                f"O{round(g.centre[0] / 10)}_{round(g.centre[1] / 10)}",
                kind,
                alternative,
                confidence,
                template.format_map(_Safe(fields)),
                g,
                features,
            )
        )
        codes = coded.get(n, [])
        op = found[-1]
        if kind in ("window", "sliding_door"):
            if len(codes) == 1:
                op.code = codes[0]
            else:  # none, or two on one break
                op.code_note = "none" if not codes else "several: " + ", ".join(codes)
                op.confidence = UNSURE
        if kind == "sliding_door":  # its evidence quotes the code
            op.evidence = rules.openings.evidence["sliding_door"].format_map(
                _Safe({"gap_mm": str(round(g.width)), "code": op.code or "NOT FOUND"})
            )
    return found


class _Safe(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""
