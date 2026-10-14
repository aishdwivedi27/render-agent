"""Mock provider: draws a labelled placeholder with reference thumbnails. Free; used for dry runs.

Output is deterministic (same request -> same bytes), so byte-identical checks work on mock runs.
`faults` lets tests inject a sequence of outcomes per render_id: an exception to raise or raw bytes
to return instead of the placeholder.
"""

from __future__ import annotations

import io
import textwrap
import threading
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from render_agent.images.base import RenderRequest

BACKGROUND = (226, 230, 234)
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
    w, h = (int(x) for x in req.size.split("x")) if "x" in req.size else (1536, 1024)
    w, h = max(64, w // 2), max(64, h // 2)  # half size keeps dry runs light
    im = Image.new("RGB", (w, h), BACKGROUND)
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=max(12, h // 30))
    lines = [f"MOCK RENDER  {req.render_id}", ""]
    for para in req.prompt.splitlines()[:14]:
        lines += textwrap.wrap(para, width=max(20, w // max(8, h // 50))) or [""]
    draw.multiline_text((16, 16), "\n".join(lines[:22]), fill=INK, font=font)
    thumb_w = w // 4
    for i, ref in enumerate(req.references[:3]):
        try:
            with Image.open(ref) as src:
                t = src.convert("RGB")
                t.thumbnail((thumb_w, h // 4))
        except OSError:
            continue
        im.paste(t, (16 + i * (thumb_w + 8), h - t.height - 16))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()
