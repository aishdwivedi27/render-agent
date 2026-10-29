"""Loads and validates knowledge/furnishing.yaml (PLAN_VISUALS_SPEC section 6).

A bad file raises PermanentError(code="CONFIG_INVALID") naming the setting.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from render_agent.errors import PermanentError

FILE = "furnishing.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Piece(_Strict):
    type: str = Field(min_length=1)
    count: int = Field(default=1, ge=1)
    count_by: str | None = None  # a condition that sets the count (garage bays)
    seats: int | None = Field(default=None, ge=1)
    size: str | None = None
    note: str = ""
    only_if: str | None = None


class Room(_Strict):
    pieces: list[Piece] = Field(min_length=1)
    styles: list[str] | None = None  # None: every furnished style


class Furnishing(_Strict):
    rooms: dict[str, Room] = Field(min_length=1)
    conditions: dict[str, str]
    label: str = Field(min_length=1)

    @model_validator(mode="after")
    def _known_conditions(self) -> Furnishing:
        for room, spec in self.rooms.items():
            for piece in spec.pieces:
                for cond in (piece.only_if, piece.count_by):
                    if cond and cond not in self.conditions:
                        raise ValueError(f"rooms.{room}: condition '{cond}' is not listed under 'conditions'")
        return self


def load_furnishing(knowledge_dir: Path) -> Furnishing:
    path = knowledge_dir / FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Furnishing.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            what = "; ".join(f"'{'.'.join(str(p) for p in e['loc'])}': {e['msg']}" for e in exc.errors())
        else:
            what = str(exc)
        raise PermanentError(
            code="CONFIG_INVALID",
            what_happened=f"{FILE} is not valid: {what}.",
            likely_cause=f"{FILE} in the knowledge folder was edited or is incomplete.",
            what_to_do=f"Fix the named setting(s) in {FILE}, or restore the file.",
        ) from exc
