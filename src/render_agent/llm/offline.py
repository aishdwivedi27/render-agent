"""OfflineLLM: schema-valid placeholder answers with no network call, for --dry-run (no spend).

Every string is "NOT FOUND", arrays are empty and enums take their first value, so a dry run
exercises the pipeline end to end without inventing architecture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def minimal_instance(schema: dict[str, Any]) -> Any:
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: minimal_instance(v) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return []
    if t == "string":
        return "NOT FOUND"
    if t in ("integer", "number"):
        return 0
    if t == "boolean":
        return False
    return None


class OfflineLLM:
    def call_json(
        self, stage: str, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        return minimal_instance(schema)  # type: ignore[no-any-return]
