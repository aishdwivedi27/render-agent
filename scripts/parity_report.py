"""Parity of the code's floor plan reading with the answer key (PV3 fix round, step J).

    bash:        python scripts/parity_report.py [--key PATH]
    PowerShell:  python scripts\\parity_report.py [--key PATH]

Reads fixture_03's floor plan with the trace code (no model, no cost), aligns it to the key by the best
wall-to-wall offset, and matches it to the key within the key's tolerance (100 mm): every wall face on a
traced wall, every room (name and corners), every opening (kind, host wall, position), every window code,
every robe.
Writes docs/parity/fixture_03_render_key.md: matched / missing / extra per kind, and the key beside the code's
reading. Without --key it uses private/fixtures/fixture_03/plan_answer_key.yaml if it exists, else the draft
(and says so at the top of the report).

NOTE: this key is the geometry of the external / internal photorealistic render (from gen_data_reference.py),
not the 2D plan reading. The 2D plan reference for fixture_03 is the owner's first-pass table in
private/fixtures/fixture_03/output/, compared by scripts/compare_reference.py (docs/parity/fixture_03.md).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from render_agent.plan import code_reading, vector_walls  # noqa: E402
from render_agent.plan.source import PdfSource, page_words  # noqa: E402
from render_agent.plan.trace_rules import TraceRules, load_trace_rules  # noqa: E402
from render_agent.util.fs import atomic_write_text  # noqa: E402

FIXTURE = ROOT / "private" / "fixtures" / "fixture_03"
KEY = FIXTURE / "plan_answer_key.yaml"
DRAFT = FIXTURE / "plan_answer_key.draft.yaml"
OUT = ROOT / "docs" / "parity"
PAGE, RATIO = 2, 100  # the floor plan sheet of fixture_03 and its scale
SNAP_MM = 25.0  # two edges this close are the same edge when aligning
CANDIDATES = 40  # the longest key faces tried when looking for the offset
ROBE_KINDS = ("robe",)


def load_key(path: Path | None = None) -> tuple[dict[str, Any], bool]:
    """(the key, whether it is only the draft)."""
    path = path or (KEY if KEY.is_file() else DRAFT)
    return yaml.safe_load(path.read_text(encoding="utf-8")), path == DRAFT


def read_code(rules: TraceRules) -> tuple[vector_walls.VectorPage, dict[str, Any]]:
    pdf = next((FIXTURE / "input").glob("*.pdf"))
    vp = vector_walls.extract(pdf, PAGE, RATIO, rules.walls)
    with PdfSource(pdf, PAGE, RATIO, rules.walls) as source:
        reading = code_reading.read_page(vp, source, page_words(pdf, PAGE, RATIO), rules)
    return vp, reading.as_dict()


# --- alignment ---------------------------------------------------------------------------------------------
def _edges(walls: list[tuple[float, float, float, float]], axis: int) -> list[float]:
    """Distinct wall edge coordinates on one axis (0: x, 1: y), merged within SNAP_MM."""
    values = sorted({round(v, 1) for w in walls for v in (w[axis], w[axis + 2])})
    out: list[float] = []
    for v in values:
        if not out or v - out[-1] > SNAP_MM / 2:
            out.append(v)
    return out


def _score(faces: list[tuple[float, float]], edges: list[float], shift: float) -> float:
    """Length of key faces (coordinate, length) that lie on a traced edge after shifting."""
    return sum(n for c, n in faces if any(abs(c + shift - e) <= SNAP_MM for e in edges))


def align(key: dict[str, Any], walls: list[tuple[float, float, float, float]]) -> tuple[float, float]:
    """The (dx, dy) that puts the key on the traced walls: the shift under which most key face length lies
    on a traced wall edge, tried from the longest key faces against every traced edge."""
    out = []
    for axis in (0, 1):
        faces = [
            (f[axis], f[3 - axis] - f[1 - axis])
            for f in key["wall_faces"]
            if f[axis] == f[axis + 2] and f[3 - axis] - f[1 - axis] > 0
        ]
        edges = _edges(walls, axis)
        longest = sorted(faces, key=lambda f: -f[1])[:CANDIDATES]
        shifts = {round(e - c, 1) for c, _ in longest for e in edges}
        out.append(max(shifts, key=lambda s: _score(faces, edges, s), default=0.0))
    return out[0], out[1]


# --- matching ----------------------------------------------------------------------------------------------
def _covered(lo: float, hi: float, spans: list[tuple[float, float]]) -> float:
    """The share of [lo, hi] covered by the union of spans."""
    total, at = 0.0, lo
    for a, b in sorted(spans):
        a, b = max(a, at), min(b, hi)
        if b > a:
            total += b - a
            at = b
    return total / (hi - lo) if hi > lo else 1.0


def _opening_spans(
    key: dict[str, Any], off: tuple[float, float], vertical: bool, line: float, reach: float
) -> list[tuple[float, float]]:
    """The stretches of one wall line taken by the key's openings: a window or door is a gap in the wall, not
    a missing wall."""
    out = []
    for o in key["openings"]:
        if (o["wall_axis"] == "v") != vertical:
            continue
        if abs(o["wall_line_mm"] + (off[0] if vertical else off[1]) - line) > reach:
            continue
        x0, y0, x1, y1 = o["frame"]
        out.append((y0 + off[1], y1 + off[1]) if vertical else (x0 + off[0], x1 + off[0]))
    return out


def _face_cover(
    key: dict[str, Any],
    walls: list[tuple[float, float, float, float]],
    off: tuple[float, float],
    f: list[float],
    reach: float,
) -> float:
    """The share of a key wall face lying on a traced wall edge within `reach`, openings counted as wall."""
    x0, y0, x1, y1 = f[0] + off[0], f[1] + off[1], f[2] + off[0], f[3] + off[1]
    vertical = abs(x1 - x0) < 1
    line = x0 if vertical else y0
    spans = [
        (w[1], w[3]) if vertical else (w[0], w[2])
        for w in walls
        if any(abs(w[a] - line) <= reach for a in ((0, 2) if vertical else (1, 3)))
    ]
    spans += _opening_spans(key, off, vertical, line, reach)
    lo, hi = (y0, y1) if vertical else (x0, x1)
    return _covered(lo, hi, spans)


def walls_check(
    key: dict[str, Any], walls: list[tuple[float, float, float, float]], off: tuple[float, float]
) -> dict[str, Any]:
    """Each key wall face against the traced walls: an edge within tolerance along most of its length. A
    missing face with a wall within 3 x tolerance is a near miss; none at all is a boundary the plan leaves
    open."""
    tol = float(key["tolerance_mm"])
    matched, missing = 0, []
    for f in key["wall_faces"]:
        if _face_cover(key, walls, off, f, tol) >= 0.8:
            matched += 1
        else:
            missing.append(f)
    near = sum(_face_cover(key, walls, off, f, 3 * tol) >= 0.8 for f in missing)
    return {"total": len(key["wall_faces"]), "matched": matched, "missing": missing, "near": near}


def _tokens(text: str) -> set[str]:
    return {t for t in "".join(c if c.isalnum() else " " for c in text.upper()).split() if t}


def _corner_off(poly: list[list[float]], other: list[list[float]], off: tuple[float, float]) -> float:
    """The worst distance from a key corner to the nearest corner of the other outline."""
    worst = 0.0
    for kx, ky in poly:
        d = min(((kx + off[0] - x) ** 2 + (ky + off[1] - y) ** 2) ** 0.5 for x, y in other)
        worst = max(worst, d)
    return worst


def rooms_check(key: dict[str, Any], code: dict[str, Any], off: tuple[float, float]) -> dict[str, Any]:
    tol = float(key["tolerance_mm"])
    matched, off_corners, merged, missing = [], [], [], []
    used: set[str] = set()
    for kr in key["rooms"]:
        names = [kr["name"], *kr.get("aliases", []), *kr.get("labels", [])]
        want = [_tokens(n) for n in names]
        named = [c for c in code["rooms"] if c["name"] and any(w and w <= _tokens(c["name"]) for w in want)]
        best = None
        for c in named:
            d = _corner_off(kr["outline"], c["outline"], off)
            if best is None or d < best[0]:
                best = (d, c)
        if best is None:  # no label to go by (a hall, a WC): the code room whose corners are closest
            for c in code["rooms"]:
                if c["id"] in used or c["name"]:
                    continue
                d = _corner_off(kr["outline"], c["outline"], off)
                if best is None or d < best[0]:
                    best = (d, c)
            if best is not None and best[0] > tol:
                best = None
        if best is None:
            missing.append(kr["name"])
        elif best[0] <= tol:
            matched.append((kr["name"], best[1]["id"], best[0]))
            used.add(best[1]["id"])
        elif len(_tokens(best[1]["name"] or "")) > len(_tokens(kr["name"])):
            merged.append((kr["name"], best[1]["name"], best[0]))  # named, but inside a bigger open-plan room
            used.add(best[1]["id"])
        else:
            off_corners.append((kr["name"], best[1]["name"], best[0]))
            used.add(best[1]["id"])
    extra = [c["name"] or "(unlabelled)" for c in code["rooms"] if c["id"] not in used]
    return {
        "total": len(key["rooms"]),
        "matched": matched,
        "merged": merged,
        "off": off_corners,
        "missing": missing,
        "extra": extra,
    }


def openings_check(key: dict[str, Any], code: dict[str, Any], off: tuple[float, float]) -> dict[str, Any]:
    tol = float(key["tolerance_mm"])
    matched, wrong_kind, missing, bad_code = [], [], [], []
    used: set[str] = set()
    for ko in key["openings"]:
        along = off[0] if ko["wall_axis"] == "h" else off[1]
        across = off[1] if ko["wall_axis"] == "h" else off[0]
        near = [
            (abs(c["position_mm"] - (ko["position_mm"] + along)), c)
            for c in code["openings"]
            if c["id"] not in used
            and c["axis"] == ko["wall_axis"]
            and abs(c["wall_line_mm"] - (ko["wall_line_mm"] + across)) <= tol
            and abs(c["position_mm"] - (ko["position_mm"] + along)) <= tol
        ]
        if not near:
            missing.append(ko)
            continue
        d, c = min(near, key=lambda n: n[0])
        used.add(c["id"])
        if c["kind"] not in ko["accepted_kinds"]:
            wrong_kind.append((ko, c))
            continue
        matched.append((ko, c, d))
        want = ko.get("code")
        if want and (c["code"] or "").split()[:1] != want.split()[:1]:  # "21-09 awn" is code 21-09
            bad_code.append((ko, c))
    extra = [c for c in code["openings"] if c["id"] not in used]
    return {
        "total": len(key["openings"]),
        "matched": matched,
        "wrong_kind": wrong_kind,
        "missing": missing,
        "bad_code": bad_code,
        "extra": extra,
        "codes": sum(1 for ko in key["openings"] if ko.get("code")),
    }


def recesses_check(key: dict[str, Any], code: dict[str, Any], off: tuple[float, float]) -> dict[str, Any]:
    tol = float(key["tolerance_mm"])
    matched, missing, used = [], [], set()
    for kr in key["recesses"]:
        kx, ky = (kr["rect"][0] + kr["rect"][2]) / 2 + off[0], (kr["rect"][1] + kr["rect"][3]) / 2 + off[1]
        near = []
        for c in code["recesses"]:
            if c["id"] in used:
                continue
            cx, cy = (c["rect"][0] + c["rect"][2]) / 2, (c["rect"][1] + c["rect"][3]) / 2
            d = ((kx - cx) ** 2 + (ky - cy) ** 2) ** 0.5
            if d <= 2 * tol:
                near.append((d, c))
        if not near:
            missing.append(kr)
            continue
        d, c = min(near, key=lambda n: n[0])
        used.add(c["id"])
        as_robe = c["kind"] in ROBE_KINDS or (c["kind"] == "other" and c["alternative"] in ROBE_KINDS)
        matched.append((kr, c, d, as_robe))
    extra = [c for c in code["recesses"] if c["id"] not in used]
    return {"total": len(key["recesses"]), "matched": matched, "missing": missing, "extra": extra}


@dataclass
class Parity:
    offset: tuple[float, float]
    walls: dict[str, Any]
    rooms: dict[str, Any]
    openings: dict[str, Any]
    recesses: dict[str, Any]
    problems: list[str] = field(default_factory=list)


def compare(key: dict[str, Any], vp: vector_walls.VectorPage, code: dict[str, Any]) -> Parity:
    off = align(key, vp.walls)
    p = Parity(
        off,
        walls_check(key, vp.walls, off),
        rooms_check(key, code, off),
        openings_check(key, code, off),
        recesses_check(key, code, off),
    )
    if p.walls["missing"]:
        p.problems.append(f"{len(p.walls['missing'])} key wall faces not on a traced wall")
    if p.rooms["missing"] or p.rooms["off"] or p.rooms["merged"]:
        p.problems.append(
            "rooms: some missing, off by more than the tolerance, or merged into an open-plan room"
        )
    o = p.openings
    if o["missing"] or o["wrong_kind"] or o["bad_code"]:
        p.problems.append("openings: some missing, of another kind, or with another window code")
    if p.recesses["missing"] or not all(m[3] for m in p.recesses["matched"]):
        p.problems.append("robes: some missing or not read as a robe")
    return p


# --- report ------------------------------------------------------------------------------------------------
def _pc(n: int, total: int) -> str:
    return f"{n}/{total} ({100 * n / total:.0f}%)" if total else "0/0"


def report(key: dict[str, Any], draft: bool, p: Parity, code: dict[str, Any], rules: TraceRules) -> str:
    o, r, w, rc = p.openings, p.rooms, p.walls, p.recesses
    lines = ["# Parity: code reading vs the answer key, fixture_03", ""]
    if draft:
        lines += [
            "**DRAFT KEY**: not yet checked against the drawing by the owner. Numbers are provisional.",
            "",
        ]
    lines += [
        f"Key tolerance {key['tolerance_mm']} mm. Alignment offset found: dx {p.offset[0]:.0f} mm, "
        f"dy {p.offset[1]:.0f} mm.",
        "",
        "| Kind | Key | Matched | Missing | Other | Extra (code only) |",
        "|---|---|---|---|---|---|",
        f"| Wall faces | {w['total']} | {w['matched']} | {len(w['missing'])} | "
        f"{w['near']} of the missing have a "
        f"wall within {3 * key['tolerance_mm']:.0f} mm | |",
        f"| Rooms (name and corners) | {r['total']} | {len(r['matched'])} | {len(r['missing'])} | "
        f"{len(r['merged'])} merged, {len(r['off'])} off | {len(r['extra'])} |",
        f"| Openings (kind, wall, position) | {o['total']} | {len(o['matched'])} | {len(o['missing'])} | "
        f"{len(o['wrong_kind'])} other kind | {len(o['extra'])} |",
        f"| Window codes | {o['codes']} | {o['codes'] - len(o['bad_code'])} | | "
        f"{len(o['bad_code'])} different | |",
        f"| Robes | {rc['total']} | {sum(1 for m in rc['matched'] if m[3])} | {len(rc['missing'])} | "
        f"{sum(1 for m in rc['matched'] if not m[3])} found, not as a robe | {len(rc['extra'])} |",
        "",
        f"Matched openings {_pc(len(o['matched']), o['total'])}; rooms {_pc(len(r['matched']), r['total'])}; "
        f"robes {_pc(sum(1 for m in rc['matched'] if m[3]), rc['total'])}.",
        "",
    ]
    if p.problems:
        lines += ["## Not matched", "", *[f"- {x}" for x in p.problems], ""]
    if r["missing"] or r["merged"] or r["off"]:
        lines += ["## Rooms", ""]
        lines += [f"- missing: {n}" for n in r["missing"]]
        lines += [f"- merged into {c}: {n} (corners {d:.0f} mm off)" for n, c, d in r["merged"]]
        lines += [f"- {n}: code room {c or '(unlabelled)'} corners {d:.0f} mm off" for n, c, d in r["off"]]
        lines.append("")
    if o["missing"] or o["wrong_kind"] or o["bad_code"]:
        lines += ["## Openings", ""]
        lines += [
            f"- missing {x['id']} {x['kind']} {x.get('code', '')} at {x['position_mm']:.0f}"
            for x in o["missing"]
        ]
        lines += [f"- {k['id']}: key {k['kind']}, code {c['kind']}" for k, c in o["wrong_kind"]]
        lines += [f"- {k['id']}: key code {k['code']}, code {c['code']}" for k, c in o["bad_code"]]
        lines.append("")
    if o["extra"]:
        lines += ["## Extra openings (allowed only as Orange rows)", ""]
        lines += [
            f"- {c['kind']} {c['width_mm']} mm at {c['position_mm']} ({c['evidence']})" for c in o["extra"]
        ]
        lines.append("")
    lines += [
        "## Readout, key beside the code",
        "",
        "| Item | Key | Code reading | Result |",
        "|---|---|---|---|",
    ]
    room_by_key = {m[0]: m for m in r["matched"]}
    code_rooms = {c["id"]: c for c in code["rooms"]}
    for kr in key["rooms"]:
        got = room_by_key.get(kr["name"])
        if got:
            c = code_rooms[got[1]]
            area = f"{c['name'] or '(unlabelled)'}, {c['area_m2']} m²"
            lines.append(f"| {kr['name']} | room | {area} | matched ({got[2]:.0f} mm) |")
            continue
        why = next((f"merged into {c}" for n, c, _ in r["merged"] if n == kr["name"]), None) or next(
            (f"{c or '(unlabelled)'}, corners {d:.0f} mm off" for n, c, d in r["off"] if n == kr["name"]),
            "not found",
        )
        lines.append(f"| {kr['name']} | room | | {why} |")
    for ko in key["openings"]:
        label = f"{ko['kind']}{' ' + ko['code'] if ko.get('code') else ''} {ko['width_mm']:.0f} mm"
        hit = next((m for m in o["matched"] if m[0]["id"] == ko["id"]), None)
        wrong = next((m for m in o["wrong_kind"] if m[0]["id"] == ko["id"]), None)
        if hit:
            c = hit[1]
            got = f"{c['kind']}{' ' + c['code'] if c['code'] else ''} {c['width_mm']} mm"
            lines.append(f"| {ko['id']} | {label} | {got} | matched ({hit[2]:.0f} mm) |")
        elif wrong:
            lines.append(f"| {ko['id']} | {label} | {wrong[1]['kind']} | other kind |")
        else:
            lines.append(f"| {ko['id']} | {label} | | missing |")
    for kr in key["recesses"]:
        hit = next((m for m in rc["matched"] if m[0]["id"] == kr["id"]), None)
        label = f"robe {kr['depth_mm']} x {kr['width_mm']} in {kr['room']}"
        if hit:
            c = hit[1]
            got = f"{c['kind']} {c['depth_mm']} x {c['length_mm']}"
            lines.append(f"| {kr['id']} | {label} | {got} | {'matched' if hit[3] else 'not as a robe'} |")
        else:
            lines.append(f"| {kr['id']} | {label} | | missing |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=Path, default=None)
    args = ap.parse_args(argv)
    rules = load_trace_rules(ROOT / "private" / "knowledge")
    key, draft = load_key(args.key)
    vp, code = read_code(rules)
    result = compare(key, vp, code)
    text = report(key, draft, result, code, rules)
    atomic_write_text(OUT / "fixture_03_render_key.md", text)
    print(f"wrote {(OUT / 'fixture_03_render_key.md').relative_to(ROOT)}")
    print(text.split("## ")[0])


if __name__ == "__main__":
    main(sys.argv[1:])
