"""House specification from evidence.json, schemas/house_spec.json -> house_specification.json."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from render_agent.pipeline.stages._common import drawing_rules, ingest_info, llm_for, load_schema, read_json
from render_agent.util.fs import atomic_write_json
from render_agent.util.prompts import load_prompt, quote_block, render, user_instructions_block

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

STAGE = "spec"
ARTIFACT = "house_specification.json"


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
    spec = llm_for(ctx, STAGE).call_json(STAGE, prompt, [], load_schema(ctx, "house_spec"), "house_spec")
    atomic_write_json(job.dir / ARTIFACT, spec)
    return ARTIFACT
