"""plan/<dwelling>/geometry.json from the code-traced walls and the readout model's reading
(PLAN_VISUALS_SPEC section 3).

The model reads in page fractions; code converts to mm (page coordinates at the drawing scale, y down),
snaps room, recess and fixture corners onto the nearest wall face, hosts each opening on the nearest
wall, splits the plan per dwelling and checks whether a dwelling repeats or mirrors another.
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from render_agent.plan.trace_rules import TraceRules

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
MIRROR_PX_MM = 100.0  # resolution of the mirror comparison


def to_mm(p: list[float], page_mm: tuple[float, float]) -> Point:
    return (round(float(p[0]) * page_mm[0], 1), round(float(p[1]) * page_mm[1], 1))


def outline_mm(outline: list[list[float]], page_mm: tuple[float, float]) -> list[Point]:
    return [to_mm(p, page_mm) for p in outline if len(p) >= 2]


def region_mm(region: dict[str, float], page_mm: tuple[float, float]) -> Rect:
    x0, x1 = sorted((region["x0"] * page_mm[0], region["x1"] * page_mm[0]))
    y0, y1 = sorted((region["y0"] * page_mm[1], region["y1"] * page_mm[1]))
    return (x0, y0, x1, y1)


def bounds(rects: list[Rect]) -> Rect:
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def inside(r: Rect, region: Rect) -> bool:
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def clip(r: Rect, region: Rect) -> Rect | None:
    """The part of the rectangle inside the region (an opening cut at the dwelling's edge), or None."""
    x0, y0, x1, y1 = max(r[0], region[0]), max(r[1], region[1]), min(r[2], region[2]), min(r[3], region[3])
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


def faces(walls: list[Rect]) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Wall faces: x faces (x, y0, y1) and y faces (y, x0, x1)."""
    xs = [(x, r[1], r[3]) for r in walls for x in (r[0], r[2])]
    ys = [(y, r[0], r[2]) for r in walls for y in (r[1], r[3])]
    return xs, ys


def _snap_axis(
    values: list[tuple[float, float]], faces_: list[tuple[float, float, float]], tol: float
) -> dict[float, float]:
    """Each distinct coordinate (with the span the corners on it cover across) -> the nearest wall face
    within `tol` whose own span overlaps it; corners sharing a coordinate move together."""
    spans: dict[float, list[float]] = {}
    for v, other in values:
        key = round(v, 1)
        span = spans.setdefault(key, [other, other])
        span[0], span[1] = min(span[0], other), max(span[1], other)
    out = {}
    for v, (lo, hi) in spans.items():
        near = [f for f in faces_ if abs(f[0] - v) <= tol and f[1] - tol <= hi and lo <= f[2] + tol]
        out[v] = min(near, key=lambda f: abs(f[0] - v))[0] if near else v
    return out


def snap(poly: list[Point], walls: list[Rect], tol: float) -> list[Point]:
    """Corners moved onto the nearest wall face within `tol`; edges stay straight because every corner
    on the same x (or y) moves to the same face."""
    if not walls or tol <= 0:
        return poly
    xs, ys = faces(walls)
    sx = _snap_axis([(x, y) for x, y in poly], xs, tol)
    sy = _snap_axis([(y, x) for x, y in poly], ys, tol)
    return [(round(sx[round(x, 1)], 1), round(sy[round(y, 1)], 1)) for x, y in poly]


def host(at: Point, walls: list[Rect], ids: list[str]) -> tuple[str, float]:
    """The nearest wall to an opening and the opening's position along it (mm from its start)."""
    if not walls:
        return "NOT FOUND", 0.0

    def dist(r: Rect) -> float:
        dx = max(r[0] - at[0], 0.0, at[0] - r[2])
        dy = max(r[1] - at[1], 0.0, at[1] - r[3])
        return dx * dx + dy * dy

    i = min(range(len(walls)), key=lambda k: dist(walls[k]))
    r = walls[i]
    along = at[0] - r[0] if (r[2] - r[0]) >= (r[3] - r[1]) else at[1] - r[1]
    return ids[i], round(along, 1)


def poly_area_m2(poly: list[Point]) -> float:
    n = len(poly)
    s = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
    return round(abs(s) / 2 / 1e6, 2)


def _wall_image(walls: list[Rect], region: Rect, size: tuple[int, int], flip: bool) -> Image.Image:
    img = Image.new("L", size, 0)
    draw = ImageDraw.Draw(img)
    for r in walls:
        x0, x1 = (r[0] - region[0]) / MIRROR_PX_MM, (r[2] - region[0]) / MIRROR_PX_MM
        if flip:
            width = (region[2] - region[0]) / MIRROR_PX_MM  # flipped within its own box
            x0, x1 = width - x1, width - x0
        draw.rectangle(
            [x0, (r[1] - region[1]) / MIRROR_PX_MM, x1, (r[3] - region[1]) / MIRROR_PX_MM], fill=255
        )
    return img


def _share(img: Image.Image, target: Image.Image) -> float:
    """Share of `img`'s wall pixels that land on (or next to) `target`'s walls."""
    total = img.histogram()[255]
    hit = ImageChops.multiply(img, target.filter(ImageFilter.MaxFilter(3))).histogram()[255]
    return hit / total if total else 0.0


def match(a: list[Rect], b: list[Rect]) -> tuple[str, float]:
    """('repeated' | 'mirrored', match) for the better of the two, where match is the smaller of the
    share of a's walls on b's and of b's walls on a's (so extra walls on either side lower it). Each
    dwelling is compared in its own walls' box, so where it sits on the page does not matter."""
    if not a or not b:
        return "repeated", 0.0
    ra, rb = bounds(a), bounds(b)
    size = (
        int(max(ra[2] - ra[0], rb[2] - rb[0]) / MIRROR_PX_MM) + 2,
        int(max(ra[3] - ra[1], rb[3] - rb[1]) / MIRROR_PX_MM) + 2,
    )
    other = _wall_image(b, rb, size, False)
    best = ("repeated", 0.0)
    for kind, flip in (("repeated", False), ("mirrored", True)):
        mine = _wall_image(a, ra, size, flip)
        score = round(min(_share(mine, other), _share(other, mine)), 3)
        if score > best[1]:
            best = (kind, score)
    return best


def build(
    dwelling: str,
    region: Rect,
    walls: list[Rect],
    exterior: list[bool],
    reading: dict[str, Any],
    page_mm: tuple[float, float],
    scale: dict[str, Any],
    method: str,
    models: list[str],
    rules: TraceRules,
) -> dict[str, Any]:
    """geometry.json for one dwelling (schemas/plan_geometry.json, without area_check)."""
    ids = [f"W{i + 1}" for i in range(len(walls))]
    tol = rules.snap_mm

    def mine(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            x
            for x in items
            if x.get("dwelling") in (dwelling, "", None) or len(reading.get("dwellings", [])) <= 1
        ]

    rooms = []
    for r in mine(reading.get("rooms", [])):
        poly = snap(outline_mm(r["outline"], page_mm), walls, tol)
        if len(poly) >= 3:
            rooms.append(
                {
                    "id": r["id"],
                    "name": r["name"].upper(),
                    "outline": [list(p) for p in poly],
                    "area_m2": poly_area_m2(poly),
                    "evidence": r["evidence"],
                }
            )
    openings = []
    for o in mine(reading.get("openings", [])):
        wall, along = host(to_mm(o["at"], page_mm), walls, ids)
        openings.append(
            {
                "id": o["id"],
                "kind": o["kind"],
                "host_wall": wall,
                "position_mm": along,
                "width_mm": o["width_mm"],
                "code": o["code"],
                "swing": o["swing"],
                "rooms": list(o["rooms"]),
                "evidence": o["evidence"],
            }
        )

    def shapes(items: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
        out = []
        for x in mine(items):
            poly = snap(outline_mm(x["outline"], page_mm), walls, tol)
            if len(poly) >= 3:
                out.append(
                    {
                        **{k: x[k] for k in fields},
                        "outline": [list(p) for p in poly],
                        "evidence": x["evidence"],
                    }
                )
        return out

    return {
        "dwelling": dwelling,
        "mirror_of": None,
        "method": method,
        "models": models,
        "scale": scale,
        "region": list(region),
        "walls": [
            {
                "id": ids[i],
                "outline": [[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]]],
                "exterior": exterior[i],
            }
            for i, r in enumerate(walls)
        ],
        "rooms": rooms,
        "openings": openings,
        "recesses": shapes(reading.get("recesses", []), ("id", "kind", "room")),
        "fixtures": [
            {**f, "source": "drawn"} for f in shapes(reading.get("fixtures", []), ("id", "type", "room"))
        ],
        "clearance_zones": [
            {**z, "kind": "wc_clearance"} for z in shapes(reading.get("clearance_zones", []), ("id", "room"))
        ],
    }
