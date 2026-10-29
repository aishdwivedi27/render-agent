"""Area check (PLAN_VISUALS_SPEC section 3): the drawing's area schedule against the traced plan.

The schedule is read from the page's text layer (labels and m² values on one line, grouped under a
"DWELLING n" heading when there are several). Traced areas are measured on a raster in mm the way a
schedule is: each wall pixel belongs to the nearest room, so exterior walls count to their outer face
and shared walls to their centre, plus the veneer on exterior faces. Living is the whole footprint
less the garage, alfresco and porch.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from render_agent.plan.trace_rules import ZONES, TraceRules

Point = tuple[float, float]
VALUE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*m", re.IGNORECASE)
OUTSIDE, WALL, FREE = 1, 0, 255
UNLABELLED = 250  # label of interior space no room covers
MARGIN = 25  # pixels of open ground around the plan, so the outside can flow all the way round
LINE_TOL_PT = 4.0


@dataclass
class Measured:
    total_m2: float
    zones: dict[str, float] = field(default_factory=dict)  # garage / alfresco / porch / living


def _lines(words: list[tuple[Any, ...]]) -> list[list[tuple[Any, ...]]]:
    rows: list[list[tuple[Any, ...]]] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        cy = (w[1] + w[3]) / 2
        if rows and abs((rows[-1][0][1] + rows[-1][0][3]) / 2 - cy) <= LINE_TOL_PT:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w[0]) for r in rows]


def parse_schedule(words: list[tuple[Any, ...]], rules: TraceRules) -> dict[str, dict[str, float]]:
    """{dwelling key: {zone: m²}}; the key is 'DWELLING n' under such a heading, else ''.

    `words` are PyMuPDF words (x0, y0, x1, y1, text, ...) in page points."""
    labels = {
        " ".join(lbl.upper().split()): zone
        for zone, names in rules.areas.schedule_labels.items()
        for lbl in names
    }
    out: dict[str, dict[str, float]] = {}
    heading = ""
    heading_y = -1e9
    for row in _lines(words):
        text = [str(w[4]) for w in row]
        upper = " ".join(t.upper() for t in text)
        if m := re.match(r"^DWELLING\s+(\w+)$", upper.strip()):
            heading, heading_y = f"DWELLING {m.group(1)}", row[0][1]
            continue
        values = [VALUE_RE.match(t) for t in text]
        idx = next((i for i, v in enumerate(values) if v), None)
        if idx is None or idx == 0:
            continue
        label = " ".join(t.upper() for t in text[:idx]).rstrip(":")
        zone = labels.get(label)
        if zone is None:
            continue
        key = heading if heading and row[0][1] - heading_y < 400 else ""
        out.setdefault(key, {})[zone] = float(values[idx].group(1))  # type: ignore[union-attr]
    return out


class Grid:
    """A raster of the plan in mm (x right, y down), `px_mm` per pixel."""

    def __init__(self, bounds: tuple[float, float, float, float], px_mm: float) -> None:
        self.x0, self.y0 = bounds[0] - MARGIN * px_mm, bounds[1] - MARGIN * px_mm
        self.px = px_mm
        self.w = int((bounds[2] - self.x0) / px_mm) + MARGIN + 1
        self.h = int((bounds[3] - self.y0) / px_mm) + MARGIN + 1

    def pt(self, p: Point) -> tuple[float, float]:
        return ((p[0] - self.x0) / self.px, (p[1] - self.y0) / self.px)

    def base(
        self, walls: list[list[Point]], closures: list[tuple[float, float, float, float]]
    ) -> Image.Image:
        img = Image.new("L", (self.w, self.h), FREE)
        draw = ImageDraw.Draw(img)
        for poly in walls:
            draw.polygon([self.pt(p) for p in poly], fill=WALL)
        for x0, y0, x1, y1 in closures:
            draw.rectangle([self.pt((x0, y0)), self.pt((x1, y1))], fill=WALL)
        ImageDraw.floodfill(img, (0, 0), OUTSIDE)
        return img


def footprint(
    pts: list[Point],
    walls_mm: list[list[Point]],
    closures_mm: list[tuple[float, float, float, float]],
    px_mm: float,
) -> tuple[Grid, Image.Image]:
    """The grid around `pts` and its image: walls and closed openings WALL, outside OUTSIDE, rest FREE."""
    bounds = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
    grid = Grid(bounds, px_mm)
    return grid, grid.base(walls_mm, closures_mm)


def _polygon_m2(poly: list[Point]) -> float:
    n = len(poly)
    s = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
    return abs(s) / 2 / 1e6


def _mask(img: Image.Image, keep: Any) -> Image.Image:
    """An 'L' mask: 255 where keep(value), else 0 (a lookup table, so it runs in C)."""
    return img.point([255 if keep(v) else 0 for v in range(256)])


def _edges(inside: Image.Image, outside: Image.Image) -> int:
    """Pixel edges between the two masks (the grid border is always outside, so wrapping is safe)."""
    n = 0
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        shifted = ImageChops.offset(outside, dx, dy)
        n += ImageChops.multiply(inside, shifted).histogram()[255]
    return n


def measure(
    walls_mm: list[list[Point]],
    closures_mm: list[tuple[float, float, float, float]],
    rooms: list[tuple[str, list[Point]]],
    rules: TraceRules,
) -> Measured:
    """Traced total and, when rooms were read, the area of each zone (m², outer faces)."""
    pts = [p for poly in walls_mm for p in poly] + [p for _, poly in rooms for p in poly]
    if not pts:
        return Measured(0.0)
    grid, img = footprint(pts, walls_mm, closures_mm, rules.areas.raster_mm_per_px)
    # room ids 2..241 on a label image; walls stay WALL until claimed by the nearest room
    lab_img = img.copy()
    draw = ImageDraw.Draw(lab_img)
    zone_ids: dict[int, str] = {}
    outdoor: dict[str, float] = {}
    for i, (name, poly) in enumerate(rooms[:240]):
        rid = i + 2
        zone_ids[rid] = rules.zone_of(name)
        draw.polygon([grid.pt(p) for p in poly], fill=rid)
        if zone_ids[rid] in rules.areas.outdoor_zones:  # measured as outlined
            outdoor[zone_ids[rid]] = outdoor.get(zone_ids[rid], 0.0) + _polygon_m2(poly)
    lab_img.paste(WALL, mask=_mask(img, lambda v: v == WALL))  # walls cut through room outlines
    w = grid.w
    data = bytearray(lab_img.tobytes())
    if rooms:  # unlabelled space inside the footprint (halls) is living and takes its half of a wall
        zone_ids[UNLABELLED] = "living"
        data = bytearray(UNLABELLED if v == FREE else v for v in data)
    claims = {i for i, z in zone_ids.items() if z not in rules.areas.outdoor_zones}
    queue = deque(i for i, v in enumerate(data) if v in claims)
    n = len(data)
    while queue:
        i = queue.popleft()
        v = data[i]
        x = i % w
        for j in (i - 1 if x > 0 else -1, i + 1 if x < w - 1 else -1, i - w, i + w):
            if 0 <= j < n and data[j] == WALL:
                data[j] = v
                queue.append(j)
    lab_img = Image.frombytes("L", lab_img.size, bytes(data))
    hist = lab_img.histogram()
    cell = (grid.px / 1000) ** 2
    veneer = rules.areas.veneer_mm * grid.px / 1e6  # m² per exterior pixel edge
    outside = _mask(lab_img, lambda v: v == OUTSIDE)
    inside_px = sum(hist) - hist[OUTSIDE]
    total = inside_px * cell + _edges(_mask(lab_img, lambda v: v != OUTSIDE), outside) * veneer
    out = Measured(round(total, 2))
    if not rooms:
        return out
    for zone in ZONES:
        ids = {i for i, z in zone_ids.items() if z == zone}
        if zone in outdoor:
            out.zones[zone] = round(outdoor[zone], 2)
        elif ids:
            edges = _edges(_mask(lab_img, lambda v, ids=ids: v in ids), outside)
            out.zones[zone] = round(sum(hist[i] for i in ids) * cell + edges * veneer, 2)
    out.zones["living"] = round(total - sum(out.zones.values()), 2)
    return out


def compare(schedule: dict[str, float], measured: Measured, tolerance: float) -> list[dict[str, Any]]:
    """Area check rows in schedule order: area, traced_m2, schedule_m2, within (schema area_check)."""
    rows = []
    for zone in ("living", *ZONES, "total"):
        traced = measured.total_m2 if zone == "total" else measured.zones.get(zone)
        drawn = schedule.get(zone)
        if drawn is None and traced is None:
            continue
        within = traced is not None and drawn is not None and abs(traced - drawn) <= tolerance * drawn
        rows.append(
            {
                "area": zone,
                "traced_m2": traced if traced is not None else 0.0,
                "schedule_m2": drawn,
                "within": within,
                "read": traced is not None,
            }
        )
    return rows
