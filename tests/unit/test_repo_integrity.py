"""Guards the agreed rules and data files. These run from day one."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

from tests.fakes import minimal_instance

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = sorted((ROOT / "schemas").glob("*.json"))


def _walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _walk_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_objects(v)


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_schema_is_strict_mode_compatible(path):
    """OpenAI strict mode: every object lists all properties as required and forbids extras."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    for obj in _walk_objects(schema):
        props = set(obj.get("properties", {}))
        assert set(obj.get("required", [])) == props, f"{path.name}: required != properties"
        assert obj.get("additionalProperties") is False, f"{path.name}: additionalProperties must be false"


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_fake_llm_output_validates(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(minimal_instance(schema), schema)


def test_view_set_matches_agreed_decisions():
    vs = yaml.safe_load((ROOT / "private" / "knowledge" / "view_set.yaml").read_text(encoding="utf-8"))
    assert vs["limits"]["max_planned_renders_per_dwelling"] == 16
    assert vs["limits"]["regenerations_count_toward_limit"] is False
    ids = [v["id"] for v in vs["views"]]
    assert ids[:2] == ["streetscape", "front_day"]  # streetscape is priority 1 for 2+ dwellings
    street = next(v for v in vs["views"] if v["id"] == "streetscape")
    assert street["min_dwellings"] == 2 and not street.get("optional")
    night = next(v for v in vs["views"] if v["id"] == "front_night")
    assert night.get("optional") is True and night["include_when"] == "instruction_file_requests_night"
    assert "pantry" not in ids, "a pantry never gets its own render"
    assert "wir" in ids
    assert next(v for v in vs["views"] if v["id"] == "secondary_bedroom")["max_count"] == 1
    assert vs["subdivisions"]["separate"]["treat_as_independent_houses"] is True
    assert vs["output_folders"]["copy_shared_into_each_dwelling"] is True


def test_prompt_placeholders_are_known():
    known = {
        "USER_INSTRUCTIONS",
        "PAGE_NUMBER",
        "PAGE_COUNT",
        "SOURCE_FILE",
        "PAGE_TEXT",
        "SHEET_ANALYSES",
        "PAGE_TEXTS",
        "EVIDENCE",
        "SPEC",
        "BUDGET",
        "VIEW_CATALOG",
        "VIEW_SET",
        "CHECKLIST",
        "DRAWING_RULES",
        "SELECTIONS",
        "MAX_REFERENCES",
        "VISUALISATION_DEFAULTS",
        # drawing_check.md
        "RENDER_ID",
        "VIEW",
        "STRICTNESS",
        "LEGEND",
        "FACTS",
        "OPENINGS",
        "IDENTITY",
        "FACADE",
    }
    for p in (ROOT / "private" / "prompts").glob("*.md"):
        found = set(re.findall(r"\{\{([A-Z_]+)\}\}", p.read_text(encoding="utf-8")))
        assert found <= known, f"{p.name}: unknown placeholders {found - known}"


def test_no_fixture_references_in_rules_or_code():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_no_fixture_refs.py")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


def test_cli_help_and_doctor_run():
    from render_agent.cli import build_parser

    assert build_parser().parse_args(["doctor"]).command == "doctor"
