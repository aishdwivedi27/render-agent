"""Close-up areas of the plan (PLAN_VISUALS_SPEC section 3a; the model's suggestion is optional).

Rooms and openings that sit tightly together are hard to read at the scale of a whole plan: a WC, a
vanity and a
bathroom with their doors, an ensuite and its walk-in robe, the garage and its door to the house, the
laundry's
entrance and its way outside, the kitchen and pantry, the alfresco and the porch. Each such cluster is cut out
at higher detail, with the traced overlay drawn over the original, and checked like a tile (`verify.py`).

Where to cut comes from three places, all data:

- groups in `tiles.focus.groups`: every room whose name holds one of a group's words, with (for `link`
  groups) the
  small rooms a door joins to them, and the openings in their walls;
- hotspots: items that are not Green and fall in no group's area, clustered by closeness;
- optionally a model (`plan_focus`, one call) that looks at the whole plan with the overlay and names
  areas worth a
  closer look. Code checks every box it returns (inside the plan, at least `min_mm`, at most `max_mm`) and the
  model can only add close-ups, never remove one.

A close-up changes nothing in the trace; it only gets looked at more closely.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from render_agent.errors import AgentError, PauseJobError
from render_agent.llm.base import LLM
from render_agent.plan.trace_rules import TraceRules
from render_agent.util.fs import atomic_write_json, safe_name
from render_agent.util.prompts import load_prompt, quote_block, render

log = logging.getLogger("render_agent.p1")
STAGE = "plan_focus"
Rect = tuple[float, float, float, float]
COVERED = 0.8  # an area this much inside a bigger one is dropped


@dataclass
class Area:
    name: str
    box: Rect
    reason: str
    source: str  # "group", "hotspot" or "model"
    items: list[str] = field(default_factory=list)


def _union(boxes: list[Rect]) -> Rect:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _outline_box(points: list[list[float]]) -> Rect:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def fit(box: Rect, plan: Rect, rules: TraceRules) -> Rect:
    """The box padded, widened to at least `min_mm` and cut to at most `max_mm` on each side (about its
    middle), inside the plan."""
    f = rules.tiles.focus
    x0, y0, x1, y1 = box[0] - f.pad_mm, box[1] - f.pad_mm, box[2] + f.pad_mm, box[3] + f.pad_mm
    out = []
    for lo, hi, plo, phi in ((x0, x1, plan[0], plan[2]), (y0, y1, plan[1], plan[3])):
        mid, size = (lo + hi) / 2, min(max(hi - lo, f.min_mm), f.max_mm)
        lo, hi = mid - size / 2, mid + size / 2
        if lo < plo:
            lo, hi = plo, plo + size
        if hi > phi:
            lo, hi = phi - size, phi
        out += [max(lo, plo), min(hi, phi)]
    return (out[0], out[2], out[1], out[3])


def _inside(centre: tuple[float, float], box: Rect) -> bool:
    return box[0] <= centre[0] <= box[2] and box[1] <= centre[1] <= box[3]


def _centre(box: Rect) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _gap(a: Rect, b: Rect) -> float:
    """Distance between two boxes (0 when they touch or overlap)."""
    return max(max(a[0] - b[2], b[0] - a[2], 0.0), max(a[1] - b[3], b[1] - a[3], 0.0))


def _clusters(
    members: set[str], boxes: dict[str, Rect], reading: dict[str, Any], touch_mm: float
) -> list[set[str]]:
    """Members split into groups that are joined to each other (through a door, or by sharing a wall)."""
    parent = {m: m for m in members}

    def find(i: str) -> str:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for c in reading["connections"]:
        if c["a"] in members and c["b"] in members:
            parent[find(c["a"])] = find(c["b"])
    ids = sorted(members)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if _gap(boxes[a], boxes[b]) <= touch_mm:
                parent[find(a)] = find(b)
    out: dict[str, set[str]] = {}
    for m in ids:
        out.setdefault(find(m), set()).add(m)
    return list(out.values())


def group_areas(reading: dict[str, Any], plan: Rect, rules: TraceRules) -> list[Area]:
    """One area for each cluster of rooms of each group on this plan: a bath far from the ensuite is a
    close-up of its own, not one wide picture of both."""
    f = rules.tiles.focus
    rooms = {r["id"]: r for r in reading["rooms"]}
    boxes = {i: _outline_box(r["outline"]) for i, r in rooms.items()}
    out: list[Area] = []
    for g in f.groups:
        words = [w.upper() for w in g.words]
        members = {
            r["id"] for r in rooms.values() if r["name"] and any(w in r["name"].upper() for w in words)
        }
        if not members:
            continue
        if g.link:
            for c in reading["connections"]:
                if (c["a"] in members) != (c["b"] in members):
                    other = rooms.get(c["b"] if c["a"] in members else c["a"])
                    if other and other["area_m2"] <= f.link_max_area_m2:
                        members.add(other["id"])
        clusters = _clusters(members, boxes, reading, f.pad_mm / 2)
        for cluster in sorted(clusters, key=lambda c: min(boxes[i][1] for i in c)):
            box = _union([boxes[i] for i in cluster])
            near = (box[0] - f.pad_mm, box[1] - f.pad_mm, box[2] + f.pad_mm, box[3] + f.pad_mm)
            doors = [o for o in reading["openings"] if _inside(_centre(tuple(o["frame"])), near)]  # type: ignore[arg-type]
            if doors:
                box = _union([box, *(tuple(o["frame"]) for o in doors)])  # type: ignore[misc]
            names = sorted({(rooms[i]["name"] or rules.rooms.unlabelled_name).title() for i in cluster})
            label = g.name if len(clusters) == 1 else f"{g.name}: {', '.join(names[:2])}"
            out.append(
                Area(
                    label,
                    fit(box, plan, rules),
                    f"Rooms: {', '.join(names)}",
                    "group",
                    [*cluster, *(o["id"] for o in doors)],
                )
            )
    return out


def hotspot_areas(reading: dict[str, Any], plan: Rect, rules: TraceRules, covered: list[Area]) -> list[Area]:
    """Items that are not Green and sit in no area yet, clustered by closeness into areas of their own."""
    f = rules.tiles.focus
    spots: list[tuple[str, Rect]] = []
    for o in reading["openings"]:
        if o["confidence"] != "clear":
            spots.append((o["id"], tuple(o["frame"])))  # type: ignore[arg-type]
    for r in reading["rooms"]:
        if not r["name"]:
            spots.append((r["id"], _outline_box(r["outline"])))
    for r in reading["recesses"]:
        if r["confidence"] != "clear":
            spots.append((r["id"], tuple(r["rect"])))  # type: ignore[arg-type]
    for k in reading.get("kitchen", []):
        if k["confidence"] != "clear":
            spots.append((k["id"], tuple(k["rect"])))  # type: ignore[arg-type]
    spots = [(i, b) for i, b in spots if not any(_inside(_centre(b), a.box) for a in covered)]
    clusters: list[list[tuple[str, Rect]]] = []
    for spot in spots:
        for c in clusters:
            box = _union([b for _, b in c])
            if (
                abs(_centre(spot[1])[0] - _centre(box)[0]) <= f.max_mm / 2 - f.pad_mm
                and abs(_centre(spot[1])[1] - _centre(box)[1]) <= f.max_mm / 2 - f.pad_mm
                and _union([box, spot[1]])[2] - _union([box, spot[1]])[0] <= f.max_mm - 2 * f.pad_mm
                and _union([box, spot[1]])[3] - _union([box, spot[1]])[1] <= f.max_mm - 2 * f.pad_mm
            ):
                c.append(spot)
                break
        else:
            clusters.append([spot])
    return [
        Area(
            f"Items to check closely ({len(c)})",
            fit(_union([b for _, b in c]), plan, rules),
            "Items the code could not read with confidence",
            "hotspot",
            [i for i, _ in c],
        )
        for c in sorted(clusters, key=len, reverse=True)
    ]


def _overlap(a: Rect, b: Rect) -> float:
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0.0) * max(h, 0.0)


def merge(areas: list[Area], rules: TraceRules) -> list[Area]:
    """Areas in order, without any that lies mostly inside an earlier one, at most `max_areas`."""
    kept: list[Area] = []
    for a in areas:
        size = (a.box[2] - a.box[0]) * (a.box[3] - a.box[1])
        if any(_overlap(a.box, k.box) >= COVERED * size for k in kept):
            continue
        kept.append(a)
    return kept[: rules.tiles.focus.max_areas]


def model_prompt(
    prompts_dir: Path,
    plan: Rect,
    reading: dict[str, Any],
    chosen: list[Area],
    ratio: int | None,
    rules: TraceRules,
) -> str:
    f = rules.tiles.focus
    width, height = plan[2] - plan[0], plan[3] - plan[1]

    def at(box: Rect) -> list[float]:
        return [
            round((box[0] - plan[0]) / width, 3),
            round((box[1] - plan[1]) / height, 3),
            round((box[2] - plan[0]) / width, 3),
            round((box[3] - plan[1]) / height, 3),
        ]

    items = [
        {"id": r["id"], "what": f"room {r['name'] or 'unlabelled'}", "at": at(_outline_box(r["outline"]))}
        for r in reading["rooms"]
    ] + [
        {"id": o["id"], "what": f"opening {o['kind']}", "at": at(tuple(o["frame"]))}  # type: ignore[arg-type]
        for o in reading["openings"]
    ]
    return render(
        load_prompt(prompts_dir, STAGE),
        {
            "SCALE": f"The drawing scale is 1:{ratio}." if ratio else "No scale was found.",
            "MAX": str(f.llm_max_areas),
            "MIN": f"{round(f.min_mm)}",
            "CHOSEN": quote_block(
                "CHOSEN_AREAS",
                "\n".join(f"- {a.name}: {at(a.box)}" for a in chosen) or "NOT FOUND",
            ),
            "ITEMS": quote_block("OVERLAY_ITEMS", json.dumps(items, indent=1)),
        },
    )


def ask_model(
    llm: LLM,
    model: str,
    schema: dict[str, Any],
    folder: Path,
    overview: Path,
    prompt: str,
    plan: Rect,
    rules: TraceRules,
) -> list[Area]:
    """Areas the model suggests, as boxes in mm that code has checked (cached by the overview and the prompt;
    [] when the model cannot answer; a key or credit problem pauses the job)."""
    f = rules.tiles.focus
    key = hashlib.sha256(overview.read_bytes() + prompt.encode()).hexdigest()
    cache = folder / f"focus_plan_{safe_name(model)}.json"
    reading: dict[str, Any] | None = None
    if cache.is_file():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            reading = saved["reading"]
    if reading is None:
        try:
            reading = llm.call_json(f"{STAGE}.{model}", prompt, [overview], schema, STAGE)
        except PauseJobError:
            raise
        except AgentError as err:
            log.warning("%s could not suggest close-ups: %s", model, err)
            return []
        atomic_write_json(cache, {"key": key, "reading": reading})
    width, height = plan[2] - plan[0], plan[3] - plan[1]
    out: list[Area] = []
    for a in reading.get("areas", [])[: f.llm_max_areas]:
        x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in a["box"])
        if x1 <= x0 or y1 <= y0:
            continue  # an empty or back-to-front box is no area
        box = (plan[0] + x0 * width, plan[1] + y0 * height, plan[0] + x1 * width, plan[1] + y1 * height)
        why = " ".join(str(a["reason"]).split())[: rules.tiles.max_what_chars]
        out.append(
            Area(
                " ".join(str(a["name"]).split())[:60] or "Suggested close-up",
                fit(box, plan, rules),
                why,
                "model",
                [str(i) for i in a.get("item_ids", [])],
            )
        )
    return out
