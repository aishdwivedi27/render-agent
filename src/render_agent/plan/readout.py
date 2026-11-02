"""Readout rows the readout model(s) read, scored and merged by code (PLAN_VISUALS_SPEC section 4).

One row per room, connection (a door or opening between spaces), recess, window and fixed fixture.
Code scores every row after the model answers:
- an item with an alternative reading cannot be Green;
- a room or recess corner, or an opening, farther than `overlay_tolerance_mm` from the traced walls
  cannot be Green;
- Orange and Red rows always carry a question (the model's, else a template from plan_trace.yaml);
  Green rows never do.
With Both, each model reads separately; the same reading from both, both Green, is Green; any
difference is Orange and shows both readings. A model that could not read the page leaves the other's
rows Orange.

Answers are applied to the primary reading in the order they were given (`apply_answers`): picking a
model's reading copies that model's item, "not on the drawing" removes it, a confirmed shower adds one,
and free text is read again by the model (`reread`). Answered rows are Green and show what changed.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from render_agent.plan import geometry, rows
from render_agent.plan.trace_rules import TraceRules

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
COLLECTIONS = ("rooms", "openings", "recesses", "fixtures")
SEVERITY = {"GREEN": 0, "ORANGE": 1, "RED": 2}
OPENING_BOX_MM = 600.0  # the highlight box around an opening without a width
REREAD_OPTIONS = ("alternative", "text")  # answers the model reads again; the others code applies
Reread = Callable[
    [int, dict[str, list[dict[str, Any]]], list[dict[str, Any]]], dict[str, list[dict[str, Any]]]
]


def lower(*levels: str) -> str:
    """The lower confidence (RED below ORANGE below GREEN)."""
    return max(levels, key=lambda c: SEVERITY[c])


def mine(items: list[dict[str, Any]], dwelling: str, count: int) -> list[dict[str, Any]]:
    """The items of one dwelling (all items when the page has one dwelling), as geometry.build does."""
    return [x for x in items if x.get("dwelling") in (dwelling, "", None) or count <= 1]


def kind_of(collection: str, item: dict[str, Any]) -> str:
    if collection == "openings":
        return "window" if item.get("kind") == "window" else "connection"
    return {"rooms": "room", "recesses": "recess", "fixtures": "fixture"}[collection]


def row_id(collection: str, item: dict[str, Any]) -> str:
    return f"{kind_of(collection, item)}_{item['id']}"


def _title(name: str) -> str:
    """'BED 3' as the table writes it: 'Bed 3'."""
    name = " ".join(str(name).split())
    return name.capitalize() if name.isupper() else name


def item_name(collection: str, item: dict[str, Any], rules: TraceRules) -> str:
    """What the row is about, as the table writes it: 'Bed 3', 'Bed 3 -> Hall', 'Robe in Bed 3',
    'Window, Bed 3 top wall' (templates in plan_trace.yaml)."""
    t, r = rules.readout.templates.item, rules.readout
    room = _title(item.get("room") or "") or "Room"
    if collection == "rooms":
        return t["room"].format(name=_title(item.get("name", "")) or "Room")
    if collection == "openings":
        if item.get("name"):
            return str(item["name"])
        label = r.kind_labels.get(str(item.get("kind")), str(item.get("kind")))
        if label.startswith("Door with"):
            label = "Door"
        if item.get("kind") != "window" and len(item.get("rooms", [])) >= 2:
            return " -> ".join(_title(x) for x in item["rooms"])
        where = (
            _title(item["host"])
            if item.get("host")
            else (_title(item["rooms"][0]) if item.get("rooms") else "")
        )
        if not where:
            return t["opening_no_room"].format(label=label)
        if item.get("side"):
            return t["opening"].format(label=label, room=where, side=item["side"])
        return t["opening_no_side"].format(label=label, room=where)
    if collection == "recesses":
        return t["recess"].format(
            kind=r.recess_labels.get(str(item.get("kind")), str(item.get("kind"))), room=room
        )
    return f"{str(item.get('type', '')).upper()} in {room}"


def reading_text(collection: str, item: dict[str, Any], shape: dict[str, Any], rules: TraceRules) -> str:
    """What the reader read, in words."""
    r = rules.readout
    t = r.templates.reading
    if collection == "rooms":
        area = geometry.poly_area_m2(shape["poly"]) if len(shape["poly"]) >= 3 else 0.0
        return t["room"].format(name=_title(item.get("name", "")), area=f"{area:.1f}")
    if collection == "openings":
        label = r.kind_labels.get(str(item.get("kind")), str(item.get("kind")))
        if item.get("code"):
            label += t["code"].format(code=item["code"])
        parts = [label]
        if item.get("hinge"):
            parts.append(t["hinge"].format(hinge=item["hinge"]))
        if item.get("swing"):
            parts.append(t["swing"].format(swing=_title(item["swing"])))
        if item.get("width_mm"):
            parts.append(t["width"].format(width=f"{float(item['width_mm']):.0f}"))
        return ", ".join(parts)
    if collection == "recesses":
        return r.recess_labels.get(str(item.get("kind")), str(item.get("kind")))
    return str(item.get("type", "")).upper()


def _same(collection: str, a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two readers read an item the same way. For an opening, a room list or a code one reader did not
    give is not a difference."""
    if collection != "openings":
        return _key(collection, a) == _key(collection, b)
    ka, kb = _key(collection, a), _key(collection, b)
    return (
        ka[0] == kb[0]
        and (not ka[1] or not kb[1] or ka[1] == kb[1])
        and (not ka[2] or not kb[2] or ka[2] == kb[2])
    )


def _key(collection: str, item: dict[str, Any]) -> tuple[Any, ...]:
    """What has to be the same for two models to have read an item the same way."""
    if collection == "rooms":
        return (" ".join(str(item.get("name", "")).upper().split()),)
    if collection == "openings":
        rooms_ = frozenset(" ".join(str(x).upper().split()) for x in item.get("rooms", []))
        return (item.get("kind"), item.get("code"), rooms_)
    if collection == "recesses":
        return (item.get("kind"), str(item.get("room", "")).upper())
    return (str(item.get("type", "")).upper(), str(item.get("room", "")).upper())


def _wall_dist(p: Point, walls: list[Rect]) -> float:
    """Distance from a point to the nearest traced wall (0 on or inside one)."""

    def d(r: Rect) -> float:
        dx = max(r[0] - p[0], 0.0, p[0] - r[2])
        dy = max(r[1] - p[1], 0.0, p[1] - r[3])
        return float((dx * dx + dy * dy) ** 0.5)

    return min((d(r) for r in walls), default=0.0)


Gap = tuple[Point, float]  # an opening: its middle and half its width, in mm


def _half(item: dict[str, Any]) -> float:
    return float(item.get("width_mm") or OPENING_BOX_MM) / 2


def gaps(reading: dict[str, Any], page_mm: tuple[float, float]) -> list[Gap]:
    """The openings read on the page: a room corner inside one is beside a doorway, not off the walls."""
    return [(geometry.to_mm(o["at"], page_mm), _half(o)) for o in reading.get("openings", []) if o.get("at")]


def _corner_off(p: Point, walls: list[Rect], openings: list[Gap]) -> float:
    off = _wall_dist(p, walls)
    for at, half in openings:
        off = min(off, max(0.0, float(((p[0] - at[0]) ** 2 + (p[1] - at[1]) ** 2) ** 0.5) - half))
    return off


def shape(
    collection: str,
    item: dict[str, Any],
    page_mm: tuple[float, float],
    walls: list[Rect],
    rules: TraceRules,
    openings: list[Gap] | None = None,
) -> dict[str, Any]:
    """The item in mm: its outline as drawn on the overlay (snapped onto the walls) or the opening's
    middle, its box, its centre, and how far it sits off the traced walls (0 when there are no walls
    to compare with). An opening's middle is a gap in the wall, so half its width does not count."""
    if collection == "openings":
        at = geometry.to_mm(item.get("at") or [0, 0], page_mm)
        half = max(_half(item), OPENING_BOX_MM / 2)
        box = (at[0] - half, at[1] - half, at[0] + half, at[1] + half)
        off = max(0.0, _wall_dist(at, walls) - _half(item)) if walls else 0.0
        return {"poly": [], "box": box, "centre": at, "off": off}
    raw = geometry.outline_mm(item.get("outline") or [], page_mm)
    poly = geometry.snap(raw, walls, rules.snap_mm) if raw else []
    if not poly:
        return {"poly": [], "box": None, "centre": (0.0, 0.0), "off": 0.0}
    box = (min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly))
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    off = 0.0
    outdoor = collection == "rooms" and rules.zone_of(str(item.get("name", ""))) in rules.areas.outdoor_zones
    if walls and collection in ("rooms", "recesses") and not outdoor:
        off = max(_corner_off(p, walls, openings or []) for p in poly)
    return {"poly": poly, "box": box, "centre": centre, "off": off}


def _union(boxes: list[Rect | None]) -> list[float] | None:
    have = [b for b in boxes if b is not None]
    if not have:
        return None
    return [round(v, 1) for v in geometry.bounds(have)]


def _match(
    collection: str,
    primary: list[tuple[dict[str, Any], dict[str, Any]]],
    other: list[tuple[dict[str, Any], dict[str, Any]]],
    within: float,
) -> dict[int, int]:
    """Greedy one-to-one match of another model's items onto the primary's: rooms by name, fixtures by
    room and type, recesses by room and kind (then the nearest centre within `within`); openings by the
    nearest middle within `within`."""
    out: dict[int, int] = {}
    used: set[int] = set()

    def dist(a: dict[str, Any], b: dict[str, Any]) -> float:
        return float(((a["centre"][0] - b["centre"][0]) ** 2 + (a["centre"][1] - b["centre"][1]) ** 2) ** 0.5)

    if collection != "openings":
        for i, (p, ps) in enumerate(primary):
            same = [
                j
                for j, (o, _) in enumerate(other)
                if j not in used and _key(collection, o) == _key(collection, p)
            ]
            if same:
                j = min(same, key=lambda k: dist(ps, other[k][1]))
                out[i] = j
                used.add(j)
    for i, (_p, ps) in enumerate(primary):
        if i in out:
            continue
        near = [j for j, (_, os_) in enumerate(other) if j not in used and dist(ps, os_) <= within]
        if near:
            j = min(near, key=lambda k: dist(ps, other[k][1]))
            out[i] = j
            used.add(j)
    return out


def _capped(item: dict[str, Any], shp: dict[str, Any], rules: TraceRules) -> tuple[str, str | None]:
    """(confidence after code's rules, why it was lowered: 'alternative' | 'off_walls' | None)."""
    conf = str(item.get("confidence") or "RED")
    conf = conf if conf in SEVERITY else "RED"
    why = None
    if item.get("alternative"):
        conf, why = lower(conf, "ORANGE"), "alternative"
    off = max(float(shp["off"]), float(item.get("off_drawing_mm") or 0))
    if off > rules.readout.overlay_tolerance_mm:
        conf, why = lower(conf, "ORANGE"), "off_walls"
        shp["off"] = off
    return conf, why


def _question(template: str, rules: TraceRules, own: str | None = None, **values: Any) -> str:
    """The model's own question when it gave one, else the template filled in."""
    if own and own.strip() and own.strip().upper() != "NOT FOUND":
        return own.strip()
    return rules.readout.questions[template].format(**values)


def _opt(key: str, label: str) -> dict[str, str]:
    return {"key": key, "label": label}


def model_rows(
    dwelling: str,
    count: int,
    readings: dict[str, dict[str, Any]],
    primary: str,
    names: list[str],
    failed: list[str],
    labels: dict[str, str],
    page_mm: tuple[float, float],
    walls: list[Rect],
    rules: TraceRules,
) -> list[dict[str, Any]]:
    """The rows for one dwelling, in the spec's order: rooms, connections, recesses, windows, fixtures."""
    ro = rules.readout
    others = [n for n in names if n != primary]
    # code is a reader of its own, but a column only when more than one model read the drawing
    readers = len(names) - (1 if labels.get(primary) == ro.code_label else 0)
    lab = {n: labels.get(n, n) for n in names}
    by_kind: dict[str, list[dict[str, Any]]] = {
        k: [] for k in ("room", "connection", "recess", "window", "fixture")
    }
    page_gaps = gaps(readings[primary], page_mm)
    for coll in COLLECTIONS:
        p_items = [
            (x, shape(coll, x, page_mm, walls, rules, page_gaps))
            for x in mine(readings[primary].get(coll, []), dwelling, count)
        ]
        matched: dict[str, dict[int, int]] = {}
        o_items: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for m in others:
            if m in readings:
                o_items[m] = [
                    (x, shape(coll, x, page_mm, walls, rules, page_gaps))
                    for x in mine(readings[m].get(coll, []), dwelling, count)
                ]
                matched[m] = _match(coll, p_items, o_items[m], ro.merge_match_mm)
        for i, (item, shp) in enumerate(p_items):
            text = reading_text(coll, item, shp, rules)
            conf, why = _capped(item, shp, rules)
            name = item_name(coll, item, rules)
            values = {
                "item": name,
                "reading": text,
                "alternative": item.get("alternative") or "",
                "off_mm": round(shp["off"]),
                "code": lab[primary],
                "evidence": str(item.get("evidence") or "no evidence"),
                "item_label": name,
            }
            per = [{"model": lab[primary], "status": "read", "reading": text, "confidence": conf}]
            model_items = [{"model": primary, "item_id": str(item["id"])}]
            boxes = [shp["box"]]
            question: str | None = None
            alternative = item.get("alternative") or None
            options: list[dict[str, str]] = []
            missing = [m for m in others if m in failed or m not in readings]
            absent = [m for m in others if m in matched and i not in matched[m]]
            differ: list[tuple[str, str]] = []
            lowest_q = item.get("question")
            template_q = None
            if lowest_q in ro.questions:  # a reader named a question template instead of writing one
                template_q, lowest_q = str(lowest_q), None
            decided: list[tuple[str, str]] = []
            for m in others:
                if m in matched and i in matched[m]:
                    o, os_ = o_items[m][matched[m][i]]
                    otext = reading_text(coll, o, os_, rules)
                    oconf, _ = _capped(o, os_, rules)
                    per.append({"model": lab[m], "status": "read", "reading": otext, "confidence": oconf})
                    model_items.append({"model": m, "item_id": str(o["id"])})
                    boxes.append(os_["box"])
                    far = (
                        coll != "openings"
                        and os_["box"]
                        and shp["box"]
                        and max(abs(a - b) for a, b in zip(os_["box"], shp["box"], strict=True))
                        > ro.overlay_tolerance_mm
                    )
                    if not _same(coll, o, item) or far:
                        differ.append((m, otext))
                        if item.get("alternative") and o.get("kind") == item["alternative"]:
                            decided.append((m, otext))
                    elif SEVERITY[oconf] > SEVERITY[conf]:
                        conf, lowest_q = oconf, o.get("question") or lowest_q
                        alternative = alternative or o.get("alternative")
                else:
                    per.append(
                        {
                            "model": lab[m],
                            "status": "not_read" if m in missing else "read",
                            "reading": "not read" if m in missing else "not found",
                            "confidence": "RED",
                        }
                    )
            if differ:
                conf = lower(conf, "ORANGE")
                text = "; ".join([f"{lab[primary]}: {text}", *(f"{lab[m]}: {t}" for m, t in differ)])
                question = ro.questions["differ"].format(
                    models=" and ".join(lab[n] for n in [primary, *(m for m, _ in differ)]), item=name
                )
                if decided and ro.scoring.ambiguous_best_guess == "model":
                    # code could not tell between two readings and a model picked one: its reading is the
                    # best guess, still Orange until the designer confirms
                    first = per[0]["reading"]
                    text = decided[0][1]
                    question = ro.questions["ambiguous"].format(
                        code=lab[primary],
                        item=name,
                        reading=first,
                        alternative=decided[0][1],
                        models=" and ".join(lab[m] for m, _ in decided),
                        model_reading=decided[0][1],
                    )
                alternative = "; ".join(t for _, t in differ)
                options = [
                    _opt(
                        f"use:{primary}",
                        ro.options["use_model"].format(model=lab[primary], reading=per[0]["reading"]),
                    )
                ]
                options += [
                    _opt(f"use:{m}", ro.options["use_model"].format(model=lab[m], reading=t))
                    for m, t in differ
                ]
            elif missing:
                conf = lower(conf, "ORANGE")
                question = ro.questions["not_read"].format(
                    missing=" and ".join(lab[m] for m in missing), only=lab[primary], item=name
                )
            elif absent and not (conf != "GREEN" and (lowest_q or template_q)):
                conf = lower(conf, "ORANGE")
                question = ro.questions["only_one"].format(
                    only=lab[primary], missing=" and ".join(lab[m] for m in absent), item=name
                )
            elif conf != "GREEN":
                template = template_q or why or ("alternative" if alternative else "default")
                if conf == "RED" and not lowest_q and not template_q and not why:
                    template = "unreadable"
                question = _question(
                    template, rules, lowest_q, **{**values, "alternative": alternative or ""}
                )
            if conf != "GREEN" and not options:
                options = [_opt("confirm", ro.options["confirm"])]
                if alternative and not differ:
                    options.append(
                        _opt("alternative", ro.options["alternative"].format(alternative=alternative))
                    )
            if conf != "GREEN":
                options.append(_opt("not_there", ro.options["not_there"]))
            r = rows.row(
                row_id(coll, item),
                name,
                kind_of(coll, item),
                text,
                str(item.get("evidence") or ""),
                conf,
                question,
                alternative=alternative if conf != "GREEN" else None,
                readings=per if readers > 1 else [],
                target_mm=_union(boxes),
                options=options,
                ref={
                    "collection": coll,
                    "item_id": str(item["id"]),
                    "model": primary,
                    "model_items": model_items,
                },
            )
            by_kind[r["category"]].append(r)
        for m in others:
            if m not in matched:
                continue
            taken = set(matched[m].values())
            for j, (o, os_) in enumerate(o_items[m]):
                if j in taken:
                    continue
                otext = reading_text(coll, o, os_, rules)
                name = item_name(coll, o, rules)
                per = [{"model": lab[primary], "status": "read", "reading": "not found", "confidence": "RED"}]
                per += [
                    {
                        "model": lab[n],
                        "status": "read",
                        "reading": otext if n == m else "not found",
                        "confidence": "ORANGE",
                    }
                    for n in others
                ]
                r = rows.row(
                    f"{kind_of(coll, o)}_{m}_{o['id']}",
                    name,
                    kind_of(coll, o),
                    f"{lab[m]}: {otext}",
                    str(o.get("evidence") or ""),
                    "ORANGE",
                    ro.questions["only_one"].format(only=lab[m], missing=lab[primary], item=name),
                    readings=per if readers > 1 else [],
                    target_mm=_union([os_["box"]]),
                    options=[
                        _opt(f"use:{m}", ro.options["confirm"]),
                        _opt("not_there", ro.options["not_there"]),
                    ],
                    ref={
                        "collection": coll,
                        "item_id": f"{m}_{o['id']}",
                        "model": m,
                        "model_items": [{"model": m, "item_id": str(o["id"])}],
                    },
                )
                by_kind[r["category"]].append(r)
    return [r for k in ("room", "connection", "recess", "window", "fixture") for r in by_kind[k]]


def batch(dwelling_rows: list[tuple[str, dict[str, Any]]], size: int) -> list[tuple[str, dict[str, Any]]]:
    """The next questions: up to `size` rows that are not Green, Red first, in table order."""
    open_ = [(d, r) for d, r in dwelling_rows if r["confidence"] != "GREEN"]
    open_.sort(key=lambda dr: -SEVERITY[dr[1]["confidence"]])  # stable: table order within a colour
    return open_[:size]


def item_of(ref: dict[str, Any], model: str) -> str | None:
    """That model's own id for the item a row is about."""
    return next((x["item_id"] for x in ref["model_items"] if x["model"] == model), None)


def _find(items: list[dict[str, Any]], item_id: str) -> int | None:
    return next((i for i, x in enumerate(items) if str(x.get("id")) == item_id), None)


def _put(reading: dict[str, Any], coll: str, item: dict[str, Any]) -> None:
    items = reading.setdefault(coll, [])
    at = _find(items, str(item["id"]))
    if at is None:
        items.append(item)
    else:
        items[at] = item


def _confirmed_shower(reading: dict[str, Any], room_id: str, rules: TraceRules) -> None:
    """R18 'bath with shower over': a shower is recorded over the bath drawn in that room."""
    room = next((r for r in reading.get("rooms", []) if str(r.get("id")) == room_id), None)
    if room is None:
        return
    name = str(room.get("name", "")).upper()
    bath = next(
        (
            f
            for f in reading.get("fixtures", [])
            if str(f.get("room", "")).upper() == name
            and any(w in str(f.get("type", "")).upper() for w in rules.showers.bath_words)
        ),
        None,
    )
    if bath is None:
        return
    _put(
        reading,
        "fixtures",
        {
            **copy.deepcopy(bath),
            "id": f"confirmed_{room_id}",
            "type": rules.showers.shower_words[0],
            "evidence": "Confirmed by the designer: bath with shower over",
            "confidence": "GREEN",
            "alternative": None,
            "question": None,
            "source": "confirmed",
        },
    )


def apply_answers(
    primary: dict[str, Any],
    readings: dict[str, dict[str, Any]],
    answers: list[dict[str, Any]],
    rules: TraceRules,
    reread: Reread | None = None,
) -> dict[str, Any]:
    """The primary reading with the designer's answers applied, batch by batch in the order given.
    Free-text answers (and a one-model alternative) go to `reread` once per batch with the items they
    are about; the items it returns replace those items."""
    out = copy.deepcopy(primary)
    for b in sorted({int(a.get("batch", 0)) for a in answers}):
        to_read: dict[str, list[dict[str, Any]]] = {}
        asked: list[dict[str, Any]] = []
        for a in (x for x in answers if int(x.get("batch", 0)) == b):
            ref, opt = a.get("ref"), str(a.get("option") or "")
            if a["row_id"].startswith("shower_") and opt == "shower_over":
                _confirmed_shower(out, a["row_id"][len("shower_") :], rules)
                continue
            if not ref:
                continue
            coll, item_id = ref["collection"], ref["item_id"]
            if opt.startswith("use:"):
                m = opt[4:]
                src_id = item_of(ref, m)
                src = readings.get(m, {}).get(coll, [])
                at = _find(src, str(src_id)) if src_id is not None else None
                if at is not None:
                    _put(out, coll, {**copy.deepcopy(src[at]), "id": item_id})
            elif opt == "not_there":
                at = _find(out.get(coll, []), item_id)
                if at is not None:
                    out[coll].pop(at)
            elif opt in REREAD_OPTIONS:
                at = _find(out.get(coll, []), item_id)
                if at is None and ref["model"] in readings:
                    src = readings[ref["model"]].get(coll, [])
                    k = _find(src, str(item_of(ref, ref["model"])))
                    item = {**copy.deepcopy(src[k]), "id": item_id} if k is not None else None
                else:
                    item = copy.deepcopy(out[coll][at]) if at is not None else None
                if item is not None:
                    to_read.setdefault(coll, []).append(item)
                    asked.append(a)
        if to_read and reread is not None:
            got = reread(b, to_read, asked)
            wanted = {(c, str(x["id"])) for c, xs in to_read.items() for x in xs}
            for coll, items in got.items():
                for item in items:
                    if (coll, str(item.get("id"))) in wanted:
                        _put(out, coll, item)
    return out


def latest(answers: list[dict[str, Any]], dwelling: str) -> dict[str, dict[str, Any]]:
    """The last answer for each row of a dwelling."""
    out: dict[str, dict[str, Any]] = {}
    for a in answers:
        if a.get("dwelling") == dwelling:
            out[a["row_id"]] = a
    return out


def finalize(drows: list[dict[str, Any]], answered: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Answered rows become Green with the answer and what the reading was; an answered row that is no
    longer read (its item was removed, or a shower confirmed) stays in the table from the answer."""
    seen = set()
    out = []
    for r in drows:
        a = answered.get(r["id"])
        if a is not None:
            before = a.get("reading_before") or ""
            r = {
                **r,
                "confidence": "GREEN",
                "question": None,
                "options": [],
                "alternative": None,
                "answer": a["answer"],
                "changed": f"was: {before}" if before and before != r["reading"] else None,
            }
        seen.add(r["id"])
        out.append(r)
    for rid, a in answered.items():
        if rid in seen:
            continue
        out.append(
            {
                **rows.row(
                    rid, a["item"], a.get("category") or "answer", a["answer"], "Your answer", "GREEN"
                ),
                "answer": a["answer"],
                "changed": f"was: {a.get('reading_before') or ''}",
                "target_mm": a.get("target_mm"),
            }
        )
    return out
