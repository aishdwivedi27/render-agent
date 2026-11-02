"""Project memory and agent memory folders in the working root (PLAN_VISUALS_SPEC section 13).

memory/projects/<project key>.json holds one project's remembered answers and fixes;
memory/agent_rules.json holds rules for all projects, seeded once from knowledge/agent_rules_seed.yaml.
Memory is data, never instructions. An unreadable memory file never stops a job: it is skipped with a
plain warning (section 15).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from render_agent.config import Settings
from render_agent.util.fs import atomic_write_json, read_text, safe_name

log = logging.getLogger("render_agent.memory")
AGENT_RULES = "agent_rules.json"
PROJECTS = "projects"
SEED = "agent_rules_seed.yaml"
UNREADABLE = (
    "The agent memory file could not be read, so this job runs without remembered rules. "
    "Ask whoever looks after Render Agent to check memory/agent_rules.json."
)
PROJECT_UNREADABLE = (
    "Your answer was applied to this job, but it could not be saved to this project's memory. "
    "Nothing else is affected."
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


def _project_schema(settings: Settings) -> dict[str, Any]:
    return json.loads((settings.path("schemas") / "project_memory.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def project_key(job_dir: Path, dwelling: str) -> tuple[str, str]:
    """(project key, drawing number) for one dwelling of a job: the project number read from the title
    block plus the dwelling; without a number, the drawing file's fingerprint stands in for it."""
    number = "NOT FOUND"
    spec_file = job_dir / "house_specification.json"
    try:
        spec = json.loads(spec_file.read_text(encoding="utf-8")) if spec_file.is_file() else {}
        number = str(spec.get("PROJECT", {}).get("project_number") or "NOT FOUND").strip() or "NOT FOUND"
    except (OSError, ValueError):
        pass
    base = number
    if number.upper() == "NOT FOUND":
        try:
            state = json.loads(read_text(job_dir / "state.json"))
            hashes = sorted(i["sha256"] for i in state.get("inputs", []))
        except (OSError, ValueError, KeyError):
            hashes = []
        base = f"drawing-{hashes[0][:12]}" if hashes else "drawing-unknown"
    return safe_name(f"{base}_{dwelling}".replace(" ", "-").lower(), 80), number


def remember(
    settings: Settings,
    job_dir: Path,
    job_id: str,
    dwelling: str,
    kind: str,
    text: str,
    applies_to: str,
) -> str | None:
    """Adds one item to the project's memory (memory/projects/<key>.json). Returns a plain warning when
    the file cannot be read or written; the job carries on without it (section 15)."""
    key, number = project_key(job_dir, dwelling)
    path = memory_dir(settings) / PROJECTS / f"{key}.json"
    schema = _project_schema(settings)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            jsonschema.validate(data, schema)
        else:
            data = {
                "project_key": key,
                "drawing_number": number,
                "dwelling": dwelling,
                "revisions": [],
                "items": [],
            }
        data["items"].append(
            {
                "id": f"m{len(data['items']) + 1}",
                "kind": kind,
                "text": " ".join(text.split()),
                "applies_to": applies_to,
                "source_job": job_id,
                "created": datetime.now(UTC).isoformat(timespec="seconds"),
                "removed": False,
            }
        )
        jsonschema.validate(data, schema)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, data)
    except (OSError, ValueError, jsonschema.ValidationError) as exc:
        log.warning("project memory not saved: %s", type(exc).__name__)
        return PROJECT_UNREADABLE
    return None
