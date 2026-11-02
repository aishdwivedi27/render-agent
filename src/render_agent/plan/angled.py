"""Walls drawn off square (PLAN_VISUALS_SPEC section 3a; local, no model, no cost).

The wall trace is a set of axis-aligned rectangles, so a wall drawn at an angle (a bay, a splayed corner)
comes out as a staircase of very short steps along one straight line. The steps are found, fitted with
one line, and reported with their length and angle. Code never reshapes the traced walls for them; each
becomes an Orange WALL row, so the designer sees the plan has an angled wall the trace cannot follow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from render_agent.plan import vector_walls
from render_agent.plan.trace_rules import TraceRules

Rect = tuple[float, float, float, float]
ORANGE = "orange"


@dataclass
class Angled:
    a: tuple[float, float]
    b: tuple[float, float]
    length_mm: float
    angle_deg: float  # how far the wall is from the nearest axis, 0 to 45
    box: Rect


Line = tuple[tuple[float, float], tuple[float, float], list[float], float]


def _line(pts: list[tuple[float, float]]) -> Line:
    """Least-squares line through points: (start, end, each point's distance, angle from the nearest axis)."""
    n = len(pts)
    mx, my = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)  # direction of the principal axis
    ux, uy = math.cos(theta), math.sin(theta)
    along = [(p[0] - mx) * ux + (p[1] - my) * uy for p in pts]
    across = [abs(-(p[0] - mx) * uy + (p[1] - my) * ux) for p in pts]
    deg = abs(math.degrees(theta)) % 90
    start = (mx + min(along) * ux, my + min(along) * uy)
    end = (mx + max(along) * ux, my + max(along) * uy)
    return start, end, across, min(deg, 90 - deg)


def _fit(rects: list[Rect], limit: float, min_points: int) -> tuple[Line, list[Rect]] | None:
    """The line through the rectangles' centres, dropping the rows where the staircase meets another wall (the
    row there runs on into that wall, so its centre sits off the line) until the rest agree."""
    keep = list(rects)
    for _ in range(4):
        pts = [((r[0] + r[2]) / 2, (r[1] + r[3]) / 2) for r in keep]
        line = _line(pts)
        good = [r for r, d in zip(keep, line[2], strict=True) if d <= limit]
        if len(good) == len(keep):
            return (line, keep) if len(keep) >= min_points else None
        if len(good) < min_points:
            return None
        keep = good
    return None


def find(vp: vector_walls.VectorPage, rules: TraceRules) -> list[Angled]:
    """Staircases of short wall steps lying on one straight line that is not square to the page."""
    a = rules.angled
    px = vp.px_mm
    thin = [r for r in vp.walls if min(r[2] - r[0], r[3] - r[1]) <= a.step_max_mm]
    if len(thin) < a.min_steps:
        return []
    roots = vector_walls._roots(thin, px * 1.5)
    groups: dict[int, list[Rect]] = {}
    for r, root in zip(thin, roots, strict=True):
        groups.setdefault(root, []).append(r)
    found: list[Angled] = []
    for rects in groups.values():
        if len(rects) < a.min_steps:
            continue
        fit = _fit(rects, a.line_tol_mm, a.min_steps)
        if fit is None:
            continue
        (start, end, _across, off_axis), steps = fit
        length = math.dist(start, end)
        # a staircase advances on both axes; the corner of an L, or a column of stacked pieces, does not
        advances = min(
            len({round((r[0] + r[2]) / 2 / px) for r in steps}),
            len({round((r[1] + r[3]) / 2 / px) for r in steps}),
        )
        if length < a.min_run_mm or off_axis < a.min_angle_deg or advances < a.min_steps:
            continue
        box = (
            min(r[0] for r in steps),
            min(r[1] for r in steps),
            max(r[2] for r in steps),
            max(r[3] for r in steps),
        )
        found.append(Angled(start, end, length, off_axis, box))
    found.sort(key=lambda w: -w.length_mm)
    return found[: a.max_rows]


def rows(found: list[Angled], rules: TraceRules) -> list[dict[str, Any]]:
    """WALL rows for code_reading.json (same shape as the overlay check's rows)."""
    a = rules.angled
    return [
        {
            "id": f"wall_angled_{round((w.box[0] + w.box[2]) / 20)}_{round((w.box[1] + w.box[3]) / 20)}",
            "kind": "angled",
            "box": [round(v, 1) for v in w.box],
            "confidence": ORANGE,
            "evidence": a.evidence.format(len=round(w.length_mm), angle=round(w.angle_deg)),
            "question": a.question,
        }
        for w in found
    ]
