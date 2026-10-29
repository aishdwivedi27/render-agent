"""'Indicative Image Only' on images and PDF pages (PLAN_VISUALS_SPEC section 11)."""

from __future__ import annotations

import io
import random

import pymupdf
import pytest
from PIL import Image, ImageDraw

from render_agent.output import disclaimer as d


def _light() -> Image.Image:
    return Image.new("RGB", (1600, 900), (250, 250, 248))


def _dark() -> Image.Image:
    return Image.new("RGB", (1600, 900), (25, 30, 40))


def _busy(left_busy: bool = True) -> Image.Image:
    """Random noise over the whole image, optionally a calm strip along the bottom right."""
    rng = random.Random(7)
    img = Image.new("RGB", (1200, 1200))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(1200 * 1200)])
    if left_busy:
        ImageDraw.Draw(img).rectangle((0, 1000, 1199, 1199), fill=(240, 240, 240))
    return img


@pytest.mark.parametrize("make,light", [(_light, True), (_dark, False)])
def test_plain_backgrounds_get_left_edge_stamp_with_contrast(make, light):
    out, p = d.stamp_image(make())
    assert p.position == "left" and p.light is light
    x0, y0, x1, y1 = p.box
    assert y1 - y0 > x1 - x0  # rotated: taller than wide
    assert abs((y0 + y1) / 2 - out.height / 2) <= 2  # vertically centred
    check = d.check_image(out, p)
    assert check.ok and check.contrast >= d.MIN_CONTRAST


def test_cap_height_follows_the_short_side():
    _, small = d.stamp_image(Image.new("RGB", (800, 600), "white"))
    _, big = d.stamp_image(Image.new("RGB", (4000, 3000), "white"))
    width = lambda p: p.box[2] - p.box[0]  # noqa: E731 (rotated: pill width = text height)
    assert 4 < width(big) / width(small) < 6


def test_busy_left_edge_moves_to_the_clear_corner():
    out, p = d.stamp_image(_busy())
    assert p.position in ("bottom_left", "bottom_right")
    assert d.check_image(out, p).ok


def test_everywhere_busy_still_reaches_contrast():
    rng = random.Random(3)
    img = Image.new("RGB", (900, 900))
    img.putdata([(rng.randrange(256),) * 3 for _ in range(900 * 900)])
    out, p = d.stamp_image(img)
    assert p.alpha > d.DARK_PILL_ALPHA or p.light  # pill made more opaque to keep 4.5:1
    assert d.check_image(out, p).ok


def test_png_round_trip_keeps_the_placement():
    out, p = d.stamp_image(_light())
    reread = Image.open(io.BytesIO(d.png_bytes(out, p)))
    assert d.placement_of(reread) == p
    assert d.check_image(reread).ok


def test_missing_or_faint_disclaimer_is_not_deliverable():
    img = _light()
    assert not d.check_image(img).ok  # no placement at all
    _, p = d.stamp_image(img)
    blank = d.check_image(img, p)  # placement recorded but nothing drawn
    assert not blank.ok and "No disclaimer" in blank.problem
    faint = img.copy()
    ImageDraw.Draw(faint).text((p.box[0] + 2, p.box[1] + 2), "Indicative", fill=(225, 225, 225))
    result = d.check_image(faint, p)
    assert not result.ok and result.contrast < d.MIN_CONTRAST
    with pytest.raises(d.DisclaimerError):
        d.require_ok(result, "plan.png")


def test_jpg_still_passes_after_compression():
    out, p = d.stamp_image(_dark())
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=92)
    assert d.check_image(Image.open(buf), p).ok


def _pdf(path, fills):
    doc = pymupdf.open()
    for fill in fills:
        page = doc.new_page(width=1190, height=842)  # A3 landscape
        page.draw_rect(page.rect, fill=fill, color=None)
        page.insert_text((300, 400), "PLAN", fontsize=40, color=(0.5, 0.5, 0.5))
    doc.save(path)
    return path


def test_every_pdf_page_is_stamped_and_checked(tmp_path):
    src = _pdf(tmp_path / "plans.pdf", [(1, 1, 1), (0.1, 0.1, 0.12), (0.95, 0.94, 0.9)])
    out = d.stamp_pdf(src, tmp_path / "stamped.pdf")
    results = d.check_pdf(out)
    assert len(results) == 3 and all(r.ok for r in results), results
    with pymupdf.open(out) as doc:
        assert all(d._text_size(page) >= d.PDF_MIN_PT for page in doc)


def test_unstamped_pdf_page_fails(tmp_path):
    src = _pdf(tmp_path / "plans.pdf", [(1, 1, 1), (1, 1, 1)])
    stamped = d.stamp_pdf(src, tmp_path / "one.pdf")
    with pymupdf.open(stamped) as doc:
        doc.insert_pdf(pymupdf.open(src), from_page=0, to_page=0)  # an extra page without it
        doc.save(tmp_path / "mixed.pdf")
    results = d.check_pdf(tmp_path / "mixed.pdf")
    assert [r.ok for r in results] == [True, True, False]
    assert "Page 3" in results[2].problem
