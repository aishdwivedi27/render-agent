"""What the code reads inside and beside a wall opening: a crop of the drawing as printed, and its words.

`PdfSource` draws a vector page without its text and dimension dots (the same page the walls are read
from) at any detail; `ImageSource` cuts the same crop from a scan or image page. Both answer in mm in the
page frame of `vector_walls` (x right, y down, at the drawing scale), so the readers never care which
kind of page they are looking at.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pymupdf
from PIL import Image

from render_agent.plan import vector_walls
from render_agent.plan.trace_rules import Walls


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2


class Source(Protocol):
    def crop(self, x0: float, y0: float, x1: float, y1: float, px_mm: float) -> Image.Image: ...


class PdfSource:
    """A vector page drawn without text and dimension dots, cropped at `px_mm` mm per pixel."""

    def __init__(self, pdf: Path, page_number: int, scale_ratio: int, rules: Walls) -> None:
        self.factor = vector_walls.mm_per_pt(scale_ratio)
        self._doc = vector_walls._page_without_text(pdf, page_number, self.factor, rules)
        self._page = self._doc[0]

    def crop(self, x0: float, y0: float, x1: float, y1: float, px_mm: float) -> Image.Image:
        zoom = self.factor / px_mm
        clip = pymupdf.Rect(x0 / self.factor, y0 / self.factor, x1 / self.factor, y1 / self.factor)
        pix = self._page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), clip=clip, colorspace=pymupdf.csGRAY, alpha=False
        )
        return Image.frombytes("L", (pix.width, pix.height), pix.samples, "raw", "L", pix.stride)

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> PdfSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class PdfView:
    """A vector page drawn as printed (text kept), cropped at `px_mm` mm per pixel, in colour: what a model or
    a designer looks at. Unlike `PdfSource`, nothing is removed from the drawing."""

    def __init__(self, pdf: Path, page_number: int, scale_ratio: int) -> None:
        self.factor = vector_walls.mm_per_pt(scale_ratio)
        self._doc = pymupdf.open(pdf)
        self._page = self._doc[page_number - 1]

    def crop(self, x0: float, y0: float, x1: float, y1: float, px_mm: float) -> Image.Image:
        zoom = self.factor / px_mm
        clip = pymupdf.Rect(x0 / self.factor, y0 / self.factor, x1 / self.factor, y1 / self.factor)
        pix = self._page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples, "raw", "RGB", pix.stride)

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> PdfView:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class ImageSource:
    """A scan or image page: `mm_per_px` is the image's pixel size at the drawing scale."""

    def __init__(self, img: Image.Image, mm_per_px: float) -> None:
        self.img = img.convert("L")
        self.mm_per_px = mm_per_px

    def crop(self, x0: float, y0: float, x1: float, y1: float, px_mm: float) -> Image.Image:
        s = self.mm_per_px
        box = (int(x0 / s), int(y0 / s), max(int(x0 / s) + 1, int(x1 / s)), max(int(y0 / s) + 1, int(y1 / s)))
        piece = self.img.crop(box)
        size = (max(1, round((x1 - x0) / px_mm)), max(1, round((y1 - y0) / px_mm)))
        return piece.resize(size, Image.Resampling.BILINEAR)

    def __enter__(self) -> ImageSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.img.close()


def page_words(pdf: Path, page_number: int, scale_ratio: int) -> list[Word]:
    """The words of a vector page in the page frame (mm). A scan has none."""
    factor = vector_walls.mm_per_pt(scale_ratio)
    with pymupdf.open(pdf) as doc:
        raw = doc[page_number - 1].get_text("words")
    return [Word(w[0] * factor, w[1] * factor, w[2] * factor, w[3] * factor, str(w[4])) for w in raw]


def distance_to_rect(point: tuple[float, float], rect: tuple[float, float, float, float]) -> float:
    dx = max(rect[0] - point[0], 0.0, point[0] - rect[2])
    dy = max(rect[1] - point[1], 0.0, point[1] - rect[3])
    return float((dx * dx + dy * dy) ** 0.5)
