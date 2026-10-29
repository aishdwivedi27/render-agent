"""plan/<dwelling>/overlay.png: the drawing with the traced walls at 50%, room outlines and names, and
the clearance zones labelled "accessibility clearance (not drawn)" (PLAN_VISUALS_SPEC sections 3, 11).
Every overlay carries the "Indicative Image Only" disclaimer and is checked before it is kept."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from render_agent.errors import PermanentError
from render_agent.output import disclaimer
from render_agent.plan.trace_rules import TraceRules
from render_agent.util.colour import hex_to_rgb
from render_agent.util.fs import atomic_write_bytes

Rect = tuple[float, float, float, float]


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", px)
    except OSError:
        return ImageFont.load_default(size=px)


def draw(
    page_png: Path, mm_per_px: float, geometry: dict[str, Any], region: Rect, rules: TraceRules
) -> Image.Image:
    """The overlay image (without the disclaimer). `mm_per_px` maps mm to pixels of `page_png`."""
    o = rules.overlay
    with Image.open(page_png) as src:
        page = src.convert("RGB")
    m = o.margin_mm
    box = (
        max(0, int((region[0] - m) / mm_per_px)),
        max(0, int((region[1] - m) / mm_per_px)),
        min(page.width, int((region[2] + m) / mm_per_px) + 1),
        min(page.height, int((region[3] + m) / mm_per_px) + 1),
    )
    base = page.crop(box).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    alpha = round(o.alpha * 255)

    def px(p: list[float] | tuple[float, float]) -> tuple[float, float]:
        return (p[0] / mm_per_px - box[0], p[1] / mm_per_px - box[1])

    wall = (*hex_to_rgb(o.wall_colour), alpha)
    for w in geometry["walls"]:
        d.polygon([px(p) for p in w["outline"]], fill=wall)
    room = (*hex_to_rgb(o.room_colour), 255)
    font = _font(max(12, base.width // 90))
    for r in geometry["rooms"]:
        pts = [px(p) for p in r["outline"]]
        d.line([*pts, pts[0]], fill=room, width=2)
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        d.text((cx, cy), r["name"], fill=room, font=font, anchor="mm")
    zone = hex_to_rgb(o.clearance_colour)
    small = _font(max(14, base.width // 70))
    for z in geometry["clearance_zones"]:
        pts = [px(p) for p in z["outline"]]
        d.polygon(pts, fill=(*zone, alpha // 2), outline=(*zone, 255), width=3)
        x0, y1 = min(p[0] for p in pts), max(p[1] for p in pts)
        width = d.textlength(rules.clearance_label, font=small)
        at = (max(4.0, min(x0, base.width - width - 8)), y1 + 6)  # kept inside the image
        tb = d.textbbox(at, rules.clearance_label, font=small)
        d.rectangle(
            (tb[0] - 4, tb[1] - 3, tb[2] + 4, tb[3] + 3), fill=(255, 255, 255, 235), outline=(*zone, 255)
        )
        d.text(at, rules.clearance_label, fill=(*zone, 255), font=small)
    out = Image.alpha_composite(base, layer).convert("RGB")
    if max(out.size) > o.max_edge_px:
        out.thumbnail((o.max_edge_px, o.max_edge_px))
    return out


def save(img: Image.Image, dest: Path) -> None:
    """Stamps the disclaimer, checks it (presence and contrast), stamps once more if the check fails,
    and writes the PNG atomically. A second failure stops the stage (SPEC section 9)."""
    for _attempt in range(2):
        stamped, placement = disclaimer.stamp_image(img)
        if disclaimer.check_image(stamped, placement).ok:
            atomic_write_bytes(dest, disclaimer.png_bytes(stamped, placement))
            return
    raise PermanentError(
        code="DISCLAIMER_FAILED",
        what_happened=f"The 'Indicative Image Only' mark could not be placed clearly on {dest.name}.",
        likely_cause="The drawing is very busy or very dark where the mark goes.",
        what_to_do="Press Retry. If it stops again, use Help → Copy diagnostics.",
    )
