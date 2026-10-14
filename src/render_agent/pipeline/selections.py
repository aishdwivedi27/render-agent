"""selections.json: material items pre-filled from the House Specification (docs/SPEC.md section 6).

Each item in knowledge/materials_library.yaml gets the PDF value (every matching spec line) or
NOT FOUND. A NOT FOUND item takes the item's standard `default` (labelled as not from the drawings); only an
item with no default that a planned view shows needs a designer choice at the gate.
`@select` directives pre-set a choice (still shown at the gate).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

NOT_FOUND = "NOT FOUND"
STANDARD_DEFAULT = "standard default (not in the drawings)"


def load_materials(knowledge_dir: Path) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((knowledge_dir / "materials_library.yaml").read_text(encoding="utf-8"))
    return data["items"]  # type: ignore[no-any-return]


def _field_lines(spec: dict[str, Any], dotted: str) -> list[str]:
    section, _, key = dotted.partition(".")
    val = spec.get(section, {}).get(key, [])
    lines = [val] if isinstance(val, str) else list(val)
    return [x for x in lines if isinstance(x, str) and x.strip() and x.strip().upper() != NOT_FOUND]


def find_values(spec: dict[str, Any], item: dict[str, Any]) -> list[tuple[str, str]]:
    """(source, text) lines that give this item's value, from the colour schedule and named spec fields."""
    found: list[tuple[str, str]] = []
    names = [w.lower() for w in item.get("schedule", [])]
    for row in spec.get("MATERIALS_AND_COLOURS", {}).get("schedule_as_documented", []):
        if any(w in str(row.get("item", "")).lower() for w in names):
            found.append(("MATERIALS_AND_COLOURS.schedule_as_documented", f"{row['item']}: {row['colour']}"))
    rule = item.get("lines") or {}
    words = [w.lower() for w in rule.get("words", [])]
    also = [w.lower() for w in rule.get("with", [])]
    for field_name in rule.get("fields", []):
        for text in _field_lines(spec, field_name):
            low = text.lower()
            if any(w in low for w in words) and (not also or any(w in low for w in also)):
                found.append((field_name, text))
    return found


def _split_select(key: str, value: str, items: dict[str, Any]) -> dict[str, str]:
    """`@select entry_door=pivot, spotted gum` fills entry_door_type then entry_door_material."""
    if key in items:
        return {key: value}
    targets = [k for k in items if k.startswith(key + "_")]
    parts = [p.strip() for p in value.split(",")]
    if not targets or len(parts) > len(targets):
        return {}
    return dict(zip(targets, parts, strict=False))


def build_selections(
    spec: dict[str, Any],
    planned_view_types: set[str],
    materials: dict[str, dict[str, Any]],
    select: dict[str, str] | None = None,
) -> dict[str, Any]:
    preset: dict[str, str] = {}
    unknown: list[str] = []
    for k, v in (select or {}).items():
        split = _split_select(k, v, materials)
        if split:
            preset.update(split)
        else:
            unknown.append(f"@select {k}={v}")
    items = []
    for key, item in materials.items():
        found = find_values(spec, item)
        shown = sorted(planned_view_types & set(item.get("shown_by", [])))
        pdf_value = " | ".join(dict.fromkeys(t for _, t in found)) if found else NOT_FOUND
        chosen = preset.get(key)
        chosen_by = "@select directive" if chosen is not None else None
        if chosen is None and pdf_value == NOT_FOUND and item.get("default"):
            chosen, chosen_by = str(item["default"]), STANDARD_DEFAULT
        items.append(
            {
                "key": key,
                "label": item.get("label", key),
                "pdf_value": pdf_value,
                "source": sorted({s for s, _ in found}) or [NOT_FOUND],
                "shown_by_planned_views": shown,
                "needs_choice": pdf_value == NOT_FOUND and bool(shown) and chosen is None,
                "chosen": chosen,
                "chosen_by": chosen_by,
                "is_override": chosen is not None and pdf_value != NOT_FOUND,
                "options": list(item.get("options", [])),
            }
        )
    return {"items": items, "ignored_selects": unknown}


REPORT_FILE = "MATERIALS_AND_DEFAULTS.txt"


def colour_schedule_status(spec: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether the PDF carries a colour schedule: rows read into the spec, and schedule sheets seen."""
    rows = [
        f"{r.get('item', '')}: {r.get('colour', '')}"
        for r in spec.get("MATERIALS_AND_COLOURS", {}).get("schedule_as_documented", [])
    ]
    sheets = [
        f"{v['label']} (sheet {v['page']})"
        for v in catalog
        if v.get("view_kind") == "schedule"
        and any(w in v.get("label", "").lower() for w in ("colour", "color", "material", "finish"))
    ]
    return {"found_in_pdf": bool(rows), "rows": rows, "schedule_sheets": sheets}


def materials_report(selections: dict[str, Any]) -> str:
    """Plain-text summary for the designer: colour schedule found or not, and every value's source."""
    status = selections.get("colour_schedule", {"found_in_pdf": False, "rows": [], "schedule_sheets": []})
    out = ["MATERIALS AND COLOURS USED FOR THIS JOB", "=" * 40, ""]
    if status["found_in_pdf"]:
        out.append("Colour schedule in the PDF: YES. These lines were read from it:")
        out += [f"  - {r}" for r in status["rows"]]
    elif status["schedule_sheets"]:
        out.append(
            "Colour schedule in the PDF: A SCHEDULE SHEET WAS SEEN ("
            + ", ".join(status["schedule_sheets"])
            + ") BUT NO COLOUR LINES COULD BE READ. Check the colours below before approving."
        )
    else:
        out.append("Colour schedule in the PDF: NO. No colour schedule was found in the drawings.")
        out.append("  Add one to the drawings, or set colours with @select lines or at the approval gate.")
    out.append("")
    groups: dict[str, list[str]] = {"drawings": [], "default": [], "designer": [], "missing": []}
    for i in selections.get("items", []):
        by = i.get("chosen_by")
        if i.get("chosen") and by == STANDARD_DEFAULT:
            groups["default"].append(f"  - {i['label']}: {i['chosen']}")
        elif i.get("chosen"):
            was = f" (drawings said: {i['pdf_value']})" if i["pdf_value"] != NOT_FOUND else ""
            groups["designer"].append(f"  - {i['label']}: {i['chosen']} [{by}]{was}")
        elif i["pdf_value"] != NOT_FOUND:
            groups["drawings"].append(f"  - {i['label']}: {i['pdf_value']}")
        else:
            groups["missing"].append(f"  - {i['label']}: NOT FOUND (no default; choose at the gate)")
    titles = {
        "drawings": "FROM THE DRAWINGS",
        "default": "STANDARD DEFAULTS USED (not in the drawings; change with @select or at the gate)",
        "designer": "CHOSEN BY THE DESIGNER",
        "missing": "STILL TO CHOOSE",
    }
    for key, title in titles.items():
        out.append(f"{title}:")
        out += groups[key] or ["  (none)"]
        out.append("")
    return "\n".join(out)


def write_materials_report(job_dir: Path, selections: dict[str, Any]) -> None:
    from render_agent.util.fs import atomic_write_text

    atomic_write_text(job_dir / REPORT_FILE, materials_report(selections))


def final_value(item: dict[str, Any]) -> str:
    """What prompts use: the designer's choice, else the PDF value (may be NOT FOUND)."""
    return str(item["chosen"]) if item.get("chosen") else str(item["pdf_value"])
