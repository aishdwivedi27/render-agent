"""Compare the code's reading of a floor plan with a reference table (fixtures with an `output/` folder).

    bash:        python scripts/compare_reference.py fixture4a fixture4b
    PowerShell:  python scripts\\compare_reference.py fixture4a fixture4b

A reference fixture holds the drawing PDF and, in `output/`, the table a person approved
(`*interpretation*.md`), with its overlay picture (`.png`, `.svg`). This script reads the PDF with the
trace code (no model, no cost), counts what the table lists (windows by code, hinged doors, cavity sliders
or arches, open walkways, garage door, robes, named rooms) against what the code found, and writes
`docs/parity/<fixture>.md` and `docs/parity/<fixture>_code_overlay.png` (the code's reading drawn over the
walls, to hold next to the reference picture).
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from render_agent.plan import code_reading, vector_walls  # noqa: E402
from render_agent.plan.source import PdfSource, page_words  # noqa: E402
from render_agent.plan.trace_rules import TraceRules, load_trace_rules  # noqa: E402
from render_agent.util.fs import atomic_write_text  # noqa: E402

FIXTURES = ROOT / "private" / "fixtures"
OUT = ROOT / "docs" / "parity"
CODE = re.compile(r"\b\d{2}-\d{2}\b")
COLOURS = {
    "hinged_door": (220, 0, 0),
    "window": (0, 160, 0),
    "arch": (255, 140, 0),
    "open_walkway": (255, 140, 0),
    "cavity_slider": (150, 0, 200),
    "sliding_door": (0, 160, 160),
    "garage_door": (90, 90, 90),
}


@dataclass
class Item:
    number: int
    title: str
    interpretation: str
    confidence: str
    evidence: str = ""


@dataclass
class Reference:
    items: list[Item]
    stated_total: int | None
    stated: Counter[str]


def parse_reference(md: str) -> Reference:
    """The numbered items of an interpretation table, and the totals its summary states."""
    items = []
    for m in re.finditer(r"^### (\d+)\. (.*)\n((?:- .*\n?)+)", md, re.M):
        body = m.group(3)
        interp = re.search(r"\*\*Interpretation:\*\* (.*)", body)
        conf = re.search(r"\*\*Confidence:\*\* (\w+)", body)
        evid = re.search(r"\*\*Drawing evidence:\*\* (.*)", body)
        items.append(
            Item(
                int(m.group(1)),
                m.group(2).strip(),
                interp.group(1) if interp else "",
                conf.group(1) if conf else "",
                evid.group(1) if evid else "",
            )
        )
    total = re.search(r"Total recorded items: (\d+)", md)
    stated = Counter()
    for key in ("Green", "Orange", "Red"):
        found = re.search(rf"- {key} \([^)]*\): (\d+)", md)
        if found:
            stated[key] = int(found.group(1))
    return Reference(items, int(total.group(1)) if total else None, stated)


def expected(ref: Reference) -> dict[str, Any]:
    """What the table lists, by the same categories the code reads."""
    codes: Counter[str] = Counter()
    hinged = cavity_arch = walkways = garage = robes = named_robes = 0
    for it in ref.items:
        text = it.interpretation.lower()
        ev = it.evidence.lower()
        window_row = "window" in it.title.lower() or "sliding" in text or bool(CODE.match(it.interpretation))
        if window_row and (m := CODE.search(it.interpretation)):
            codes[m.group(0)] += 1
        if "hinged" in text or ev.startswith(("swing arc", "arc")):  # a table may only show the arc
            hinged += 1
        if "cavity" in text or " arch" in text or text.startswith("arch") or "dashed" in ev:
            cavity_arch += 1
        if "open walkway" in text:
            walkways += 1
        if (
            "garage" in it.title.lower()
            and ("panel" in text or "opening" in text or "panel lift" in ev)
            or "panel-lift" in text
        ):
            garage += 1
        title = it.title.lower()
        if "robe" in title and "walk-in" not in title and "wir" not in title:
            robes += 1
        elif "robe" in text and "walk-in" not in title and "wir" not in title:
            # named inside its room's row ("3000 x 3000 plus robe pocket"); "Two ... pockets" is two robes
            named_robes += 2 if title.startswith("two ") else 1
    return {
        "codes": codes,
        "hinged": hinged,
        "cavity_arch": cavity_arch,
        "walkways": walkways,
        "garage": garage,
        "robes": robes or named_robes,  # robe rows, else the robes named inside room rows
    }


def found(reading: dict[str, Any]) -> dict[str, Any]:
    """The same categories from the code's reading."""
    kinds = Counter(o["kind"] for o in reading["openings"])
    codes: Counter[str] = Counter()
    for o in reading["openings"]:
        if o["code"]:
            codes[o["code"].split()[0]] += 1
    return {
        "codes": codes,
        "hinged": kinds["hinged_door"],
        "cavity_arch": kinds["cavity_slider"] + kinds["arch"],
        "walkways": kinds["open_walkway"],
        "garage": kinds["garage_door"],
        "robes": sum(1 for r in reading["recesses"] if r["kind"] == "robe"),
    }


PAGES = {"fixture_03": 2}  # the floor plan sheet where it is not the first page of the PDF


def read_fixture(name: str, rules: TraceRules) -> tuple[vector_walls.VectorPage, code_reading.CodeReading]:
    pdf = next(
        iter(sorted((FIXTURES / name).glob("*.pdf")) or sorted((FIXTURES / name / "input").glob("*.pdf")))
    )
    page = PAGES.get(name, 1)
    vp = vector_walls.extract(pdf, page, 100, rules.walls)
    with PdfSource(pdf, page, 100, rules.walls) as source:
        return vp, code_reading.read_page(vp, source, page_words(pdf, page, 100), rules)


def draw(vp: vector_walls.VectorPage, reading: code_reading.CodeReading) -> Image.Image:
    px = vp.px_mm
    img = Image.frombytes("L", vp.size, vp.raster).convert("RGB")
    d = ImageDraw.Draw(img)
    for room in reading.rooms:
        d.polygon([(x / px, y / px) for x, y in room.outline], outline=(0, 90, 200), width=4)
    for op in reading.openings:
        f = op.gap.frame
        d.rectangle(
            [f[0] / px, f[1] / px, f[2] / px, f[3] / px], outline=COLOURS.get(op.kind, (0, 0, 0)), width=5
        )
    for rec in reading.recesses:
        f = rec.rect
        d.rectangle([f[0] / px, f[1] / px, f[2] / px, f[3] / px], outline=(255, 0, 255), width=5)
    xs = [w[0] for w in vp.walls] + [w[2] for w in vp.walls]
    ys = [w[1] for w in vp.walls] + [w[3] for w in vp.walls]
    box = [min(xs) / px - 30, min(ys) / px - 30, max(xs) / px + 30, max(ys) / px + 30]
    out = img.crop([int(v) for v in box])
    out.thumbnail((1400, 1800))
    return out


def compare(name: str, rules: TraceRules) -> dict[str, Any]:
    folder = FIXTURES / name / "output"  # the table, and any supplement for items its file left out
    ref = parse_reference(
        "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in sorted(folder.glob("*.md")))
    )
    vp, reading = read_fixture(name, rules)
    page = reading.as_dict()
    return {
        "name": name,
        "reference": ref,
        "expected": expected(ref),
        "found": found(page),
        "page": page,
        "picture": draw(vp, reading),
    }


def _row(label: str, want: int, got: int) -> str:
    return f"| {label} | {want} | {got} | {min(want, got)} | {max(want - got, 0)} | {max(got - want, 0)} |"


def report(result: dict[str, Any]) -> str:
    ref: Reference = result["reference"]
    want, got = result["expected"], result["found"]
    lines = [
        f"# Reference table vs code reading: {result['name']}",
        "",
        f"Reference table: {len(ref.items)} items parsed"
        + (f", its summary states {ref.stated_total}" if ref.stated_total is not None else "")
        + (
            " (**the table file is incomplete**)"
            if ref.stated_total and ref.stated_total != len(ref.items)
            else ""
        )
        + ".",
        f"Reference confidence (stated): {dict(ref.stated)}",
        "",
        "| Category | Reference | Code | Matched | Missing | Extra |",
        "|---|---|---|---|---|---|",
    ]
    for key, label in (
        ("hinged", "Hinged doors"),
        ("cavity_arch", "Cavity sliders or arches"),
        ("walkways", "Open walkways"),
        ("garage", "Garage door"),
        ("robes", "Robes"),
    ):
        lines.append(_row(label, want[key], got[key]))
    lines.append(
        _row("Windows and sliding doors (by code)", sum(want["codes"].values()), sum(got["codes"].values()))
    )
    lines += ["", "## Codes", "", "| Code | Reference | Code reading |", "|---|---|---|"]
    for code in sorted(set(want["codes"]) | set(got["codes"])):
        lines.append(f"| {code} | {want['codes'][code]} | {got['codes'][code]} |")
    rooms = result["page"]["rooms"]
    lines += [
        "",
        "## Robes found",
        "",
        "| Room | Depth x length (mm) | Read from |",
        "|---|---|---|",
        *[
            f"| {r['room_name'] or '(unlabelled)'} | {r['depth_mm']} x {r['length_mm']} | {r['source']} |"
            for r in result["page"]["recesses"]
            if r["kind"] == "robe"
        ],
    ]
    lines += [
        "",
        "## Rooms read by code",
        "",
        "| Name | m² |",
        "|---|---|",
        *[f"| {r['name'] or '(unlabelled)'} | {r['area_m2']} |" for r in rooms],
        "",
        f"Connections read: {len(result['page']['connections'])}; wall rows from the overlay check: "
        f"{len(result['page']['wall_rows'])}.",
        "",
        f"Picture: `{result['name']}_code_overlay.png` (the code's reading; blue rooms, red doors, green "
        "windows, orange arches, purple recesses). Hold it next to the reference picture in "
        f"`private/fixtures/{result['name']}/output/`.",
    ]
    return "\n".join(lines) + "\n"


def main(names: list[str]) -> None:
    rules = load_trace_rules(ROOT / "private" / "knowledge")
    OUT.mkdir(parents=True, exist_ok=True)
    for name in names:
        result = compare(name, rules)
        atomic_write_text(OUT / f"{name}.md", report(result))
        result["picture"].save(OUT / f"{name}_code_overlay.png")
        print(f"wrote docs/parity/{name}.md")


if __name__ == "__main__":
    main(sys.argv[1:] or ["fixture4a", "fixture4b"])
