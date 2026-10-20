"""DRAFT overlay, applied to every render version until the designer approves it at review.
The overlay-free copy lives only in output/clean/, for the latest version of each render, until delivered."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

TEXT = "DRAFT"


def draft_overlay(png: bytes) -> bytes:
    with Image.open(io.BytesIO(png)) as src:
        base = src.convert("RGBA")
    w, h = base.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = ImageFont.load_default(size=max(16, h // 6))
    box = draw.textbbox((0, 0), TEXT, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    draw.text(
        ((w - tw) / 2, (h - th) / 2),
        TEXT,
        font=font,
        fill=(255, 255, 255, 150),
        stroke_width=max(2, h // 90),
        stroke_fill=(0, 0, 0, 150),
    )  # white with a dark outline: readable on light and dark images
    small = ImageFont.load_default(size=max(10, h // 40))
    for y in range(0, h, max(40, h // 6)):
        draw.text(
            (12, y + 8),
            f"{TEXT} - not approved",
            font=small,
            fill=(255, 255, 255, 130),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 130),
        )
    out = Image.alpha_composite(base, layer).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
