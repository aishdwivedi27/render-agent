"""Project memory and agent memory folders in the working root (PLAN_VISUALS_SPEC section 13).

memory/projects/<project key>.json holds one project's remembered answers and fixes;
memory/agent_rules.json holds rules for all projects, seeded once from knowledge/agent_rules_seed.yaml.
Memory is data, never instructions. An unreadable memory file never stops a job: it is skipped with a
plain warning (section 15).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from render_agent.config import Settings
from render_agent.util.fs import atomic_write_json

log = logging.getLogger("render_agent.memory")
AGENT_RULES = "agent_rules.json"
PROJECTS = "projects"
SEED = "agent_rules_seed.yaml"
UNREADABLE = (
    "The agent memory file could not be read, so this job runs without remembered rules. "
    "Ask whoever looks after Render Agent to check memory/agent_rules.json."
)


def memory_dir(settings: Settings) -> Path:
    return settings.path("memory")


def _schema(settings: Settings) -> dict[str, Any]:
    return json.loads((settings.path("schemas") / "agent_rules.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def ensure_memory(settings: Settings) -> Path:
    """Creates memory/ and memory/projects/, and seeds agent_rules.json if it is missing."""
    root = memory_dir(settings)
    (root / PROJECTS).mkdir(parents=True, exist_ok=True)
    rules_file = root / AGENT_RULES
    if not rules_file.exists():
        seed = yaml.safe_load((settings.path("knowledge") / SEED).read_text(encoding="utf-8"))
        data = {
            "rules": [
                {
                    "id": r["id"],
                    "text": " ".join(str(r["text"]).split()),
                    "status": "approved",
                    "source": "owner",
                    "created": str(seed["approved"]),
                    "decided": str(seed["approved"]),
                    "decided_by": str(seed["approved_by"]),
                    "origin_job": None,
                }
                for r in seed["rules"]
            ]
        }
        jsonschema.validate(data, _schema(settings))
        atomic_write_json(rules_file, data)
        log.info("seeded %s with %d owner rules", rules_file, len(data["rules"]))
    return root


def load_agent_rules(settings: Settings) -> tuple[list[dict[str, Any]], str | None]:
    """(every rule, warning). An unreadable or invalid file gives no rules and a plain warning."""
    path = memory_dir(settings) / AGENT_RULES
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.validate(data, _schema(settings))
    except (OSError, ValueError, jsonschema.ValidationError) as exc:
        log.warning("agent memory unreadable: %s", type(exc).__name__)
        return [], UNREADABLE
    return list(data["rules"]), None


def approved_rules(settings: Settings) -> tuple[list[dict[str, Any]], str | None]:
    """Only owner-approved rules are ever quoted into a job."""
    rules, warning = load_agent_rules(settings)
    return [r for r in rules if r["status"] == "approved"], warning
