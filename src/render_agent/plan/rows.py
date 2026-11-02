"""Readout rows that code writes from the trace (PLAN_VISUALS_SPEC sections 3 and 4): scale, area check,
shower rule (R18), clearance zones (R10) and a repeated or mirrored dwelling. PV3 adds the rows the
readout model reads (plan/readout.py). Orange and Red rows always carry a question; Green rows never do."""

from __future__ import annotations

from typing import Any

from render_agent.plan.trace_rules import TraceRules

ZONE_LABEL = {
    "living": "Living",
    "garage": "Garage",
    "alfresco": "Alfresco",
    "porch": "Porch",
    "total": "Total",
}


def row(
    rid: str,
    item: str,
    category: str,
    reading: str,
    evidence: str,
    confidence: str,
    question: str | None = None,
    *,
    alternative: str | None = None,
    readings: list[dict[str, Any]] | None = None,
    target_mm: list[float] | None = None,
    options: list[dict[str, str]] | None = None,
    ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if confidence == "GREEN":
        question, options = None, []
    elif not question:
        raise ValueError(f"row {rid}: an {confidence} row needs a question")
    return {
        "id": rid,
        "item": item,
        "category": category,
        "table": "main",
        "readings": readings or [],
        "reading": reading,
        "alternative": alternative,
        "evidence": evidence,
        "confidence": confidence,
        "question": question,
        "answer": None,
        "target_mm": target_mm,
        "changed": None,
        "options": options or [],
        "ref": ref,
    }


def scale_row(ratio: int | None, source: str) -> dict[str, Any]:
    if ratio is None:
        return row(
            "scale",
            "Scale",
            "scale",
            "NOT FOUND",
            "No 1:N scale in the sheet reading or the page text",
            "RED",
            "What scale is the floor plan drawn at (for example 1:100)?",
        )
    return row("scale", "Scale", "scale", f"1:{ratio}", source, "GREEN")


def area_rows(results: list[dict[str, Any]], tolerance: float, rules: TraceRules) -> list[dict[str, Any]]:
    out = []
    for r in results:
        if not r["read"] or r["schedule_m2"] is None:
            continue
        label = ZONE_LABEL.get(r["area"], r["area"].title())
        diff = r["traced_m2"] - r["schedule_m2"]
        share = diff / r["schedule_m2"] if r["schedule_m2"] else 0.0
        reading = f"traced {r['traced_m2']:.2f} m², drawing {r['schedule_m2']:.2f} m² ({share:+.1%})"
        evidence = f"Area schedule on the drawing; traced to the outer wall faces (tolerance {tolerance:.0%})"
        if r["within"]:
            out.append(row(f"area_{r['area']}", f"{label} area", "area", reading, evidence, "GREEN"))
        else:
            out.append(
                row(
                    f"area_{r['area']}",
                    f"{label} area",
                    "area",
                    reading,
                    evidence,
                    "RED",
                    f"The traced {label.lower()} area differs from the drawing's schedule by {share:+.1%}. "
                    "Is a wall, room or outdoor area outline wrong, or is the schedule measured "
                    "differently?",
                    options=[{"key": "schedule", "label": rules.readout.options["schedule"]}],
                )
            )
    return out


def _has(words: list[str], text: str) -> bool:
    return any(w in text.upper() for w in words)


def shower_rows(geometry: dict[str, Any], rules: TraceRules) -> list[dict[str, Any]]:
    """R18: a wet room with a bath and no shower symbol asks 'bath only, or bath with shower over?'."""
    s = rules.showers
    out = []
    for room in geometry["rooms"]:
        if not _has(s.wet_room_words, room["name"]):
            continue
        types = [f["type"] for f in geometry["fixtures"] if f["room"].upper() == room["name"]]
        bath = any(_has(s.bath_words, t) for t in types)
        shower = any(_has(s.shower_words, t) for t in types)
        if bath and not shower:
            out.append(
                row(
                    f"shower_{room['id']}",
                    f"{room['name']}: shower",
                    "fixture",
                    "Bath drawn, no shower symbol",
                    "Fixtures read in this room",
                    "ORANGE",
                    s.question,
                    options=[{"key": k, "label": v} for k, v in s.options.items()],
                )
            )
    return out


def clearance_rows(geometry: dict[str, Any], rules: TraceRules) -> list[dict[str, Any]]:
    return [
        row(
            f"clearance_{z['id']}",
            f"{z['room']}: hatched square",
            "clearance",
            rules.clearance_label,
            z["evidence"],
            "GREEN",
        )
        for z in geometry["clearance_zones"]
    ]


def mirror_row(dwelling: str, other: str, kind: str, score: float, rules: TraceRules) -> dict[str, Any]:
    how = "mirrored" if kind == "mirrored" else "repeated"
    return row(
        "mirror",
        f"{dwelling}: same layout as {other}",
        "dwelling",
        f"Same as {other}, {how} ({score:.0%} of the walls match)",
        "Traced walls compared after flipping",
        "ORANGE",
        f"Is {dwelling} the same as {other}, {how}? If yes, its plan is built from {other}'s.",
        options=[{"key": k, "label": rules.readout.options[k]} for k in ("same", "different")],
    )


def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {k.lower(): sum(r["confidence"] == k for r in rows) for k in ("GREEN", "ORANGE", "RED")}


def wall_rows(code_walls: list[dict[str, Any]], rules: TraceRules) -> list[dict[str, Any]]:
    """WALL rows from the overlay check against the drawing (a band not traced, or a trace not drawn)."""
    r = rules.readout
    out = []
    for w in code_walls:
        box = w["box"]
        out.append(
            row(
                w["id"],
                r.templates.item["wall"].format(
                    kind=w["kind"], x=round((box[0] + box[2]) / 2), y=round((box[1] + box[3]) / 2)
                ),
                "wall",
                r.templates.reading[f"wall_{w['kind']}"],
                w["evidence"],
                str(w["confidence"]).upper(),
                w["question"],
                target_mm=box,
                options=[
                    {"key": "confirm", "label": r.options["confirm"]},
                    {"key": "not_there", "label": r.options["not_there"]},
                ],
            )
        )
    return out


def assign_tables(all_rows: list[dict[str, Any]], rules: TraceRules) -> list[dict[str, Any]]:
    """Main table first, in the project's order (scale, rooms, connections, recesses, windows, walls);
    every other kind of row goes to the second table, 'Other checks'."""
    main = rules.readout.main_kinds
    for r in all_rows:
        r["table"] = "main" if r["category"] in main else "other"
    first = sorted((r for r in all_rows if r["table"] == "main"), key=lambda r: main.index(r["category"]))
    return first + [r for r in all_rows if r["table"] == "other"]


def summaries(all_rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Counts for the whole readout (the gate), the main table and 'Other checks'."""
    return {
        "all": summary(all_rows),
        "main": summary([r for r in all_rows if r["table"] == "main"]),
        "other": summary([r for r in all_rows if r["table"] == "other"]),
    }


def summary_line(counts: dict[str, int]) -> str:
    return f"{counts['green']} Green, {counts['orange']} Orange, {counts['red']} Red"
