"""Checklist via LLM, then validate/checklist_validator.py; writes checklist.json,
checklist_validation.json and selections.json. See docs/SPEC.md sections 4 and 6."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import yaml

from render_agent.pipeline.selections import (
    build_selections,
    colour_schedule_status,
    load_materials,
    write_materials_report,
)
from render_agent.pipeline.stages._common import drawing_rules, ingest_info, llm_for, load_schema, read_json
from render_agent.util.directives import Directives, parse_directives
from render_agent.util.fs import atomic_write_json
from render_agent.util.prompts import load_prompt, quote_block, render, user_instructions_block
from render_agent.validate.checklist_validator import (
    Options,
    declined_views,
    documented_fields,
    load_view_set,
    qualifying_dwellings,
    requested_views,
    share_groups,
    validate,
)

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

STAGE = "checklist"
ARTIFACT = "checklist.json"
log = logging.getLogger("render_agent.s5")


def view_catalog(ctx: RunContext) -> list[dict[str, Any]]:
    """Every drawing view found by stage 2, with its sheet (page) number."""
    out = []
    for n in range(1, int(ingest_info(ctx)["page_count"]) + 1):
        sheet = read_json(ctx.job.dir / "sheets" / f"sheet-{n}.json", STAGE)
        for v in sheet.get("views", []):
            out.append(
                {
                    "view_id": v["view_id"],
                    "page": n,
                    "sheet_title": sheet.get("sheet_title", ""),
                    "label": v["label"],
                    "view_kind": v["view_kind"],
                    "facing": v["facing"],
                    "dwellings_shown": v["dwellings_shown"],
                }
            )
    return out


def schedule_warnings(catalog: list[dict[str, Any]], spec: dict[str, Any]) -> list[str]:
    """A colour/material schedule found on a sheet must reach the spec; say so at the gate if not."""
    found = [
        v
        for v in catalog
        if v["view_kind"] == "schedule"
        and any(w in v["label"].lower() for w in ("colour", "color", "material", "finish"))
    ]
    lines = spec.get("MATERIALS_AND_COLOURS", {}).get("schedule_as_documented", [])
    if found and not lines:
        where = ", ".join(f"{v['label']} (sheet {v['page']})" for v in found)
        return [
            f"A schedule was found on the drawings ({where}) but no colour lines reached the "
            "specification; check colours before approving."
        ]
    return []


def render_limit(ctx: RunContext, view_set: dict[str, Any], d: Directives) -> int:
    limits = [
        ctx.settings.planning.max_planned_renders_per_dwelling,
        int(view_set["limits"]["max_planned_renders_per_dwelling"]),
    ]
    if d.max_images:
        limits.append(d.max_images)  # can lower the limit, never raise it
    return min(limits)


def run(ctx: RunContext) -> str:
    job = ctx.job
    knowledge = ctx.settings.path("knowledge")
    spec = read_json(job.dir / "house_specification.json", STAGE)
    instructions = ingest_info(ctx).get("instructions", "")
    directives = parse_directives(instructions)
    view_set = load_view_set(knowledge)
    limit = render_limit(ctx, view_set, directives)
    catalog = view_catalog(ctx)

    prompt = render(
        load_prompt(ctx.settings.path("prompts"), STAGE),
        {
            "BUDGET": str(limit),
            "VIEW_SET": yaml.safe_dump(
                {"views": view_set["views"], "subdivisions": view_set["subdivisions"]}, sort_keys=False
            ),
            "VIEW_CATALOG": json.dumps(catalog, indent=1, ensure_ascii=False),
            "USER_INSTRUCTIONS": user_instructions_block(instructions),
            "DRAWING_RULES": drawing_rules(ctx),
            "SPEC": quote_block("HOUSE_SPECIFICATION", json.dumps(spec, indent=1, ensure_ascii=False)),
        },
    )
    raw = llm_for(ctx, STAGE).call_json(STAGE, prompt, [], load_schema(ctx, "checklist"), "checklist")

    opts = Options(
        dwellings=[d["dwelling_id"] for d in spec.get("DWELLINGS", []) if d.get("dwelling_id")],
        limit=limit,
        requested=requested_views(instructions, view_set),
        declined=declined_views(instructions, view_set),
        qualifying=qualifying_dwellings(spec, view_set),
        views_only=directives.views,
        dwellings_only=directives.dwellings,
        documented=documented_fields(spec, view_set),
        project_number=str(spec.get("PROJECT", {}).get("project_number", "NOT FOUND")),
        share_groups=share_groups(spec, view_set),
    )
    checklist, corrections = validate(raw, view_set, opts)
    for c in corrections:
        log.info("job %s: checklist correction %s: %s", job.id, c["rule"], c["detail"])
    atomic_write_json(job.dir / ARTIFACT, checklist)
    atomic_write_json(
        job.dir / "checklist_validation.json",
        {
            "limit_per_dwelling": limit,
            "dwellings": opts.dwellings,
            "night_requested": "front_night" in opts.requested,
            "optional_views_requested": sorted(opts.requested),
            "share_groups": {k: [sorted(g) for g in v] for k, v in opts.share_groups.items()},
            "corrections": corrections,
            "ignored_directives": directives.ignored,
            "llm_checklist": raw,
        },
    )
    planned = {r["view_type"] for r in checklist["RENDER_REQUIREMENTS"]}
    selections = build_selections(spec, planned, load_materials(knowledge), directives.select)
    selections["ignored_directives"] = directives.ignored
    selections["warnings"] = schedule_warnings(catalog, spec)
    selections["colour_schedule"] = colour_schedule_status(spec, catalog)
    atomic_write_json(job.dir / "selections.json", selections)
    write_materials_report(job.dir, selections)
    return ARTIFACT
