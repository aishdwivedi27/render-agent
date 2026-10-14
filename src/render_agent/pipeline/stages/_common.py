"""Helpers shared by the LLM stages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from render_agent.errors import PermanentError
from render_agent.llm.base import LLM, get_llm
from render_agent.llm.offline import OfflineLLM

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext


def llm_for(ctx: RunContext, stage: str) -> LLM:
    """ctx.extras["llm"] (tests inject FakeLLM there), OfflineLLM on a dry run (no spend),
    else the configured LLM for this stage."""
    injected = ctx.extras.get("llm")
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    if ctx.dry_run:
        return OfflineLLM()
    return get_llm(ctx.settings, stage, raw_dir=ctx.job.dir / "llm", job_choice=job_llm_choice(ctx))


def job_llm_choice(ctx: RunContext) -> str | None:
    """The @llm=<name> directive of this job, when the name is one of llm.choices."""
    from render_agent.util.directives import parse_directives

    if not (ctx.job.dir / "ingest.json").is_file():
        return None
    name = parse_directives(ingest_info(ctx).get("instructions", "")).llm
    return name if name in ctx.settings.llm.choices else None


def load_schema(ctx: RunContext, name: str) -> dict[str, Any]:
    return json.loads((ctx.settings.path("schemas") / f"{name}.json").read_text(encoding="utf-8"))


def read_json(path: Path, stage: str) -> Any:
    if not path.is_file():
        raise PermanentError(
            code="ARTIFACT_MISSING",
            what_happened=f"{stage} needs {path.name}, which is missing from the job folder.",
            likely_cause="An earlier stage's output was deleted or moved.",
            what_to_do="Reset the earlier stage in state.json to 'pending' and resume the job.",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_info(ctx: RunContext) -> dict[str, Any]:
    return read_json(ctx.job.dir / "ingest.json", "this stage")  # type: ignore[no-any-return]


def drawing_rules(ctx: RunContext) -> str:
    """knowledge/drawing_reading_rules.md, quoted into the extraction and planning prompts."""
    return (ctx.settings.path("knowledge") / "drawing_reading_rules.md").read_text(encoding="utf-8")
