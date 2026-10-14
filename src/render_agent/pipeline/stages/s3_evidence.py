"""Evidence: merge sheet analyses + page text, schemas/evidence.json -> evidence.json."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from render_agent.pipeline.stages._common import drawing_rules, ingest_info, llm_for, load_schema, read_json
from render_agent.util.fs import atomic_write_json
from render_agent.util.prompts import load_prompt, quote_block, render, user_instructions_block

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

STAGE = "evidence"
ARTIFACT = "evidence.json"


def run(ctx: RunContext) -> str:
    job = ctx.job
    info = ingest_info(ctx)
    sheets, texts = [], []
    for page in info["pages"]:
        n = page["page"]
        sheets.append({"page": n, **read_json(job.dir / "sheets" / f"sheet-{n}.json", STAGE)})
        body = (job.dir / page["text"]).read_text(encoding="utf-8")
        texts.append(f"--- page {n} ({page['source_file']}) ---\n{body if body.strip() else '(scan)'}")
    prompt = render(
        load_prompt(ctx.settings.path("prompts"), STAGE),
        {
            "SHEET_ANALYSES": quote_block("SHEET_ANALYSES", json.dumps(sheets, indent=1, ensure_ascii=False)),
            "PAGE_TEXTS": quote_block("PAGE_TEXTS", "\n\n".join(texts)),
            "USER_INSTRUCTIONS": user_instructions_block(info.get("instructions", "")),
            "DRAWING_RULES": drawing_rules(ctx),
        },
    )
    evidence = llm_for(ctx, STAGE).call_json(STAGE, prompt, [], load_schema(ctx, STAGE), STAGE)
    atomic_write_json(job.dir / ARTIFACT, evidence)
    return ARTIFACT
