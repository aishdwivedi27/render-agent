"""House specification from evidence.json, schemas/house_spec.json -> house_specification.json.

ROOMS (which wall each opening and fixture is on) is copied by code from the sheet analyses, never
written by the LLM, so layouts reach later stages unchanged. A room drawn on two sheets with
different layouts keeps both entries, each naming the contradiction, which is also listed in
UNCERTAINTIES. The whole specification is validated against the full schema afterwards.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema

from render_agent.errors import PermanentError
from render_agent.pipeline.stages._common import drawing_rules, ingest_info, llm_for, load_schema, read_json
from render_agent.util.fs import atomic_write_json
from render_agent.util.prompts import load_prompt, quote_block, render, user_instructions_block

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

STAGE = "spec"
ARTIFACT = "house_specification.json"
CODE_SECTIONS = ("ROOMS",)  # filled by code, not asked of the LLM


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _layout(room: dict[str, Any]) -> list[tuple[str, tuple[tuple[str, str, str], ...]]]:
    return sorted(
        (w["side"], tuple((i["kind"], _key(i["leads_to"]), i["position"]) for i in w["items"]))
        for w in room["walls"]
    )


def rooms_from_sheets(job_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """(ROOMS entries, contradiction lines) from sheets/sheet-N.json `rooms`."""
    rooms: list[dict[str, Any]] = []
    for f in sorted((job_dir / "sheets").glob("sheet-*.json"), key=lambda p: p.name):
        if f.name.endswith(".notes.json"):
            continue
        sheet = json.loads(f.read_text(encoding="utf-8"))
        page = int(f.stem.split("-")[1])
        for r in sheet.get("rooms", []):
            rooms.append(
                {
                    "dwelling_id": r["dwelling"],
                    "name": r["name"],
                    "source_sheet": sheet.get("sheet_number") or f"page {page}",
                    "view_id": r["view_id"],
                    "page": page,
                    "bbox": r["bbox"],
                    "walls": r["walls"],
                    "contradictions": [],
                }
            )
    notes = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rooms:
        groups.setdefault((_key(r["dwelling_id"]), _key(r["name"])), []).append(r)
    for same in groups.values():
        if len({json.dumps(_layout(r)) for r in same}) > 1:
            sheets = ", ".join(r["source_sheet"] for r in same)
            line = (
                f"{same[0]['dwelling_id']}: {same[0]['name']} has different wall layouts on sheets {sheets}"
            )
            notes.append(line)
            for r in same:
                r["contradictions"].append(line)
    return rooms, notes


def run(ctx: RunContext) -> str:
    job = ctx.job
    evidence = read_json(job.dir / "evidence.json", STAGE)
    prompt = render(
        load_prompt(ctx.settings.path("prompts"), STAGE),
        {
            "EVIDENCE": quote_block("EVIDENCE", json.dumps(evidence, indent=1, ensure_ascii=False)),
            "USER_INSTRUCTIONS": user_instructions_block(ingest_info(ctx).get("instructions", "")),
            "DRAWING_RULES": drawing_rules(ctx),
        },
    )
    full = load_schema(ctx, "house_spec")
    asked = {
        **full,
        "properties": {k: v for k, v in full["properties"].items() if k not in CODE_SECTIONS},
        "required": [k for k in full["required"] if k not in CODE_SECTIONS],
    }
    spec = llm_for(ctx, STAGE).call_json(STAGE, prompt, [], asked, "house_spec")
    rooms, contradictions = rooms_from_sheets(job.dir)
    spec["ROOMS"] = rooms
    spec["UNCERTAINTIES"] = [*spec.get("UNCERTAINTIES", []), *contradictions]
    try:
        jsonschema.validate(spec, full)
    except jsonschema.ValidationError as err:
        raise PermanentError(
            code="SCHEMA_INVALID",
            what_happened=f"Room layouts from the sheets do not fit the specification: {err.message}",
            likely_cause="A sheet analysis was written by an older version or edited by hand.",
            what_to_do="Delete the sheets/ folder of this job and resume it to read the sheets again.",
        ) from err
    atomic_write_json(job.dir / ARTIFACT, spec)
    return ARTIFACT
