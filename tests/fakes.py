"""FakeLLM: returns schema-valid JSON without calling any API.

By default it builds the smallest valid instance of the schema ("NOT FOUND" strings, empty arrays,
first enum value, zero integers). Pass canned responses per stage to drive realistic tests.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from render_agent.llm.offline import minimal_instance

__all__ = ["FakeLLM", "minimal_instance", "write_minimal_plan"]


class FakeLLM:
    def __init__(self, canned: dict[str, dict[str, Any]] | None = None) -> None:
        self.canned = canned or {}
        self.calls: list[dict[str, Any]] = []

    def call_json(
        self, stage: str, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        self.calls.append({"stage": stage, "prompt": prompt, "images": images, "schema_name": schema_name})
        if stage in self.canned:
            return copy.deepcopy(self.canned[stage])
        return minimal_instance(schema)


def write_minimal_plan(job_dir: Path, schemas_dir: Path) -> None:
    """The stage-5 artifacts the approval gate reads, empty but valid (for stub-stage tests)."""
    checklist = minimal_instance(json.loads((schemas_dir / "checklist.json").read_text(encoding="utf-8")))
    (job_dir / "checklist.json").write_text(json.dumps(checklist), encoding="utf-8")
    (job_dir / "checklist_validation.json").write_text(json.dumps({"corrections": []}), encoding="utf-8")
    (job_dir / "selections.json").write_text(
        json.dumps({"items": [], "ignored_selects": []}), encoding="utf-8"
    )
