"""Start the plan check (gate_plan_start; PLAN_VISUALS_SPEC section 2).

After the drawings are read an app job waits in AWAITING_PLAN_START: the designer picks the readout
model (Claude, OpenAI or both) or "Exterior renders only (no plans)", which skips every plan stage and
runs today's path. The choice is saved to plan/outputs_chosen.json and written as `@readout_llm` /
`@plans_mode` into the job's instructions (ingest.json and input/start_options.txt), like `@views`.
Those directives in the designer's instruction file pre-select the screen. Jobs run from the CLI (no
designer folders) pass at once, as renders only unless their instruction file says `@plans_mode=plans`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from render_agent.config import Settings
from render_agent.output.costs import llm_cost, money
from render_agent.pipeline.job import PLAN_STAGES, Job
from render_agent.pipeline.view_choice import OPTIONS_FILE, needs_choice
from render_agent.util.directives import parse_directives, with_directive_line
from render_agent.util.fs import atomic_write_json, atomic_write_text

CHOSEN = "plan/outputs_chosen.json"
STAGE = "gate_plan_start"
SKIP_RENDERS_ONLY = "Exterior renders only (no plans)"
READOUT_SHEETS = ("floor_plan", "mixed")  # sheets a floor plan readout reads
PROVIDER = {"claude": "anthropic", "openai": "openai"}
LABEL = {"claude": "Claude", "openai": "OpenAI", "both": "Claude + OpenAI"}
INFO = (
    "Claude, OpenAI or both read your drawing and list what they find. Both: each model reads "
    "separately and any disagreement becomes an Orange question for you, for about double the "
    "reading cost. This does not change how the plans are drawn."
)

__all__ = ["CHOSEN", "INFO", "choose", "is_chosen", "needs_choice", "options", "pass_without_screen"]


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}  # unreadable: treated as not chosen, so the screen is shown again


def chosen(job_dir: Path) -> dict[str, Any]:
    data = _read(job_dir / CHOSEN)
    return data if isinstance(data, dict) else {}


def is_chosen(job_dir: Path) -> bool:
    return bool(chosen(job_dir).get("plans_mode"))


def readout_text(job_dir: Path) -> str:
    """For the job card: 'Claude', 'OpenAI', 'Claude + OpenAI', 'Exterior renders only' or ''."""
    c = chosen(job_dir)
    if c.get("plans_mode") == "renders_only":
        return "Exterior renders only"
    return LABEL.get(str(c.get("readout_llm")), "") if c.get("plans_mode") == "plans" else ""


def _choice_name(settings: Settings, option: str) -> str | None:
    """The first llm.choices name for the option's provider (config decides the model)."""
    default = settings.llm.choices.get(settings.plans.readout_llm_default)
    if default is not None and default.provider == PROVIDER[option]:
        return settings.plans.readout_llm_default
    return next((k for k, c in settings.llm.choices.items() if c.provider == PROVIDER[option]), None)


def _models(option: str) -> tuple[str, ...]:
    return ("claude", "openai") if option == "both" else (option,)


def choice_names(settings: Settings, option: str) -> list[str | None]:
    return [_choice_name(settings, o) for o in _models(option)]


def _readout_sheets(job_dir: Path) -> int:
    n = 0
    for p in (job_dir / "sheets").glob("sheet-*.json"):
        if p.name.endswith(".notes.json"):
            continue
        n += _read(p).get("sheet_type") in READOUT_SHEETS
    return max(n, 1)


def _cost_text(settings: Settings, names: list[str], sheets: int) -> str:
    est = settings.plans.readout_estimate_tokens
    usage = {"input_tokens": est.input * sheets, "output_tokens": est.output * sheets}
    total = {"cost_usd": 0.0, "price_missing": False, "no_usage": 0}
    for name in names:
        cost = llm_cost(usage, settings.pricing.get(settings.llm.choices[name].model))
        if cost is None:
            total["price_missing"] = True
        else:
            total["cost_usd"] += cost
    text = money(total)
    return text if text == "price not set" else f"about {text}"


def _from_instructions(job_dir: Path) -> dict[str, str | None]:
    d = parse_directives(str(_read(job_dir / "ingest.json").get("instructions", "")))
    return {"readout_llm": d.readout_llm, "plans_mode": d.plans_mode}


def _default_option(settings: Settings) -> str:
    default = settings.llm.choices.get(settings.plans.readout_llm_default)
    return "openai" if default is not None and default.provider == "openai" else "claude"


def options(job_dir: Path, settings: Settings) -> dict[str, Any]:
    """The readout options with their estimated reading cost, and what starts selected (an earlier
    choice, else the instruction file's directives, else plans.readout_llm_default)."""
    sheets = _readout_sheets(job_dir)
    out = []
    for option in ("claude", "openai", "both"):
        names = choice_names(settings, option)
        missing = [LABEL[o] for o, n in zip(_models(option), names, strict=True) if n is None]
        out.append(
            {
                "id": option,
                "label": LABEL[option],
                "available": not missing,
                "why_not": f"no {' or '.join(missing)} model in llm.choices (config.yaml)" if missing else "",
                "cost": _cost_text(settings, [n for n in names if n], sheets) if not missing else "",
            }
        )
    before = chosen(job_dir)
    asked = _from_instructions(job_dir)
    readout = before.get("readout_llm") or asked["readout_llm"] or _default_option(settings)
    mode = before.get("plans_mode") or asked["plans_mode"] or "plans"
    return {
        "options": out,
        "readout_llm": readout,
        "plans_mode": mode,
        "info": INFO,
        "sheets": sheets,
        "from_file": {k: v for k, v in asked.items() if v},
    }


def _write_directives(job_dir: Path, lines: dict[str, str]) -> None:
    info = _read(job_dir / "ingest.json")
    opts = job_dir / "input" / OPTIONS_FILE
    opts_text = opts.read_text(encoding="utf-8") if opts.is_file() else None
    for key, line in lines.items():
        if info:
            info["instructions"] = with_directive_line(str(info.get("instructions", "")), key, line)
        if opts_text is not None:
            opts_text = with_directive_line(opts_text, key, line)
    if info:
        atomic_write_json(job_dir / "ingest.json", info)
    if opts_text is not None:
        atomic_write_text(opts, opts_text)


def choose(job: Job, settings: Settings, plans_mode: str, readout_llm: str, by: str) -> dict[str, Any]:
    """Records the choice; renders only marks every later plan stage skipped."""
    plans = plans_mode == "plans"
    names = [n for n in choice_names(settings, readout_llm) if n] if plans else []
    data = {
        "plans_mode": plans_mode,
        "readout_llm": readout_llm if plans else None,
        "readout_models": names,
        "by": by,
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (job.dir / CHOSEN).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(job.dir / CHOSEN, data)
    lines = {"plans_mode": f"@plans_mode={plans_mode}"}
    lines["readout_llm"] = f"@readout_llm={readout_llm}" if plans else ""
    _write_directives(job.dir, lines)
    if not plans:
        for name in PLAN_STAGES:
            if name != STAGE and job.stage(name)["status"] not in ("done", "skipped"):
                job.stage(name).update(status="skipped", reason=SKIP_RENDERS_ONLY)
        job.save()
    return data


def pass_without_screen(job: Job, settings: Settings) -> None:
    """A CLI job: its instruction file decides; renders only unless it says @plans_mode=plans."""
    asked = _from_instructions(job.dir)
    mode = asked["plans_mode"] or "renders_only"
    readout = asked["readout_llm"] or _default_option(settings)
    choose(job, settings, mode, readout, by="instruction file (CLI)")
