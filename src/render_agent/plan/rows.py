"""Readout rows that code writes from the trace (PLAN_VISUALS_SPEC sections 3 and 4): scale, area check,
shower rule (R18), clearance zones (R10) and a repeated or mirrored dwelling. PV3 adds the rows the
readout model reads. Orange and Red rows always carry a question; Green rows never do."""

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
) -> dict[str, Any]:
    if confidence == "GREEN":
        question = None
    elif not question:
        raise ValueError(f"row {rid}: an {confidence} row needs a question")
    return {
        "id": rid,
        "item": item,
        "category": category,
        "readings": [],
        "reading": reading,
        "alternative": None,
        "evidence": evidence,
        "confidence": confidence,
        "question": question,
        "answer": None,
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


def area_rows(results: list[dict[str, Any]], tolerance: float) -> list[dict[str, Any]]:
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


def mirror_row(dwelling: str, other: str, kind: str, score: float) -> dict[str, Any]:
    how = "mirrored" if kind == "mirrored" else "repeated"
    return row(
        "mirror",
        f"{dwelling}: same layout as {other}",
        "dwelling",
        f"Same as {other}, {how} ({score:.0%} of the walls match)",
        "Traced walls compared after flipping",
        "ORANGE",
        f"Is {dwelling} the same as {other}, {how}? If yes, its plan is built from {other}'s.",
    )


def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {k.lower(): sum(r["confidence"] == k for r in rows) for k in ("GREEN", "ORANGE", "RED")}
