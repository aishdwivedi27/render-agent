r"""Compare a finished readout table with a reference first-pass table.

The reference is the first readout the Claude project wrote for a drawing: the test of how well the agent
makes its own first table.

    bash:        python scripts/compare_readout.py <plan/dwelling folder> <reference.md> [--out FILE]
    PowerShell:  python scripts\compare_readout.py <plan\dwelling folder> <reference.md> [--out FILE]

The folder is a job's `plan/<dwelling>/` (it holds `readout.json` and `code_reading.json`). The reference is
a `### N. Title` / Interpretation / Confidence file such as fixture_03's first-pass reference table.

Every reference item is looked for in the table: rooms by name (and size), doors and openings by the rooms
they join and their width, windows by code, robes and recesses by room. For each one found the report says
whether the size agrees and whether the code's own confidence (Green / not Green) agrees with the
reference's. Items the table lists that the reference does not are counted as extras. Confidence is judged
from the code's own reading, not the table's, because a table with a second (model) reader turns every
item the model did not repeat Orange. Matching is by exact room phrase ("bed 3", never just "bed") and the
best overall assignment, so one table row is never claimed by two reference items (rooms excepted: an
open-plan room answers for each label in it). No model is called. Writes markdown (default:
docs/parity/<fixture>_readout.md).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from render_agent.plan.trace_rules import TraceRules, load_trace_rules  # noqa: E402

NUM = re.compile(r"\b(\d{3,4})\b")
CODE = re.compile(r"\b\d{2}-\d{2}\b")
DIMS = re.compile(r"(\d{3,5})\s*[×x]\s*(\d{3,5})")
NOISE = re.compile(r"“?\d{4} × \d{3,4}”?|x≈\d+(?:-\d+)?|y≈\d+")  # label sizes and pixel positions, not widths
WIDTH = re.compile(r"(\d{3,4}) mm wide")
WIDTH_OVER = (-40, 120)  # a door's gap is its written leaf size plus this much
AREA_TOL = 0.25  # a reference room size is an internal clear size, so areas agree within this share
GENERIC = {
    "room",
    "door",
    "opening",
    "window",
    "wall",
    "space",
    "the",
    "and",
    "left",
    "right",
    "top",
    "bottom",
}
UNLABELLED_FOR = (
    "hall",
    "wc",
    "vanity",
    "basin",
    "linen",
    "entry",
    "corridor",
)  # rooms the plan leaves unlabelled


def _reference_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "compare_reference", ROOT / "scripts" / "compare_reference.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class Result:
    item: Any
    category: str
    found: str = "NOT FOUND"
    row: int | None = None
    detail: str = ""
    code_conf: str | None = None  # the code's own confidence: Green / Orange / None (not read by code)
    notes: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.found != "NOT FOUND"

    @property
    def agrees(self) -> bool | None:
        if not self.matched or self.code_conf is None:
            return None
        return (self.code_conf == "Green") == (self.item.confidence == "Green")


def _category(it: Any) -> str:
    title = it.title.lower()
    if title.startswith("scale"):
        return "scale"
    if "wall band" in title or "wall thickness" in title:
        return "walls"
    if CODE.search(it.interpretation) and any(w in title for w in ("window", "awning", "slider")):
        return "window"
    if any(w in title for w in ("door", "opening", "↔", "garage front", "west edge")):
        return "opening"
    if "pocket" in title or ("robe" in title and "walk" not in title):
        return "robe"
    if "shower recess" in title:
        return "recess"
    return "room"


def _phrases(rules: TraceRules, text: str) -> set[str]:
    """Room phrases in a text: names expanded (WIP -> walk-in pantry), a number kept with its word (bed 3)."""
    out = text.lower().replace("walk-in", "walkin")
    for short, full in rules.rooms.expand.items():
        out = re.sub(rf"\b{short.lower()}\b", full.lower().replace("walk-in", "walkin"), out)
    return {p.strip() for p in re.findall(r"[a-z]{3,}(?: \d)?", out) if p.split()[0] not in GENERIC}


def _area(reading: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?) m²", reading.split("Claude:")[0])
    return float(m.group(1)) if m else None


def _code_conf_of(row: dict[str, Any], code: dict[str, Any]) -> str | None:
    rid = row["id"].split("_", 1)[1] if "_" in row["id"] else ""
    for coll in ("openings", "recesses"):
        for item in code[coll]:
            if item["id"] == rid:
                return "Green" if item["confidence"] == "clear" else "Orange"
    for room in code["rooms"]:
        if room["id"] == rid:
            # a zone of an open space is always asked ("where does it end?"), so it is not Green
            return "Green" if room["name"] and not room.get("shares_with") else "Orange"
    return None


def compare(folder: Path, reference: Path) -> tuple[list[Result], list[dict[str, Any]], dict[str, Any]]:
    ref = _reference_module().parse_reference(reference.read_text(encoding="utf-8", errors="replace"))
    table = json.loads((folder / "readout.json").read_text(encoding="utf-8"))
    code = json.loads((folder / "code_reading.json").read_text(encoding="utf-8"))
    rules = load_trace_rules(ROOT / "private" / "knowledge")
    rows: list[dict[str, Any]] = table["rows"]
    number = {id(r): i + 1 for i, r in enumerate(rows)}
    results = [Result(it, _category(it)) for it in ref.items]
    used: set[int] = set()  # table rows claimed by a door, window or pocket (rooms may be shared)

    def take(res: Result, row: dict[str, Any], detail: str = "") -> None:
        res.row, res.found, res.detail = number[id(row)], row["item"], detail
        res.code_conf = _code_conf_of(row, code)

    for res in results:
        it = res.item
        if res.category == "scale":
            if row := next((r for r in rows if r["category"] == "scale"), None):
                take(res, row, row["reading"])
                res.code_conf = "Green"
        elif res.category == "walls":
            res.found, res.row = "overlay check", 0
            res.detail = f"{len(code['wall_rows'])} wall difference row(s) from the overlay check"
            res.code_conf = "Green" if not code["wall_rows"] else "Orange"
        elif res.category == "room":
            want = _phrases(rules, it.title)
            named = [r for r in rows if r["category"] == "room" and want & _phrases(rules, r["item"])]
            alias = [
                r
                for r in rows
                if r["category"] == "room"
                and r["item"].lower().startswith("unlabelled")
                and any(k in it.title.lower() for k in UNLABELLED_FOR)
            ]
            # a room the code read by its label comes before one only a model read, then unlabelled space
            pool = sorted(named, key=lambda r: (_code_conf_of(r, code) is None, number[id(r)])) or alias
            if not pool:
                continue
            row = pool[0]
            take(res, row)
            res.notes.append("name not read by code (unlabelled space)") if row in alias else None
            dims = DIMS.search(it.interpretation[:40])
            got = _area(row["reading"])
            res.detail = f"{got:.1f} m²" if got else ""
            if dims and got and it.confidence != "Red":
                ref_area = int(dims.group(1)) * int(dims.group(2)) / 1e6
                res.detail += f" vs {ref_area:.1f} m² from the reference sizes"
                if "," in row["item"]:
                    res.notes.append("one open-plan room in the code reading (the name lists several labels)")
                elif abs(got / ref_area - 1) > AREA_TOL:
                    res.notes.append(f"area {got / ref_area:.0%} of the reference's")
        elif res.category in ("robe", "recess"):
            text = (it.title + " " + it.interpretation).lower()
            fits = []
            for row in rows:
                if row["category"] != "recess":
                    continue
                room = row["item"].lower().split(" in ")[-1].strip()
                if room in text and re.search(rf"\b{re.escape(room)}\b", text):
                    fits.append(row)
            for row in fits[: 2 if it.title.lower().startswith("two ") else 1]:
                if res.matched:
                    res.notes.append(f"also {row['item']} (#{number[id(row)]})")
                else:
                    take(res, row, row["reading"])
                used.add(id(row))

    # doors, openings and windows: score every pair, then assign the best pairs first
    pairs: list[tuple[int, int, dict[str, Any]]] = []
    for ri, res in enumerate(results):
        if res.category not in ("opening", "window"):
            continue
        it = res.item
        code_m = CODE.search(it.interpretation)
        leaf = next(
            (int(n) for n in NUM.findall(re.sub(NOISE, "", it.interpretation)) if 500 <= int(n) <= 2600),
            None,
        )
        rooms_ref = _phrases(rules, it.title + " " + it.interpretation)
        for row in rows:
            if row["category"] not in ("connection", "window") or id(row) in used:
                continue
            reading, score = row["reading"], 0
            if code_m:
                score += 3 if code_m.group(0) in reading else 0
            elif CODE.search(reading) and res.category == "opening" and "slider" not in it.title.lower():
                continue  # a window is not a door
            w = WIDTH.search(reading)
            if leaf and w and WIDTH_OVER[0] <= int(w.group(1)) - leaf <= WIDTH_OVER[1]:
                score += 2
            phrases = _phrases(rules, row["item"])
            if rooms_ref & phrases:
                score += 2
            elif "unlabelled" in row["item"].lower() and any(k in it.title.lower() for k in UNLABELLED_FOR):
                score += 1
            need = 3 if code_m else (3 if leaf else 2)
            if score >= need and (not code_m or score >= 3):
                pairs.append((score, ri, row))
    taken: set[int] = set()
    for _score, ri, row in sorted(pairs, key=lambda p: (-p[0], p[1], number[id(p[2])])):
        if ri in taken or id(row) in used:
            continue
        taken.add(ri)
        used.add(id(row))
        res = results[ri]
        take(res, row, row["reading"])
        leaf_note = WIDTH.search(row["reading"])
        if leaf_note and (m := re.search(r"\b(\d{3,4})\b", re.sub(NOISE, "", res.item.interpretation))):
            res.notes.append(f"gap {leaf_note.group(1)}, reference says {m.group(1)}")
    extras = [r for r in rows if r["category"] in ("connection", "window", "recess") and id(r) not in used]
    return results, extras, {"table": table, "ref": ref, "rooms": rows}


def report(results: list[Result], extras: list[dict[str, Any]], meta: dict[str, Any], name: str) -> str:
    ref, table = meta["ref"], meta["table"]
    by: dict[str, list[Result]] = {}
    for r in results:
        by.setdefault(r.category, []).append(r)
    lines = [
        f"# Readout table vs reference first pass: {name}",
        "",
        f"Reference: {len(ref.items)} items, {dict(ref.stated)}. Table: {len(table['rows'])} rows "
        f"({table['summary']}), readers: {', '.join(['Code', *table['models']])}.",
        "",
        "| Kind of item | Reference | Found in the table | Not found | Confidence agrees |",
        "|---|---|---|---|---|",
    ]
    labels = {
        "scale": "Scale",
        "walls": "Wall trace",
        "room": "Rooms",
        "opening": "Doors and openings",
        "window": "Windows and sliders (by code)",
        "robe": "Robes and pockets",
        "recess": "Shower recesses",
    }
    tot = [0, 0, 0, 0]
    for key, label in labels.items():
        group = by.get(key, [])
        if not group:
            continue
        judged = [r for r in group if r.agrees is not None]
        agree = sum(1 for r in judged if r.agrees)
        found = sum(1 for r in group if r.matched)
        lines.append(
            f"| {label} | {len(group)} | {found} | {len(group) - found} | {agree} of {len(judged)} |"
        )
        tot = [tot[0] + len(group), tot[1] + found, tot[2] + len(group) - found, tot[3] + agree]
    lines.append(f"| **All** | **{tot[0]}** | **{tot[1]}** | **{tot[2]}** | |")
    lines += [
        "",
        f"Table items the reference does not list (doors, windows, pockets): {len(extras)}.",
        "",
        "## Reference item by item",
        "",
        "| # | Reference item | Ref | Found in table (row) | Size / reading | Code's own | Agrees | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        agrees = {True: "yes", False: "**no**", None: ""}[r.agrees]
        row = f"{r.found} (#{r.row})" if r.matched and r.row else (r.found if r.matched else "**not found**")
        lines.append(
            f"| {r.item.number} | {r.item.title} | {r.item.confidence} | {row} | {r.detail[:70]} | "
            f"{r.code_conf or ''} | {agrees} | {'; '.join(r.notes)} |"
        )
    lines += [
        "",
        "## Extras: in the table, not in the reference",
        "",
        "| Row | Item | Reading |",
        "|---|---|---|",
    ]
    rows = table["rows"]
    for r in extras:
        lines.append(f"| {rows.index(r) + 1} | {r['item']} | {r['reading'][:80]} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("folder", type=Path)
    p.add_argument("reference", type=Path)
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    name = a.reference.parent.parent.name
    results, extras, meta = compare(a.folder, a.reference)
    out = a.out or ROOT / "docs" / "parity" / f"{name}_readout.md"
    out.write_text(report(results, extras, meta, name), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
