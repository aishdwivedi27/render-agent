"""Loads and validates knowledge/plan_trace.yaml (PLAN_VISUALS_SPEC section 3).

A bad file raises PermanentError(code="CONFIG_INVALID") naming the setting, like plan_styles.yaml.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from render_agent.errors import PermanentError

FILE = "plan_trace.yaml"
HEX = r"^#[0-9A-Fa-f]{6}$"
ZONES = ("garage", "alfresco", "porch")  # living = every other room; total = the footprint


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Walls(_Strict):
    dark_below: int = Field(gt=0, le=255)
    min_wall_mm: float = Field(gt=0)
    raster_mm: float = Field(gt=0)
    dot_max_mm: float = Field(gt=0)
    max_fill_share: float = Field(gt=0, le=1)
    cluster_gap_mm: float = Field(gt=0)


class Areas(_Strict):
    tolerance: float = Field(gt=0, lt=1)
    veneer_mm: float = Field(ge=0)
    raster_mm_per_px: float = Field(gt=0)
    footprint_max_gap_mm: float = Field(gt=0)
    schedule_labels: dict[str, list[str]]
    outdoor_zones: list[str]
    room_words: dict[str, list[str]]


class Showers(_Strict):
    wet_room_words: list[str] = Field(min_length=1)
    bath_words: list[str] = Field(min_length=1)
    shower_words: list[str] = Field(min_length=1)
    question: str = Field(min_length=1)


class Recess(_Strict):
    min_depth_mm: float = Field(ge=0)
    max_depth_mm: float = Field(gt=0)
    where: str


class Overlay(_Strict):
    alpha: float = Field(gt=0, le=1)
    wall_colour: str = Field(pattern=HEX)
    room_colour: str = Field(pattern=HEX)
    clearance_colour: str = Field(pattern=HEX)
    margin_mm: float = Field(ge=0)
    max_edge_px: int = Field(ge=600)


class TraceRules(_Strict):
    walls: Walls
    areas: Areas
    floor_plan_words: list[str] = Field(min_length=1)
    snap_mm: float = Field(ge=0)
    showers: Showers
    recesses: dict[str, Recess]
    clearance_label: str = Field(min_length=1)
    mirror_min_match: float = Field(gt=0, le=1)
    overlay: Overlay

    def zone_of(self, room_name: str) -> str:
        """'garage', 'alfresco', 'porch' or 'living' for a room name."""
        name = room_name.upper()
        for zone in ZONES:
            if any(w in name for w in self.areas.room_words.get(zone, [])):
                return zone
        return "living"


def _invalid(what: str, fix: str) -> PermanentError:
    return PermanentError(
        code="CONFIG_INVALID",
        what_happened=what,
        likely_cause=f"{FILE} in the knowledge folder was edited or is incomplete.",
        what_to_do=fix,
    )


def load_trace_rules(knowledge_dir: Path) -> TraceRules:
    path = knowledge_dir / FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise _invalid(f"{FILE} could not be read.", "Reinstall Render Agent, or restore the file.") from exc
    except yaml.YAMLError as exc:
        raise _invalid(f"{FILE} is not valid YAML: {exc}", "Fix the line shown.") from exc
    try:
        return TraceRules.model_validate(data)
    except ValidationError as exc:
        names = "; ".join(f"'{'.'.join(str(p) for p in e['loc'])}': {e['msg']}" for e in exc.errors())
        raise _invalid(
            f"Invalid setting(s) in {FILE}: {names}.", f"Fix the named setting(s) in {FILE}."
        ) from exc
