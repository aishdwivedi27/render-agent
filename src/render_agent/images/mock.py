"""Mock provider: draws a labelled practice image (a plain house shape). Free; used for dry runs.

Output is deterministic (same request -> same bytes), so byte-identical checks work on mock runs.
`faults` lets tests inject a sequence of outcomes per render_id: an exception to raise or raw bytes
to return instead of the placeholder.
"""

from __future__ import annotations

import io
import threading
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from render_agent.images.base import RenderRequest

SKY = (214, 226, 234)
GROUND = (120, 140, 118)
WALL = (236, 233, 226)
ROOF = (86, 92, 100)
GLASS = (150, 176, 196)
INK = (40, 44, 52)


class MockProvider:
    name = "mock"
    model = "mock"
    paid = False

    def __init__(self, faults: dict[str, list[Any]] | None = None) -> None:
        self.faults = {k: list(v) for k, v in (faults or {}).items()}
        self.calls: list[RenderRequest] = []
        self._lock = threading.Lock()

    def render(self, req: RenderRequest) -> bytes:
        with self._lock:
            self.calls.append(req)
            queue = self.faults.get(req.render_id)
            fault = queue.pop(0) if queue else None
        if isinstance(fault, BaseException):
            raise fault
        if isinstance(fault, bytes):
            return fault
        return placeholder(req)


def placeholder(req: RenderRequest) -> bytes:
    """An obvious practice image: a plain house shape and a label. No prompt text and no drawing
    crops, so nobody mistakes it for a render or for the drawings."""
    w, h = (int(x) for x in req.size.split("x")) if "x" in req.size else (1536, 1024)
    w, h = max(64, w // 2), max(64, h // 2)  # half size keeps dry runs light
    im = Image.new("RGB", (w, h), SKY)
    draw = ImageDraw.Draw(im)
    ground = int(h * 0.72)
    draw.rectangle((0, ground, w, h), fill=GROUND)
    bw, bh = int(w * 0.46), int(h * 0.30)
    x0, y0 = (w - bw) // 2, ground - bh
    draw.rectangle((x0, y0, x0 + bw, ground), fill=WALL, outline=INK, width=max(1, h // 200))
    draw.polygon(
        [(x0 - bw // 14, y0), (x0 + bw // 2, y0 - int(bh * 0.55)), (x0 + bw + bw // 14, y0)], fill=ROOF
    )
    dw, dh = bw // 7, int(bh * 0.6)
    draw.rectangle((x0 + bw // 2 - dw // 2, ground - dh, x0 + bw // 2 + dw // 2, ground), fill=ROOF)
    for wx in (x0 + bw // 8, x0 + bw - bw // 8 - bw // 6):
        draw.rectangle((wx, y0 + bh // 4, wx + bw // 6, y0 + bh // 4 + bh // 3), fill=GLASS)
    big = ImageFont.load_default(size=max(14, h // 14))
    small = ImageFont.load_default(size=max(10, h // 30))
    draw.text((w // 2, h // 9), "PRACTICE IMAGE", font=big, fill=INK, anchor="mm")
    draw.text(
        (w // 2, h // 9 + h // 11),
        "No real render was made. Use a real run to judge the house.",
        font=small,
        fill=INK,
        anchor="mm",
    )
    draw.text(
        (w // 2, ground + (h - ground) // 2), req.render_id, font=small, fill=(255, 255, 255), anchor="mm"
    )
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()
