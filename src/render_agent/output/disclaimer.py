""" "Indicative Image Only" on every image and PDF page (PLAN_VISUALS_SPEC section 11, R9).

One implementation for plans and renders. Placement: rotated 90 degrees (reads bottom to top) on the
left edge, vertically centred, inset about 2%; when the left edge is busy, the clearer bottom corner,
horizontal. On light backgrounds charcoal text on a white pill (~85%) with a thin outline; otherwise
white text on a charcoal pill (~60%). The pill gets more opaque until the text contrast is at least
4.5:1 against the worst pixels underneath.

The checks look at the pixels, not at what was asked for: a file is deliverable only when the
disclaimer is found where its placement says and its text contrast is at least 4.5:1. PNG files carry
the placement in a text chunk; other formats pass the placement in.
"""

from __future__ import annotations

import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageStat
from PIL.PngImagePlugin import PngInfo

from render_agent.util.colour import contrast, luminance
from render_agent.util.fs import atomic_write_bytes

TEXT = "Indicative Image Only"
MIN_CONTRAST = 4.5
CHARCOAL = (31, 31, 31)
WHITE = (255, 255, 255)
DARK_PILL_ALPHA = 0.60
LIGHT_PILL_ALPHA = 0.85
CAP_FRACTION = 0.0175  # cap height as a share of the short side (spec: 1.5 to 2%)
INSET_FRACTION = 0.02
BUSY_STDDEV = 32.0  # luminance spread (0-255) above which an edge holds content
PDF_MIN_PT = 10.0
MIN_TEXT_PX = 14  # smaller text is mostly anti-aliasing and cannot reach 4.5:1
PNG_KEY = "indicative_disclaimer"
CHECK_SCALE = 3  # PDF pages are rasterised at this zoom to check the stamp


@dataclass(frozen=True)
class Placement:
    position: str  # left | bottom_left | bottom_right
    box: tuple[int, int, int, int]  # pill bounds in pixels (x0, y0, x1, y1)
    light: bool  # True: charcoal text on a white pill
    alpha: float


@dataclass(frozen=True)
class Check:
    ok: bool
    contrast: float
    problem: str = ""


class DisclaimerError(Exception):
    """A file without a readable disclaimer is not deliverable (the caller rebuilds it)."""


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=max(px, 8))


def _pill_size(short_side: int) -> tuple[int, int, int, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    """(width, height, text size, font) of the horizontal pill for an image this size."""
    size = max(round(short_side * CAP_FRACTION / 0.72), MIN_TEXT_PX)  # cap height ~ 0.72 of the size
    font = _font(size)
    x0, y0, x1, y1 = font.getbbox(TEXT)
    pad = max(round(size * 0.45), 3)
    return round(x1 - x0) + 2 * pad, round(y1 - y0) + 2 * pad, size, font


def _candidates(w: int, h: int, pw: int, ph: int) -> list[tuple[str, tuple[int, int, int, int]]]:
    ix, iy = round(w * INSET_FRACTION), round(h * INSET_FRACTION)
    left_y0 = (h - pw) // 2  # rotated: the pill is ph wide and pw tall
    return [
        ("left", (ix, left_y0, ix + ph, left_y0 + pw)),
        ("bottom_left", (ix, h - iy - ph, ix + pw, h - iy)),
        ("bottom_right", (w - ix - pw, h - iy - ph, w - ix, h - iy)),
    ]


def _spread(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    return float(ImageStat.Stat(img.convert("L").crop(box)).stddev[0])


def _choose(img: Image.Image, pw: int, ph: int) -> tuple[str, tuple[int, int, int, int]]:
    w, h = img.size
    options = [
        (n, b) for n, b in _candidates(w, h, pw, ph) if b[0] >= 0 and b[1] >= 0 and b[2] <= w and b[3] <= h
    ]
    if not options:
        raise DisclaimerError(f"The image ({w}x{h}) is too small for the disclaimer.")
    for name, box in options:
        if _spread(img, box) <= BUSY_STDDEV:
            return name, box
    return min(options, key=lambda o: _spread(img, o[1]))


def _alpha_for(
    region: Image.Image, pill: tuple[int, int, int], text: tuple[int, int, int], start: float
) -> float:
    """The smallest opacity from `start` that keeps the text at 4.5:1 over the worst pixel below."""
    lum = region.convert("L")
    hist = lum.histogram()
    total = sum(hist)
    # the 2nd and 98th percentile grey: the darkest and lightest the pill has to cover
    acc, lo, hi = 0, 0, 255
    for v, n in enumerate(hist):
        acc += n
        if acc >= total * 0.02 and lo == 0:
            lo = v
        if acc >= total * 0.98:
            hi = v
            break
    alpha = start
    while alpha < 1.0:
        worst = min(contrast(text, tuple(alpha * p + (1 - alpha) * g for p in pill)) for g in (lo, hi))
        if worst >= MIN_CONTRAST + 0.3:  # margin for anti-aliasing and JPG
            break
        alpha = min(alpha + 0.05, 1.0)
    return round(alpha, 2)


def stamp_image(img: Image.Image) -> tuple[Image.Image, Placement]:
    """A copy of `img` (RGB) with the disclaimer, and where it went."""
    base = img.convert("RGB")
    w, h = base.size
    pw, ph, size, font = _pill_size(min(w, h))
    position, box = _choose(base, pw, ph)
    region = base.crop(box)
    light = luminance(ImageStat.Stat(region).mean) > 0.5
    pill_rgb, text_rgb = (WHITE, CHARCOAL) if light else (CHARCOAL, WHITE)
    alpha = _alpha_for(region, pill_rgb, text_rgb, LIGHT_PILL_ALPHA if light else DARK_PILL_ALPHA)

    tile = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    outline = (*CHARCOAL, 140) if light else None
    draw.rounded_rectangle(
        (0, 0, pw - 1, ph - 1), radius=ph // 2, fill=(*pill_rgb, round(alpha * 255)), outline=outline, width=1
    )
    bx0, by0, bx1, by1 = font.getbbox(TEXT)
    draw.text(
        ((pw - (bx1 - bx0)) / 2 - bx0, (ph - (by1 - by0)) / 2 - by0), TEXT, font=font, fill=(*text_rgb, 255)
    )
    if position == "left":
        tile = tile.rotate(90, expand=True)  # counter-clockwise: reads bottom to top
    out = base.convert("RGBA")
    out.alpha_composite(tile, (box[0], box[1]))
    return out.convert("RGB"), Placement(position, box, light, alpha)


def png_bytes(img: Image.Image, placement: Placement) -> bytes:
    """PNG with the placement recorded, so check_png can find the stamp later."""
    info = PngInfo()
    info.add_text(PNG_KEY, json.dumps(asdict(placement)))
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


def placement_of(img: Image.Image) -> Placement | None:
    raw = img.info.get(PNG_KEY)  # PNG text chunks load into info
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return Placement(d["position"], tuple(d["box"]), bool(d["light"]), float(d["alpha"]))  # type: ignore[arg-type]
    except (ValueError, KeyError, TypeError):
        return None


def _two_tone(region: Image.Image) -> Check:
    """Splits the pill into text and background pixels and measures their contrast."""
    raw = region.convert("RGB").tobytes()
    pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
    if not pixels:
        return Check(False, 0.0, "The disclaimer area is empty.")
    lums = sorted(luminance(p) for p in pixels)
    lo, hi = lums[len(lums) // 20], lums[-(len(lums) // 20) - 1]
    if hi - lo < 0.05:
        return Check(False, 1.0, "No disclaimer text found where it should be.")
    mid = (lo + hi) / 2
    dark = [p for p in pixels if luminance(p) < mid]
    bright = [p for p in pixels if luminance(p) >= mid]
    small = min(len(dark), len(bright)) / len(pixels)
    if small < 0.02:
        return Check(False, 1.0, "No disclaimer text found where it should be.")

    # background: the bigger group's median; text: its solid core (anti-aliased edges are mixed)
    text, ground = (dark, bright) if len(dark) <= len(bright) else (bright, dark)
    text.sort(key=luminance, reverse=text is bright)
    ground.sort(key=luminance)
    core, back = text[len(text) // 4], ground[len(ground) // 2]
    ratio = contrast(core, back)
    if ratio < MIN_CONTRAST:
        return Check(False, ratio, f"The disclaimer contrast is {ratio:.1f}:1; it must be at least 4.5:1.")
    return Check(True, ratio)


def check_image(img: Image.Image, placement: Placement | None = None) -> Check:
    placement = placement or placement_of(img)
    if placement is None:
        return Check(False, 0.0, "This image has no disclaimer.")
    w, h = img.size
    x0, y0, x1, y1 = placement.box
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return Check(False, 0.0, "The disclaimer is outside the image.")
    return _two_tone(img.crop(placement.box))


# ---------- PDF pages ----------


def _page_image(page: pymupdf.Page, zoom: float) -> Image.Image:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _stamp_page(page: pymupdf.Page) -> None:
    rect = page.rect
    short = min(rect.width, rect.height)
    size = max(short * CAP_FRACTION / 0.72, PDF_MIN_PT)
    text_w = pymupdf.get_text_length(TEXT, fontname="helv", fontsize=size)
    pad = size * 0.45
    pw, ph = text_w + 2 * pad, size + 2 * pad
    zoom = 1.0
    preview = _page_image(page, zoom)
    position, box = _choose(preview, round(pw), round(ph))
    region = preview.crop(box)
    light = luminance(ImageStat.Stat(region).mean) > 0.5
    pill_rgb, text_rgb = (WHITE, CHARCOAL) if light else (CHARCOAL, WHITE)
    alpha = _alpha_for(region, pill_rgb, text_rgb, LIGHT_PILL_ALPHA if light else DARK_PILL_ALPHA)
    r = pymupdf.Rect(*box)
    shape = page.new_shape()
    shape.draw_rect(r, radius=0.5 if position != "left" else None)
    shape.finish(
        color=tuple(c / 255 for c in CHARCOAL) if light else None,
        fill=tuple(c / 255 for c in pill_rgb),
        fill_opacity=alpha,
        width=0.5,
        stroke_opacity=0.55,
    )
    shape.commit()
    colour = tuple(c / 255 for c in text_rgb)
    if position == "left":  # rotated 90: the baseline runs up the left edge
        point = pymupdf.Point(r.x1 - pad - size * 0.22, r.y1 - pad)
        page.insert_text(point, TEXT, fontname="helv", fontsize=size, color=colour, rotate=90)
    else:
        point = pymupdf.Point(r.x0 + pad, r.y1 - pad - size * 0.22)
        page.insert_text(point, TEXT, fontname="helv", fontsize=size, color=colour)


def stamp_pdf(src: Path, dest: Path | None = None) -> Path:
    """Stamps every page; writes atomically to `dest` (default: in place)."""
    dest = dest or src
    with pymupdf.open(src) as doc:
        for page in doc:
            _stamp_page(page)
        data = doc.tobytes(garbage=3, deflate=True)
    atomic_write_bytes(dest, data)
    return dest


def check_pdf(path: Path) -> list[Check]:
    """One result per page: the text is on the page and readable at 4.5:1."""
    results: list[Check] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            hits = page.search_for(TEXT)
            if not hits:
                results.append(Check(False, 0.0, f"Page {page.number + 1} has no disclaimer."))
                continue
            size_ok = _text_size(page) >= PDF_MIN_PT - 0.01
            img = _page_image(page, CHECK_SCALE)
            area = hits[0]
            for h in hits[1:]:
                area |= h
            pad = 2
            crop = tuple(
                round(v * CHECK_SCALE) for v in (area.x0 - pad, area.y0 - pad, area.x1 + pad, area.y1 + pad)
            )
            check = _two_tone(img.crop(crop))  # type: ignore[arg-type]
            if check.ok and not size_ok:
                check = Check(
                    False, check.contrast, f"Page {page.number + 1}: the disclaimer is smaller than 10 pt."
                )
            results.append(check)
    return results


def _text_size(page: pymupdf.Page) -> float:
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if TEXT in span["text"]:
                    return float(span["size"])
    return 0.0


def require_ok(results: Check | list[Check], name: str) -> None:
    """Raises DisclaimerError when any result fails (file not deliverable)."""
    for r in results if isinstance(results, list) else [results]:
        if not r.ok:
            raise DisclaimerError(f"{name}: {r.problem}")
