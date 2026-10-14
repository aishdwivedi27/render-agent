"""Doctored test images for the drawing check (docs/MILESTONES.md M3).

A synthetic single-storey street elevation (line drawing) is the reference; a coloured "render" of
it is the base image, and each variant changes one thing. Variants in MUST_FAIL break a documented
fact; variants in MUST_PASS change only what the spec tolerates.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

W, H = 1200, 800
GROUND = 640
WALL = (60, 360, 1140, GROUND)  # single storey
WINDOWS = [(140, 430, 300, 560), (380, 430, 500, 560)]
DOOR = (580, 420, 660, GROUND)
GARAGE = (760, 440, 1080, GROUND)

MUST_FAIL = ["extra_window", "missing_door", "door_colour", "second_storey", "swapped_order"]
MUST_PASS = ["larger_openings", "panel_count"]
VARIANTS = ["base", *MUST_FAIL, *MUST_PASS]

FACTS = [
    "Single storey",
    "Hip roof, dark grey metal",
    "Street elevation: two windows left of the entry, one timber entry door, one double garage door",
    "Entry door: natural timber",
    "Garage door: dark grey panel lift",
    "Walls: white render",
]


def _scale(box: tuple[int, int, int, int], f: float) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * f, (y1 - y0) * f
    return (int(cx - w / 2), int(min(cy + h / 2, GROUND) - h), int(cx + w / 2), int(min(cy + h / 2, GROUND)))


def _house(draw: ImageDraw.ImageDraw, variant: str, line: bool) -> None:
    stroke = (0, 0, 0)
    wall_fill = None if line else (245, 245, 240)
    roof_fill = None if line else (70, 72, 76)
    top = WALL[1]
    if variant == "second_storey":
        draw.rectangle((WALL[0], top - 260, WALL[2], top), fill=wall_fill, outline=stroke, width=3)
        for x in (160, 420, 700, 940):
            draw.rectangle(
                (x, top - 200, x + 120, top - 90), fill=None if line else (30, 30, 30), outline=stroke
            )
        top -= 260
    draw.polygon(
        [(WALL[0] - 40, top), (300, top - 180), (900, top - 180), (WALL[2] + 40, top)],
        fill=roof_fill,
        outline=stroke,
    )
    draw.rectangle(WALL, fill=wall_fill, outline=stroke, width=3)
    f = 1.1 if variant == "larger_openings" else 1.0
    windows = list(WINDOWS)
    door = DOOR
    if variant == "swapped_order":  # entry door moved to the far left, a window where it was
        door = (WINDOWS[0][0] + 40, DOOR[1], WINDOWS[0][0] + 120, DOOR[3])
        windows = [(560, 430, 680, 560), WINDOWS[1]]
    if variant == "extra_window":
        windows.append((690, 450, 740, 540))  # between the door and the garage
    for wbox in windows:
        b = _scale(wbox, f)
        draw.rectangle(b, fill=None if line else (40, 60, 80), outline=stroke, width=6)
        draw.line(((b[0] + b[2]) // 2, b[1], (b[0] + b[2]) // 2, b[3]), fill=stroke, width=3)
    if variant != "missing_door":
        door_fill = (200, 30, 30) if variant == "door_colour" else (150, 100, 55)
        draw.rectangle(_scale(door, f), fill=None if line else door_fill, outline=stroke, width=4)
    g = _scale(GARAGE, f)
    draw.rectangle(g, fill=None if line else (80, 82, 86), outline=stroke, width=4)
    panels = 8 if variant == "panel_count" else 4
    for i in range(1, panels):
        y = g[1] + (g[3] - g[1]) * i // panels
        draw.line((g[0], y, g[2], y), fill=(0, 0, 0) if line else (110, 112, 116), width=2)
    draw.line((0, GROUND, W, GROUND), fill=stroke, width=3)


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def reference_png() -> bytes:
    im = Image.new("RGB", (W, H), "white")
    _house(ImageDraw.Draw(im), "base", line=True)
    return _png(im)


def render_png(variant: str) -> bytes:
    if variant not in VARIANTS:
        raise ValueError(variant)
    im = Image.new("RGB", (W, H), (180, 210, 235))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, GROUND, W, H), fill=(110, 150, 90))
    _house(draw, variant, line=False)
    return _png(im)


def make_check_job(job_dir: Path, render: bytes, render_id: str = "T-D1-FRONT") -> dict[str, Any]:
    """The smallest set of artifacts stage 8 reads, for one render of one dwelling."""
    for sub in ("sheets", "references", "prompts", "checks", "output/clean"):
        (job_dir / sub).mkdir(parents=True, exist_ok=True)
    (job_dir / "references" / "E1.png").write_bytes(reference_png())
    (job_dir / "output" / "clean" / f"{render_id}_v1.png").write_bytes(render)
    (job_dir / "output" / f"{render_id}_v1.png").write_bytes(render)
    view = {
        "view_id": "E1",
        "label": "ELEVATION 1",
        "view_kind": "elevation",
        "facing": "front_street",
        "dwellings_shown": ["ALL"],
        "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        "description": "street elevation",
    }
    (job_dir / "sheets" / "sheet-1.json").write_text(json.dumps({"views": [view]}), encoding="utf-8")
    spec = {
        "ELEVATIONS": [
            {
                "elevation": "ELEVATION 1",
                "openings": [
                    "Window W1, left of entry",
                    "Window W2, left of entry",
                    "Entry door D1, natural timber",
                    "Garage door GD1, double, panel lift, dark grey",
                ],
                "roof_information": ["Hip roof, dark grey metal"],
            }
        ]
    }
    (job_dir / "house_specification.json").write_text(json.dumps(spec), encoding="utf-8")
    req = {
        "render_id": render_id,
        "location": "street front",
        "architectural_facts": FACTS,
        "materials_and_colours": [],
    }
    (job_dir / "checklist.json").write_text(json.dumps({"RENDER_REQUIREMENTS": [req]}), encoding="utf-8")
    (job_dir / "prompts" / f"{render_id}.txt").write_text("FIXED IDENTITY: D1\n", encoding="utf-8")
    entry = {
        "render_id": render_id,
        "dwelling_id": "D1",
        "view_type": "Front day",
        "readiness": "READY",
        "prompt_file": f"prompts/{render_id}.txt",
        "references": ["references/E1.png"],
        "reference_view_ids": ["E1"],
        "facade_left_to_right": ["window", "window", "entry door", "double garage door"],
        "facade_source": "ELEVATION 1 (D1)",
        "aspect": "landscape",
    }
    identity = "FIXED IDENTITY: D1\n- Storeys: Single storey\n- Roof: Hip, dark grey metal\n" + "\n".join(
        f"- {f}" for f in FACTS[3:]
    )
    (job_dir / "prompts" / "index.json").write_text(
        json.dumps({"renders": [entry], "identity_blocks": {"D1": identity}}), encoding="utf-8"
    )
    version = {
        "version": 1,
        "file": f"output/{render_id}_v1.png",
        "clean_file": f"output/clean/{render_id}_v1.png",
        "provider": "mock",
        "model": "mock",
        "kind": "first",
        "attempts": 1,
        "fix_instructions": [],
    }
    index = {"renders": {render_id: {"status": "rendered", "dwelling_id": "D1", "versions": [version]}}}
    (job_dir / "output" / "render_index.json").write_text(json.dumps(index), encoding="utf-8")
    return entry
