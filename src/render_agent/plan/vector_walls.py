"""Walls from a vector floor plan page (PLAN_VISUALS_SPEC section 3; local, no model, no cost).

Revit exports draw the cut walls solid black. Paths list segments in any order, use holes and are
partly covered by white masks drawn later, so they are not rebuilt by hand: the page is drawn as
printed (without its text) at `raster_mm` per pixel, near-black pixels are kept, anything thinner than
a wall is removed, and the result is split into axis-aligned wall rectangles in mm (page coordinates,
y down). Marks far from the plan (title block, section arrows) are dropped by grouping.

`closures` finds the openings: two free wall ends that face each other on the same line within a
maximum gap. Closing them gives the footprint for the area check.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter

from render_agent.plan.trace_rules import Walls

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 in mm
Point = tuple[float, float]
SCALE_RE = re.compile(r"\b1\s*:\s*(\d{2,4})\b")
WALL_RUN = re.compile(rb"[\x00-\x7f]+")  # dark pixels of a grey row
END_MAX_MM = 450.0  # a wall end (or a pier face) is at most this long
KEEP_SHARE = 0.4  # wall groups spanning less than this share of the largest are not part of the plan


@dataclass
class VectorPage:
    width_mm: float
    height_mm: float
    mm_per_pt: float
    walls: list[Rect]
    px_mm: float = 0.0
    origin: Point = (0.0, 0.0)
    size: tuple[int, int] = (0, 0)
    raster: bytes = b""

    def wall_at(self, x: float, y: float) -> bool:
        w, h = self.size
        px, py = int((x - self.origin[0]) / self.px_mm), int((y - self.origin[1]) / self.px_mm)
        return 0 <= px < w and 0 <= py < h and self.raster[py * w + px] < 128


def mm_per_pt(scale_ratio: int) -> float:
    return 25.4 / 72 * scale_ratio


def scale_from_text(*texts: str) -> int | None:
    """The first '1:N' in the texts (the stage 2 scale first, then the page text)."""
    for text in texts:
        if m := SCALE_RE.search(text or ""):
            return int(m.group(1))
    return None


def polygon(r: Rect) -> list[Point]:
    return [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]


def _roots(rects: list[Rect], gap: float) -> list[int]:
    """A group id per rectangle: rectangles within `gap` of each other share one."""
    parent = list(range(len(rects)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(rects)), key=lambda i: rects[i][0])
    for n, i in enumerate(order):  # sweep along x: only rectangles that can be within `gap`
        a = rects[i]
        for j in order[n + 1 :]:
            b = rects[j]
            if b[0] - gap > a[2]:
                break
            if a[1] - gap <= b[3] and b[1] - gap <= a[3]:
                parent[find(i)] = find(j)
    return [find(i) for i in range(len(rects))]


def _plan_only(rects: list[Rect], px: float, rules: Walls) -> list[int]:
    """Indexes of the rectangles that are walls of the plan. Small shapes on their own are dimension
    dots; groups of walls much smaller than the largest group are title-block marks or arrows."""
    roots = _roots(rects, px * 1.5)
    box: dict[int, list[float]] = {}
    for r, root in zip(rects, roots, strict=True):
        b = box.setdefault(root, [r[0], r[1], r[2], r[3]])
        b[0], b[1], b[2], b[3] = min(b[0], r[0]), min(b[1], r[1]), max(b[2], r[2]), max(b[3], r[3])
    shapes = [
        i
        for i, root in enumerate(roots)
        if max(box[root][2] - box[root][0], box[root][3] - box[root][1]) > rules.dot_max_mm
    ]
    groups = _roots([rects[i] for i in shapes], rules.cluster_gap_mm)
    gbox: dict[int, list[float]] = {}
    filled: dict[int, float] = defaultdict(float)
    for i, g in zip(shapes, groups, strict=True):
        r = rects[i]
        b = gbox.setdefault(g, [r[0], r[1], r[2], r[3]])
        b[0], b[1], b[2], b[3] = min(b[0], r[0]), min(b[1], r[1]), max(b[2], r[2]), max(b[3], r[3])
        filled[g] += (r[2] - r[0]) * (r[3] - r[1])

    def span(g: int) -> float:
        return (gbox[g][2] - gbox[g][0]) + (gbox[g][3] - gbox[g][1])

    def solid(g: int) -> bool:  # logos and arrows are mostly ink; a plan is mostly rooms
        area = (gbox[g][2] - gbox[g][0]) * (gbox[g][3] - gbox[g][1])
        return area > 0 and filled[g] / area > rules.max_fill_share

    plans = [g for g in gbox if not solid(g)]
    if not plans:
        return []
    biggest = max(span(g) for g in plans)
    keep = {g for g in plans if span(g) >= KEEP_SHARE * biggest}
    return [i for i, g in zip(shapes, groups, strict=True) if g in keep]


def _rectangles(samples: bytes, w: int, h: int) -> list[tuple[int, int, int, int]]:
    """Dark pixel runs merged down the rows into rectangles (x0, y0, x1, y1), end-exclusive."""
    out = []
    active: dict[tuple[int, int], int] = {}
    for y in range(h + 1):
        runs = set()
        if y < h:
            runs = {(m.start(), m.end()) for m in WALL_RUN.finditer(samples[y * w : (y + 1) * w])}
        for key in [k for k in active if k not in runs]:
            out.append((key[0], active.pop(key), key[1], y))
        for key in runs:
            active.setdefault(key, y)
    return out


def _page_without_text(pdf: Path, page_number: int) -> pymupdf.Document:
    doc = pymupdf.open()
    with pymupdf.open(pdf) as src:
        doc.insert_pdf(src, from_page=page_number - 1, to_page=page_number - 1)
    page = doc[0]
    page.add_redact_annot(page.rect)
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,  # type: ignore[attr-defined]
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,  # type: ignore[attr-defined]
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,  # type: ignore[attr-defined]
    )
    return doc


def extract(pdf: Path, page_number: int, scale_ratio: int, rules: Walls) -> VectorPage:
    """The wall rectangles (mm) of page `page_number` (1-based) of `pdf` drawn at 1:`scale_ratio`.

    The page is drawn exactly as printed (so masks and drawing order are right), without its text;
    near-black pixels are kept and anything thinner than `min_wall_mm` (door swings, symbols,
    dimension lines) is removed with an opening filter."""
    factor = mm_per_pt(scale_ratio)
    px = rules.raster_mm
    with _page_without_text(pdf, page_number) as doc:
        page = doc[0]
        zoom = factor / px
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
        out = VectorPage(page.rect.width * factor, page.rect.height * factor, factor, [])
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples, "raw", "L", pix.stride)
    dark = img.point([0 if v < rules.dark_below else 255 for v in range(256)])
    size = max(3, int(rules.min_wall_mm / px) | 1)  # odd filter size
    walls_img = dark.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))
    w, h = walls_img.size
    samples = walls_img.tobytes()
    rects = [(x0 * px, y0 * px, x1 * px, y1 * px) for x0, y0, x1, y1 in _rectangles(samples, w, h)]
    keep = set(_plan_only(rects, px, rules))
    if len(keep) < len(rects):  # blank out what is not part of the plan
        draw = ImageDraw.Draw(walls_img)
        for i, r in enumerate(rects):
            if i not in keep:
                draw.rectangle([r[0] / px, r[1] / px, r[2] / px - 1, r[3] / px - 1], fill=255)
        samples = walls_img.tobytes()
    out.walls = [r for i, r in enumerate(rects) if i in keep]
    out.px_mm, out.origin, out.size, out.raster = px, (0.0, 0.0), (w, h), samples
    return out


def _band(vp: VectorPage, along: float, mid: float, axis: str) -> tuple[float, float]:
    """The wall's full thickness through (along, mid): scans across the wall from inside it."""
    px = vp.px_mm

    def wall(c: float) -> bool:
        return vp.wall_at(along, c) if axis == "h" else vp.wall_at(c, along)

    lo = hi = mid
    while wall(lo - px) and mid - lo < END_MAX_MM:
        lo -= px
    while wall(hi + px) and hi - mid < END_MAX_MM:
        hi += px
    return lo, hi + px


def _ends(vp: VectorPage, min_mm: float) -> list[tuple[str, float, float, float, int]]:
    """Free wall ends: (axis, position, span lo, span hi, outward sign). Axis 'h' is the end of a
    horizontal wall (a vertical edge). The span is the wall's full thickness, read from the raster, so
    the thin strips a wall splits into give one end; an end touching more wall is a junction."""
    found: dict[tuple[str, int, int, int, int], tuple[str, float, float, float, int]] = {}
    step = vp.px_mm * 1.5
    for x0, y0, x1, y1 in vp.walls:
        w, h = x1 - x0, y1 - y0
        if h <= END_MAX_MM:
            for x, sign in ((x0, -1), (x1, 1)):
                if any(vp.wall_at(x + sign * step, y) for y in (y0 + h * 0.25, y0 + h * 0.5, y0 + h * 0.75)):
                    continue
                lo, hi = _band(vp, x - sign * vp.px_mm / 2, (y0 + y1) / 2, "h")
                if min_mm <= hi - lo <= END_MAX_MM:
                    found[("h", round(x / 10), round(lo / 10), round(hi / 10), sign)] = ("h", x, lo, hi, sign)
        if w <= END_MAX_MM:
            for y, sign in ((y0, -1), (y1, 1)):
                if any(vp.wall_at(x, y + sign * step) for x in (x0 + w * 0.25, x0 + w * 0.5, x0 + w * 0.75)):
                    continue
                lo, hi = _band(vp, y - sign * vp.px_mm / 2, (x0 + x1) / 2, "v")
                if min_mm <= hi - lo <= END_MAX_MM:
                    found[("v", round(y / 10), round(lo / 10), round(hi / 10), sign)] = ("v", y, lo, hi, sign)
    return list(found.values())


def closures(vp: VectorPage, max_gap_mm: float, min_wall_mm: float) -> list[Rect]:
    """Rectangles (mm) closing each opening between two facing free wall ends, nearest first."""
    ends = _ends(vp, min_wall_mm)
    bins: dict[tuple[str, int], list[int]] = defaultdict(list)
    for i, e in enumerate(ends):
        bins[(e[0], round((e[2] + e[3]) / 2 / 50))].append(i)
    pairs = []
    for i, c in enumerate(ends):
        mid = round((c[2] + c[3]) / 2 / 50)
        for j in (j for k in (mid - 1, mid, mid + 1) for j in bins.get((c[0], k), []) if j > i):
            d = ends[j]
            a, b = (c, d) if c[1] < d[1] else (d, c)
            if not (a[4] == 1 and b[4] == -1):
                continue
            gap = b[1] - a[1]
            lo, hi = max(a[2], b[2]), min(a[3], b[3])
            shorter = min(a[3] - a[2], b[3] - b[2])
            if 0 < gap <= max_gap_mm and hi - lo >= 0.6 * shorter:  # the two ends line up
                pairs.append((gap, i, j, a, lo, hi, b))
    pairs.sort(key=lambda x: x[0])
    used: set[int] = set()
    out: list[Rect] = []
    for _gap, i, j, a, lo, hi, b in pairs:
        if i in used or j in used:
            continue
        used |= {i, j}
        out.append((a[1], lo, b[1], hi) if a[0] == "h" else (lo, a[1], hi, b[1]))
    return out
