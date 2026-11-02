"""Cropped views of the plan for the readout models (PLAN_VISUALS_SPEC section 3a; local, no model, no cost).

A whole sheet is mostly title block and margin, so the plan on it is small. These views cut the plan out at
high detail:

- `plan_crop`: the plan's bounding box (the traced walls plus a margin) cut from the sheet, as printed;
- zone tiles: that box cut into overlapping tiles, each shown twice, once as printed and once with the traced
  walls, room outlines and openings drawn over it (the overlay a model checks against the drawing).

Everything is in mm in the page frame of `vector_walls` (x right, y down, at the drawing scale), so a position
read from a tile converts back to the page by `to_page`. Nothing here changes the trace.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from render_agent.plan.focus import Area
from render_agent.plan.source import Source
from render_agent.plan.trace_rules import TraceRules
from render_agent.util.colour import hex_to_rgb
from render_agent.util.fs import atomic_write_bytes, atomic_write_json

Rect = tuple[float, float, float, float]
Point = tuple[float, float]


@dataclass(frozen=True)
class Tile:
    index: int
    box: Rect  # mm, page frame

    @property
    def name(self) -> str:
        return f"tile-{self.index}"


def plan_box(walls: list[Rect], rules: TraceRules, page_mm: Point | None = None) -> Rect | None:
    """The traced walls' bounding box plus the margin, inside the page."""
    if not walls:
        return None
    m = rules.tiles.plan_margin_mm
    box = (
        min(w[0] for w in walls) - m,
        min(w[1] for w in walls) - m,
        max(w[2] for w in walls) + m,
        max(w[3] for w in walls) + m,
    )
    if page_mm is not None:
        box = (max(0.0, box[0]), max(0.0, box[1]), min(page_mm[0], box[2]), min(page_mm[1], box[3]))
    return box


def _starts(lo: float, hi: float, size: float, overlap: float) -> list[float]:
    """Tile start positions covering lo..hi with tiles of `size` that overlap by at least `overlap`."""
    span = hi - lo
    if span <= size:
        return [lo]
    count = math.ceil((span - overlap) / (size - overlap))
    step = (span - size) / (count - 1)
    return [lo + i * step for i in range(count)]


def _wall_inside(walls: list[Rect], box: Rect) -> float:
    """Wall length (the longer side of each wall piece) inside the box, mm."""
    total = 0.0
    for r in walls:
        x0, y0, x1, y1 = max(r[0], box[0]), max(r[1], box[1]), min(r[2], box[2]), min(r[3], box[3])
        if x0 < x1 and y0 < y1:
            total += max(x1 - x0, y1 - y0)
    return total


def zone_tiles(box: Rect, walls: list[Rect], rules: TraceRules) -> list[Tile]:
    """The plan box cut into overlapping tiles; tiles with almost no wall are not cut. If the grid would be
    bigger than `max_tiles` the tiles grow until it fits."""
    t = rules.tiles
    size = t.tile_mm
    while True:
        xs = _starts(box[0], box[2], size, t.overlap_mm)
        ys = _starts(box[1], box[3], size, t.overlap_mm)
        if len(xs) * len(ys) <= t.max_tiles:
            break
        size *= 1.15
    cells = [(x, y, min(x + size, box[2]), min(y + size, box[3])) for y in ys for x in xs]
    kept = [c for c in cells if _wall_inside(walls, c) >= t.min_wall_mm]
    return [Tile(i + 1, c) for i, c in enumerate(kept)]


def render(
    source: Source, box: Rect, rules: TraceRules, max_edge_px: int | None = None
) -> tuple[Image.Image, float]:
    """The box cut from the source at the rule's detail (capped to the longest edge): (image, mm/pixel)."""
    t = rules.tiles
    cap = max_edge_px or t.max_edge_px
    px = max(t.px_mm, max(box[2] - box[0], box[3] - box[1]) / cap)
    return source.crop(*box, px).convert("RGB"), px


def to_page(tile_box: Rect, frac: Point) -> Point:
    """A position read as fractions of a tile image, in the page frame (mm)."""
    return (
        tile_box[0] + min(max(frac[0], 0.0), 1.0) * (tile_box[2] - tile_box[0]),
        tile_box[1] + min(max(frac[1], 0.0), 1.0) * (tile_box[3] - tile_box[1]),
    )


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", px)
    except OSError:
        return ImageFont.load_default(size=px)


def _centre(points: list[list[float]]) -> Point:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _meets(box: Rect, tile: Rect) -> bool:
    return box[0] < tile[2] and box[2] > tile[0] and box[1] < tile[3] and box[3] > tile[1]


def items_in(reading: dict[str, Any], box: Rect) -> list[dict[str, Any]]:
    """Rooms and openings of a code reading whose centre is inside the box, as short lines for a prompt."""
    out: list[dict[str, Any]] = []
    for r in reading.get("rooms", []):
        cx, cy = _centre(r["outline"])
        if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
            out.append({"id": r["id"], "what": f"room {r['name'] or 'unlabelled'}", "area_m2": r["area_m2"]})
    for o in reading.get("openings", []):
        f = o["frame"]
        if box[0] <= (f[0] + f[2]) / 2 <= box[2] and box[1] <= (f[1] + f[3]) / 2 <= box[3]:
            alt = f" (or {o['alternative']})" if o.get("alternative") else ""
            out.append({"id": o["id"], "what": f"opening {o['kind']}{alt}", "width_mm": o["width_mm"]})
    return out


def overlay(
    plain: Image.Image, box: Rect, px: float, walls: list[Rect], reading: dict[str, Any], rules: TraceRules
) -> Image.Image:
    """The traced walls (at the overlay alpha), room outlines with names and openings with their ids drawn
    over a tile as printed. The same colours as the designer's overlay, so both read alike."""
    o, t = rules.overlay, rules.tiles
    base = plain.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def px_of(p: Point | list[float]) -> tuple[float, float]:
        return ((p[0] - box[0]) / px, (p[1] - box[1]) / px)

    wall = (*hex_to_rgb(o.wall_colour), round(t.wall_alpha * 255))
    for r in walls:
        if _meets(r, box):
            d.rectangle([*px_of((r[0], r[1])), *px_of((r[2], r[3]))], fill=wall)
    room = (*hex_to_rgb(o.room_colour), 255)
    mark = (*hex_to_rgb(t.marker_colour), 255)
    font = _font(max(12, base.width // 70))
    for r in reading.get("rooms", []):
        cx, cy = _centre(r["outline"])
        if not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
            continue
        pts = [px_of(p) for p in r["outline"]]
        d.line([*pts, pts[0]], fill=room, width=2)
        d.text(px_of((cx, cy)), r["name"] or "?", fill=room, font=font, anchor="mm")
    for op in reading.get("openings", []):
        f = op["frame"]
        if not _meets((f[0], f[1], f[2], f[3]), box):
            continue
        a, b = px_of((f[0], f[1])), px_of((f[2], f[3]))
        d.rectangle([*a, *b], outline=mark, width=2)
        d.text((a[0], b[1] + 2), op["id"], fill=mark, font=font)
    pocket = (*hex_to_rgb(o.clearance_colour), 255)  # recesses and kitchen items: boxes with their kind
    for item in [*reading.get("recesses", []), *reading.get("kitchen", [])]:
        f = item["rect"]
        if not _meets((f[0], f[1], f[2], f[3]), box):
            continue
        a, b = px_of((f[0], f[1])), px_of((f[2], f[3]))
        d.rectangle([*a, *b], outline=pocket, width=2)
        d.text(
            (a[0], a[1] - font.size - 2 if hasattr(font, "size") else a[1]),
            str(item.get("type") or item.get("kind")),
            fill=pocket,
            font=font,
        )
    return Image.alpha_composite(base, layer).convert("RGB")


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def write(
    folder: Path,
    source: Source,
    box: Rect,
    walls: list[Rect],
    reading: dict[str, Any],
    rules: TraceRules,
) -> dict[str, Any]:
    """Cuts and saves the tiles of one dwelling under `folder` (tile-N.png as printed, tile-N_overlay.png with
    the trace, tiles.json listing them). Returns the index that was written."""
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for tile in zone_tiles(box, walls, rules):
        plain, px = render(source, tile.box, rules)
        atomic_write_bytes(folder / f"{tile.name}.png", png_bytes(plain))
        atomic_write_bytes(
            folder / f"{tile.name}_overlay.png",
            png_bytes(overlay(plain, tile.box, px, walls, reading, rules)),
        )
        entries.append(
            {
                "name": tile.name,
                "box_mm": [round(v, 1) for v in tile.box],
                "mm_per_px": round(px, 3),
                "size_px": list(plain.size),
                "plain": f"{tile.name}.png",
                "overlay": f"{tile.name}_overlay.png",
            }
        )
    index = {"box_mm": [round(v, 1) for v in box], "tiles": entries, "focus": []}
    atomic_write_json(folder / "tiles.json", index)
    return index


def write_focus(
    folder: Path,
    source: Source,
    index: dict[str, Any],
    areas: list[Area],
    walls: list[Rect],
    reading: dict[str, Any],
    rules: TraceRules,
) -> list[dict[str, Any]]:
    """Cuts and saves the close-up areas (focus-N.png as printed, focus-N_overlay.png with the trace) at the
    close-up detail, adds them to `tiles.json` under "focus" and returns their entries."""
    entries = []
    for n, area in enumerate(areas, start=1):
        px = max(
            rules.tiles.focus.px_mm,
            max(area.box[2] - area.box[0], area.box[3] - area.box[1]) / rules.tiles.max_edge_px,
        )
        b = area.box
        plain = source.crop(b[0], b[1], b[2], b[3], px).convert("RGB")
        name = f"focus-{n}"
        atomic_write_bytes(folder / f"{name}.png", png_bytes(plain))
        atomic_write_bytes(
            folder / f"{name}_overlay.png", png_bytes(overlay(plain, area.box, px, walls, reading, rules))
        )
        entries.append(
            {
                "name": name,
                "title": area.name,
                "reason": area.reason,
                "source": area.source,
                "items": area.items,
                "box_mm": [round(v, 1) for v in area.box],
                "mm_per_px": round(px, 3),
                "size_px": list(plain.size),
                "plain": f"{name}.png",
                "overlay": f"{name}_overlay.png",
            }
        )
    index["focus"] = entries
    atomic_write_json(folder / "tiles.json", index)
    return entries
