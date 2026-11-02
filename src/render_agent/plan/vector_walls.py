"""Walls from a vector floor plan page (PLAN_VISUALS_SPEC section 3; local, no model, no cost).

Revit exports draw the cut walls solid black. Paths list segments in any order, use holes and are
partly covered by white masks drawn later, so they are not rebuilt by hand: the page is drawn as
printed (without its text) at `raster_mm` per pixel, near-black pixels are kept, anything thinner than
a wall is removed, and the result is split into axis-aligned wall rectangles in mm (page coordinates,
y down). Marks far from the plan (title block, section arrows) are dropped by grouping.

Dimension dots (small filled squares at the ends of dimension lines) are removed from the drawing first,
because they sit on wall ends and hide where a wall stops. Scans use the same mask pipeline on the page
image (`from_image`); a model's traced outlines are rasterised into the same mask (`from_polygons`), never
reduced to boxes. Each connected group of walls also gets its outline as an orthogonal polygon, every jog
and return kept (`outlines`).

`closures` finds the openings: two free wall ends that face each other on the same line within a
maximum gap. Closing them gives the footprint for the area check.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter

from render_agent.plan.trace_rules import Walls

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 in mm
Point = tuple[float, float]
POST_LINE_MM = 100.0  # thickness of the line a post closes an outdoor space with
Outline = tuple[list[Point], bool]  # corners in mm, and whether it is a hole (a room inside walls)
SCALE_RE = re.compile(r"\b1\s*:\s*(\d{2,4})\b")
WALL_RUN = re.compile(rb"[\x00-\x7f]+")  # dark pixels of a grey row


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
    drawing: bytes = b""  # the drawing's own dark pixels (before the opening filter): 0 = dark
    band: bytes = b""  # every wall-thick dark pixel on the page, before marks far from the plan are dropped
    end_max_mm: float = 450.0
    outlines: list[Outline] = field(default_factory=list)

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
    keep = {g for g in plans if span(g) >= rules.keep_share * biggest}
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


def _remove_dots(page: pymupdf.Page, factor: float, rules: Walls) -> None:
    """Delete dimension dots: small, nearly square, black filled shapes. They sit on wall ends and would
    hide where a wall stops. Walls, piers and thin marks are bigger or thinner and stay."""
    limit = rules.dot_max_mm / factor
    for d in page.get_drawings():
        fill, r = d.get("fill"), d["rect"]
        if fill is None or sum(fill) > 0.3 or not (0 < r.width <= limit and 0 < r.height <= limit):
            continue
        if min(r.width, r.height) / max(r.width, r.height) >= rules.dot_aspect_min:
            page.add_redact_annot(r + (-0.2, -0.2, 0.2, 0.2))
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,  # type: ignore[attr-defined]
        graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,  # type: ignore[attr-defined]
        text=pymupdf.PDF_REDACT_TEXT_NONE,  # type: ignore[attr-defined]
    )


def _page_without_text(pdf: Path, page_number: int, factor: float, rules: Walls) -> pymupdf.Document:
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
    _remove_dots(page, factor, rules)
    return doc


Cell = tuple[int, int]


def _boundary(edges: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Boundary pieces (line, from, to, sign) of edges (line, from, to, sign) once opposite edges of
    touching rectangles cancel: the sweep keeps the stretches whose net count is not zero."""
    by_line: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for line, a, b, sign in edges:
        by_line[line].append((a, sign))
        by_line[line].append((b, -sign))
    out = []
    for line, events in by_line.items():
        net, last = 0, 0
        for pos, delta in sorted(events):
            if net != 0 and pos > last:
                out.append((line, last, pos, 1 if net > 0 else -1))
            net += delta
            last = pos
    return out


def _merge_collinear(pts: list[Cell]) -> list[Cell]:
    out: list[Cell] = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        if a == b or (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
            continue
        out.append(b)
    return out


def _simplify(pts: list[Cell], jog: int) -> list[Cell]:
    """Merge collinear corners and drop notches and bumps shorter than `jog` (raster noise)."""
    pts = _merge_collinear(pts)
    changed = True
    while changed and len(pts) > 4:
        changed = False
        n = len(pts)
        for i in range(n):
            a, b, c, d = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
            short = abs(c[0] - b[0]) + abs(c[1] - b[1])
            ab, cd = (b[0] - a[0], b[1] - a[1]), (d[0] - c[0], d[1] - c[1])
            opposite = ab[0] * cd[0] + ab[1] * cd[1] < 0
            narrow = abs(ab[0]) + abs(ab[1]) <= jog and abs(cd[0]) + abs(cd[1]) <= jog
            if opposite and (0 < short <= jog or narrow):  # a notch or bump, short or thin
                pts = _merge_collinear([p for k, p in enumerate(pts) if k not in (i, (i + 1) % n)])
                changed = True
                break
            if 0 < short <= jog and ab[0] * cd[0] + ab[1] * cd[1] > 0:  # a small step in a wall line
                kept = [p for k, p in enumerate(pts) if k not in (i, (i + 1) % n)]
                at = i if i < (i + 1) % n else 0
                options = [
                    _merge_collinear(kept[:at] + [corner] + kept[at:])
                    for corner in ((d[0], a[1]), (a[0], d[1]))
                ]
                pts = min(options, key=len)
                changed = True
                break
    return pts


def outlines(rects: list[Rect], px: float, min_jog_mm: float) -> list[Outline]:
    """The outline of every connected group of wall rectangles as an orthogonal polygon (mm). Corners are
    on the pixel grid, every jog and return is kept; rooms enclosed by walls come back as holes."""
    tops, bottoms, lefts, rights = [], [], [], []
    for x0, y0, x1, y1 in rects:
        i0, j0, i1, j1 = round(x0 / px), round(y0 / px), round(x1 / px), round(y1 / px)
        tops.append((j0, i0, i1, 1))
        bottoms.append((j1, i0, i1, -1))
        lefts.append((i0, j0, j1, -1))
        rights.append((i1, j0, j1, 1))
    out_of: dict[Cell, list[Cell]] = defaultdict(list)
    for line, a, b, sign in _boundary(tops + bottoms):
        start, end = ((a, line), (b, line)) if sign > 0 else ((b, line), (a, line))
        out_of[start].append(end)
    for line, a, b, sign in _boundary(rights + lefts):
        start, end = ((line, a), (line, b)) if sign > 0 else ((line, b), (line, a))
        out_of[start].append(end)
    result: list[Outline] = []
    jog = round(min_jog_mm / px)
    while out_of:
        start = next(iter(out_of))
        loop, cur, heading = [start], start, (0, 0)
        while True:
            nexts = out_of[cur]
            if len(nexts) > 1:  # two loops touch at this corner: turn right to keep them apart
                nxt = max(nexts, key=lambda t: heading[0] * (t[1] - cur[1]) - heading[1] * (t[0] - cur[0]))
            else:
                nxt = nexts[0]
            nexts.remove(nxt)
            if not nexts:
                del out_of[cur]
            heading, cur = (nxt[0] - cur[0], nxt[1] - cur[1]), nxt
            if cur == start or cur not in out_of:
                break
            loop.append(cur)
        pts = _simplify(loop, jog)
        if len(pts) < 4:
            continue
        area2 = sum(
            pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
            for i in range(len(pts))
        )
        result.append(([(x * px, y * px) for x, y in pts], area2 < 0))
    return result


def _read_mask(
    dark: Image.Image,
    px: float,
    factor: float,
    size: tuple[float, float],
    rules: Walls,
    plan_only: bool = True,
) -> VectorPage:
    """The shared mask pipeline: `dark` is the drawing's own dark pixels (0 = dark) at `px` mm per
    pixel. Anything thinner than a wall is removed with an opening filter, the rest is split into
    rectangles and outlines, and marks far from the plan are dropped."""
    out = VectorPage(size[0], size[1], factor, [], end_max_mm=rules.end_max_mm)
    filt = max(3, int(rules.min_wall_mm / px) | 1)  # odd filter size
    walls_img = dark.filter(ImageFilter.MaxFilter(filt)).filter(ImageFilter.MinFilter(filt))
    w, h = walls_img.size
    samples = walls_img.tobytes()
    out.band = samples
    rects = [(x0 * px, y0 * px, x1 * px, y1 * px) for x0, y0, x1, y1 in _rectangles(samples, w, h)]
    keep = set(_plan_only(rects, px, rules)) if plan_only else set(range(len(rects)))
    if len(keep) < len(rects):  # blank out what is not part of the plan
        draw = ImageDraw.Draw(walls_img)
        for i, r in enumerate(rects):
            if i not in keep:
                draw.rectangle([r[0] / px, r[1] / px, r[2] / px - 1, r[3] / px - 1], fill=255)
        samples = walls_img.tobytes()
    out.walls = [r for i, r in enumerate(rects) if i in keep]
    out.px_mm, out.origin, out.size, out.raster = px, (0.0, 0.0), (w, h), samples
    out.drawing = dark.tobytes()
    out.outlines = outlines(out.walls, px, rules.outline_min_jog_mm)
    return out


def extract(pdf: Path, page_number: int, scale_ratio: int, rules: Walls) -> VectorPage:
    """The wall rectangles (mm) of page `page_number` (1-based) of `pdf` drawn at 1:`scale_ratio`.

    The page is drawn exactly as printed (so masks and drawing order are right), without its text and
    dimension dots; near-black pixels are kept and anything thinner than `min_wall_mm` (door swings,
    symbols, dimension lines) is removed with an opening filter."""
    factor = mm_per_pt(scale_ratio)
    px = rules.raster_mm
    with _page_without_text(pdf, page_number, factor, rules) as doc:
        page = doc[0]
        zoom = factor / px
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
        size = (page.rect.width * factor, page.rect.height * factor)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples, "raw", "L", pix.stride)
    dark = img.point([0 if v < rules.dark_below else 255 for v in range(256)])
    return _read_mask(dark, px, factor, size, rules)


def from_image(img: Image.Image, mm_per_px: float, scale_ratio: int, rules: Walls) -> VectorPage:
    """The same wall reading on a scan or image page: `mm_per_px` is the pixel size at the drawing scale."""
    gray = img.convert("L")
    dark = gray.point([0 if v < rules.dark_below else 255 for v in range(256)])
    size = (gray.width * mm_per_px, gray.height * mm_per_px)
    return _read_mask(dark, mm_per_px, mm_per_pt(scale_ratio), size, rules)


def from_polygons(
    polygons: list[list[Point]], size_mm: tuple[float, float], scale_ratio: int, rules: Walls
) -> VectorPage:
    """Walls a model traced as outlines (mm), rasterised as polygons into the wall mask; an outline is
    never reduced to its bounding box."""
    px = rules.raster_mm
    img = Image.new("L", (max(1, int(size_mm[0] / px)), max(1, int(size_mm[1] / px))), 255)
    draw = ImageDraw.Draw(img)
    for poly in polygons:
        if len(poly) >= 3:
            draw.polygon([(x / px, y / px) for x, y in poly], fill=0)
    return _read_mask(img, px, mm_per_pt(scale_ratio), size_mm, rules, plan_only=False)


def _band(vp: VectorPage, along: float, mid: float, axis: str) -> tuple[float, float]:
    """The wall's full thickness through (along, mid): scans across the wall from inside it."""
    px = vp.px_mm

    def wall(c: float) -> bool:
        return vp.wall_at(along, c) if axis == "h" else vp.wall_at(c, along)

    lo = hi = mid
    while wall(lo - px) and mid - lo < vp.end_max_mm:
        lo -= px
    while wall(hi + px) and hi - mid < vp.end_max_mm:
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
        if h <= vp.end_max_mm:
            for x, sign in ((x0, -1), (x1, 1)):
                if any(vp.wall_at(x + sign * step, y) for y in (y0 + h * 0.25, y0 + h * 0.5, y0 + h * 0.75)):
                    continue
                lo, hi = _band(vp, x - sign * vp.px_mm / 2, (y0 + y1) / 2, "h")
                if min_mm <= hi - lo <= vp.end_max_mm:
                    found[("h", round(x / 10), round(lo / 10), round(hi / 10), sign)] = ("h", x, lo, hi, sign)
        if w <= vp.end_max_mm:
            for y, sign in ((y0, -1), (y1, 1)):
                if any(vp.wall_at(x, y + sign * step) for x in (x0 + w * 0.25, x0 + w * 0.5, x0 + w * 0.75)):
                    continue
                lo, hi = _band(vp, y - sign * vp.px_mm / 2, (x0 + x1) / 2, "v")
                if min_mm <= hi - lo <= vp.end_max_mm:
                    found[("v", round(y / 10), round(lo / 10), round(hi / 10), sign)] = ("v", y, lo, hi, sign)
    return list(found.values())


def piers(vp: VectorPage, rules: Walls) -> list[Rect]:
    """Posts: square wall groups bigger than a dimension dot and no longer than a wall end (a post marks
    the corner of an outdoor zone such as an alfresco or porch). Short wall stubs are not posts."""
    roots = _roots(vp.walls, vp.px_mm * 1.5)
    box: dict[int, list[float]] = {}
    for r, root in zip(vp.walls, roots, strict=True):
        b = box.setdefault(root, [r[0], r[1], r[2], r[3]])
        b[0], b[1], b[2], b[3] = min(b[0], r[0]), min(b[1], r[1]), max(b[2], r[2]), max(b[3], r[3])
    return [
        (b[0], b[1], b[2], b[3])
        for b in box.values()
        if b[2] - b[0] <= vp.end_max_mm
        and b[3] - b[1] <= vp.end_max_mm
        and min(b[2] - b[0], b[3] - b[1]) > rules.dot_max_mm
        and min(b[2] - b[0], b[3] - b[1]) >= rules.dot_aspect_min * max(b[2] - b[0], b[3] - b[1])
    ]


def pier_closures(vp: VectorPage, max_gap_mm: float, rules: Walls) -> list[Rect]:
    """A post closes the gap to the wall it faces (the open side of an alfresco or porch): the nearest
    wall within `max_gap_mm` straight out from each face."""
    out: list[Rect] = []
    px = vp.px_mm
    for x0, y0, x1, y1 in piers(vp, rules):
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            start = (x0 if dx < 0 else x1) if dx else (y0 if dy < 0 else y1)
            step = px
            while step <= max_gap_mm:
                d = step * (dx or dy)
                x, y = (start + d, my) if dx else (mx, start + d)
                if vp.wall_at(x, y):
                    break
                step += px
            else:
                continue
            if step <= px * 3:  # touching another wall: not a gap
                continue
            far = start + (step - px) * (dx or dy)
            lo, hi = sorted((start, far))
            half = POST_LINE_MM / 2  # a thin line along the post's centre: the edge of the outdoor space
            out.append((lo, my - half, hi, my + half) if dx else (mx - half, lo, mx + half, hi))
    return out


def closures(vp: VectorPage, max_gap_mm: float, min_wall_mm: float, rules: Walls | None = None) -> list[Rect]:
    """Rectangles (mm) closing each opening between two facing free wall ends, nearest first. Posts
    also close the gap to the wall they face when `rules` is given (the footprint needs it, openings
    do not)."""
    found = _end_closures(vp, max_gap_mm, min_wall_mm)
    return found + pier_closures(vp, max_gap_mm, rules) if rules is not None else found


def across_ends(
    vp: VectorPage,
    min_gap_mm: float,
    max_gap_mm: float,
    min_wall_mm: float,
    max_thickness_mm: float,
    align_mm: float = 150.0,
) -> list[Rect]:
    """Breaks between two parallel walls that end side by side facing the same way (a garage front between
    its two side walls): the line joining the two ends, wide enough to be a garage door."""
    ends = _ends(vp, min_wall_mm)
    out: list[Rect] = []
    for i, a in enumerate(ends):
        for b in ends[i + 1 :]:
            if a[0] != b[0] or a[4] != b[4] or abs(a[1] - b[1]) > align_mm:
                continue
            if max(a[3] - a[2], b[3] - b[2]) > max_thickness_mm:
                continue  # hatched boxes and piers are thicker than a wall
            first, second = (a, b) if (a[2] + a[3]) < (b[2] + b[3]) else (b, a)
            gap = second[2] - first[3]
            if not min_gap_mm <= gap <= max_gap_mm:
                continue
            pos, t = (a[1] + b[1]) / 2, max(first[3] - first[2], second[3] - second[2])
            near, far = (pos - t, pos) if a[4] > 0 else (pos, pos + t)
            out.append((first[3], near, second[2], far) if a[0] == "v" else (near, first[3], far, second[2]))
    return out


def end_to_wall(vp: VectorPage, max_gap_mm: float, min_wall_mm: float) -> list[Rect]:
    """Breaks where one side is a free wall end and the other a wall running across it (a doorway cut
    into a T-junction): the nearest wall face-on within `max_gap_mm` straight out from each free end."""
    out: list[Rect] = []
    px = vp.px_mm
    for axis, pos, lo, hi, sign in _ends(vp, min_wall_mm):
        mid, span = (lo + hi) / 2, hi - lo
        probes = (lo + span * 0.25, mid, hi - span * 0.25)
        step = px * 2
        while step <= max_gap_mm:
            d = pos + sign * step
            if all(vp.wall_at(d, c) if axis == "h" else vp.wall_at(c, d) for c in probes):
                break
            step += px
        else:
            continue
        a, b = sorted((pos, pos + sign * (step - px)))
        out.append((a, lo, b, hi) if axis == "h" else (lo, a, hi, b))
    return out


def _end_closures(vp: VectorPage, max_gap_mm: float, min_wall_mm: float) -> list[Rect]:
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
