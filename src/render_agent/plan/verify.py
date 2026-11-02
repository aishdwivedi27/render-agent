"""The model's check of each overlay tile (PLAN_VISUALS_SPEC section 3a).

Code draws the traced walls, rooms and openings over each cropped tile (`tiles.py`). A model looks at the tile
as printed beside the same tile with the overlay and reports where they differ. What it reports is asked,
never applied: every finding becomes one Orange WALL row (a question for the designer) and the trace is
unchanged. A model can therefore add questions but cannot turn a Green item into anything else, and what
it writes is quoted into the row as data.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from render_agent.errors import AgentError, PauseJobError
from render_agent.llm.base import LLM
from render_agent.plan import rows, tiles
from render_agent.plan.trace_rules import TraceRules
from render_agent.util.fs import atomic_write_json, safe_name
from render_agent.util.prompts import load_prompt, quote_block, render

log = logging.getLogger("render_agent.p1")
STAGE = "plan_verify"
Point = tuple[float, float]


def build_prompt(
    prompts_dir: Path,
    knowledge_dir: Path,
    tile: dict[str, Any],
    ratio: int | None,
    items: list[dict[str, Any]],
    rules: TraceRules,
) -> str:
    rules_file = knowledge_dir / "drawing_reading_rules.md"
    box = tile["box_mm"]
    return render(
        load_prompt(prompts_dir, STAGE),
        {
            "TILE": (f"{tile['title']} ({tile['reason']}). " if tile.get("title") else "")
            + f"{tile['name']}: x {round(box[0])} to {round(box[2])} mm, "
            f"y {round(box[1])} to {round(box[3])} mm of the sheet, "
            f"shown at {tile['mm_per_px']} mm per pixel.",
            "SCALE": f"The drawing scale is 1:{ratio}." if ratio else "No scale was found.",
            "MAX": str(rules.tiles.max_findings),
            "KINDS": "\n".join(f"- `{k}`: {v}" for k, v in rules.tiles.finding_kinds.items()),
            "ITEMS": quote_block("OVERLAY_ITEMS", json.dumps(items, indent=1) if items else "NOT FOUND"),
            "RULES": quote_block(
                "DRAWING_RULES",
                rules_file.read_text(encoding="utf-8") if rules_file.is_file() else "NOT FOUND",
            ),
        },
    )


def check_tile(
    llm: LLM,
    model: str,
    schema: dict[str, Any],
    folder: Path,
    tile: dict[str, Any],
    prompt: str,
) -> dict[str, Any] | None:
    """The model's findings for one tile (cached by the overlay's content, so Retry never pays twice), or
    None when this model could not read it. A key or credit problem pauses the job (raised)."""
    plain, overlay = folder / tile["plain"], folder / tile["overlay"]
    key = hashlib.sha256(overlay.read_bytes() + plain.read_bytes() + prompt.encode()).hexdigest()
    cache = folder / f"verify_{safe_name(model)}_{tile['name']}.json"
    if cache.is_file():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            return saved["reading"]  # type: ignore[no-any-return]
    try:
        reading = llm.call_json(f"{STAGE}.{model}.{tile['name']}", prompt, [plain, overlay], schema, STAGE)
    except PauseJobError:
        raise
    except AgentError as err:
        log.warning("%s could not check %s: %s", model, tile["name"], err)
        return None
    atomic_write_json(cache, {"key": key, "reading": reading})
    return reading


def _near(a: Point, b: Point, mm: float) -> bool:
    return abs(a[0] - b[0]) <= mm and abs(a[1] - b[1]) <= mm


def finding_rows(
    checked: list[tuple[dict[str, Any], dict[str, Any]]], rules: TraceRules
) -> list[dict[str, Any]]:
    """One Orange WALL row per distinct finding. `checked` is (tile entry, model reading) pairs. Tiles
    overlap, so the same finding seen from two tiles is one row; each tile keeps at most `max_findings`."""
    t, r = rules.tiles, rules.readout
    seen: list[tuple[str, Point]] = []
    out: list[dict[str, Any]] = []
    for entry, reading in checked:
        for f in reading.get("findings", [])[: t.max_findings]:
            kind = f["kind"] if f["kind"] in t.finding_kinds else "other"
            at = tiles.to_page(tuple(entry["box_mm"]), (f["at"][0], f["at"][1]))  # type: ignore[arg-type]
            if any(k == kind and _near(p, at, r.merge_match_mm) for k, p in seen):
                continue
            seen.append((kind, at))
            what = " ".join(str(f["what"]).split())[: t.max_what_chars]
            x, y = round(at[0]), round(at[1])
            out.append(
                rows.row(
                    f"check_{kind}_{round(at[0] / 10)}_{round(at[1] / 10)}",
                    r.templates.item["wall"].format(kind="check", x=x, y=y),
                    "wall",
                    r.templates.reading["wall_check"],
                    f"{t.finding_kinds[kind]}: {what}" if what else t.finding_kinds[kind],
                    "ORANGE",
                    t.focus.llm_question if entry.get("title") else t.question,
                    target_mm=[
                        round(at[0] - t.finding_box_mm, 1),
                        round(at[1] - t.finding_box_mm, 1),
                        round(at[0] + t.finding_box_mm, 1),
                        round(at[1] + t.finding_box_mm, 1),
                    ],
                    options=[
                        {"key": "confirm", "label": r.options["confirm"]},
                        {"key": "not_there", "label": r.options["not_there"]},
                    ],
                )
            )
    return out
