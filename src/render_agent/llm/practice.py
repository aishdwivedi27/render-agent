"""PracticeLLM: sample readings for a Practice run of a known test drawing (no key, no cost).

The `practice` folder (`paths.practice`, read-only, in the install root) holds test drawing sets, each
as `<set>/input/*.pdf` with sample answers in `<set>/canned/`. When a Practice run's input PDF is one of
them (same sha256), the plan stages answer from `canned/<stage>.<model>.json`, else `canned/<stage>.json`
(stage = the part of the stage name before the first dot, e.g. plan_trace). Anything else gets the
schema-valid placeholder of OfflineLLM, as before. The samples are checked against the schema, so a
practice run behaves like a real reading. No folder, or no matching drawing: OfflineLLM as before.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

import jsonschema

from render_agent.config import Settings
from render_agent.errors import PermanentError
from render_agent.llm.offline import OfflineLLM, minimal_instance
from render_agent.util.fs import sha256_file

log = logging.getLogger("render_agent.practice")
_HASHES: dict[tuple[str, int, int], str] = {}


def _sha(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _HASHES:
        _HASHES[key] = sha256_file(path)
    return _HASHES[key]


def find_canned(settings: Settings, input_hashes: list[str]) -> Path | None:
    """The `canned/` folder of the test drawing set whose PDF matches one of the job's inputs."""
    root = settings.path("practice")
    if not root.is_dir() or not input_hashes:
        return None
    wanted = set(input_hashes)
    for pdf in sorted(root.glob("*/input/*.pdf")):
        canned = pdf.parent.parent / "canned"
        if canned.is_dir() and _sha(pdf) in wanted:
            return canned
    return None


class PracticeLLM(OfflineLLM):
    def __init__(self, canned: Path) -> None:
        self.canned = canned

    def call_json(
        self, stage: str, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        parts = stage.split(".")
        names = [f"{parts[0]}.{parts[1]}.json"] if len(parts) > 1 else []
        for name in [*names, f"{parts[0]}.json"]:
            path = self.canned / name
            if path.is_file():
                answer = json.loads(path.read_text(encoding="utf-8"))
                try:
                    jsonschema.validate(answer, schema)
                except jsonschema.ValidationError as exc:
                    raise PermanentError(
                        code="PRACTICE_SAMPLE_INVALID",
                        what_happened=f"The practice sample {name} does not fit the {schema_name} schema.",
                        likely_cause="The sample readings were not updated after the schema changed.",
                        what_to_do="Ask whoever looks after Render Agent to update the practice samples.",
                    ) from exc
                log.info("practice run: %s answered from %s", stage, name)
                return copy.deepcopy(answer)  # type: ignore[no-any-return]
        return minimal_instance(schema)  # type: ignore[no-any-return]
