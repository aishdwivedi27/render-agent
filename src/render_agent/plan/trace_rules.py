"""Loads and validates knowledge/plan_trace.yaml (PLAN_VISUALS_SPEC section 3).

A bad file raises PermanentError(code="CONFIG_INVALID") naming the setting, like plan_styles.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
    end_max_mm: float = Field(gt=0)
    keep_share: float = Field(gt=0, le=1)
    dot_aspect_min: float = Field(gt=0, le=1)
    outline_min_jog_mm: float = Field(ge=0)


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
    options: dict[str, str] = Field(min_length=1)  # answer key -> label (bath_only, shower_over)


class Recess(_Strict):
    min_depth_mm: float = Field(ge=0)
    max_depth_mm: float = Field(gt=0)
    min_aspect: float = Field(ge=0)  # length / depth of the pocket
    max_aspect: float = Field(gt=0)
    rooms: list[str]  # words of the room names this kind belongs in
    where: str


class RecessSearch(_Strict):
    min_pocket_mm: float = Field(gt=0)
    max_share: float = Field(gt=0, le=1)
    drawn_rect_kinds: list[str]
    drawn_rect_min_mm: float = Field(gt=0)
    drawn_min_mm: dict[str, float]
    drawn_corner_kinds: list[str]
    drawn_max_share: float = Field(gt=0, le=1)
    drawn_max_ink: float = Field(ge=0, le=1)
    drawn_ink_inset_mm: float = Field(ge=0)
    drawn_rect_cover: float = Field(gt=0, le=1)
    drawn_raster_mm_per_px: float = Field(gt=0)
    drawn_dark_below: int = Field(gt=0, le=255)
    drawn_skip_mm: float = Field(ge=0)
    end_wall_mm: float = Field(ge=0)
    front_line_kinds: list[str]
    front_line_margin_mm: float = Field(ge=0)
    front_line_tol_mm: float = Field(ge=0)
    evidence: dict[str, str]
    sides: list[str] = Field(min_length=4, max_length=4)


class Openings(_Strict):
    min_gap_mm: float = Field(gt=0)
    max_gap_mm: float = Field(gt=0)
    max_thickness_mm: float = Field(gt=0)
    door_max_mm: float = Field(gt=0)
    walkway_min_mm: float = Field(gt=0)
    door_min_mm: float = Field(gt=0)
    walkway_max_mm: float = Field(gt=0)
    across_max_thickness_mm: float = Field(gt=0)
    garage_min_mm: float = Field(gt=0)
    raster_mm_per_px: float = Field(gt=0)
    dark_below: int = Field(gt=0, le=255)
    line_cover: float = Field(gt=0, le=1)
    dash_cover: tuple[float, float]
    dash_transitions: int = Field(ge=1)
    arc_min: float = Field(gt=0, le=1)
    arc_radii: list[float] = Field(min_length=1)
    arc_contrast: float = Field(ge=0, le=1)
    leaf_min: float = Field(ge=0, le=1)
    arc_tolerance_mm: float = Field(ge=0)
    arc_over_cavity: float = Field(gt=0, le=1)
    arc_over_lines: float = Field(gt=0, le=1)
    window_lines_min: int = Field(ge=1)
    cavity_words: list[str]
    garage_words: list[str]
    text_search_mm: float = Field(gt=0)
    cavity_search_mm: float = Field(gt=0)
    cavity_dash_search_mm: float = Field(gt=0)
    code_pattern: str
    code_suffixes: list[str]
    code_search_mm: float = Field(gt=0)
    door_code_height: int = Field(ge=1)
    ends: dict[str, list[str]]
    sides: dict[str, list[str]]
    evidence: dict[str, str]


class Rooms(_Strict):
    tidy_mm: float = Field(ge=0)
    seal_mm: float = Field(ge=0)
    min_area_m2: float = Field(ge=0)
    label_line_mm: float = Field(gt=0)
    label_gap_mm: float = Field(gt=0)
    words: list[str] = Field(min_length=1)
    expand: dict[str, str]
    unlabelled_name: str = Field(min_length=1)


class OverlayCheck(_Strict):
    margin_mm: float = Field(ge=0)
    max_rows: int = Field(ge=1)
    merge_mm: float = Field(ge=0)
    look_mm: float = Field(gt=0)
    questions: dict[str, str]
    evidence: dict[str, str]


class Overlay(_Strict):
    alpha: float = Field(gt=0, le=1)
    wall_colour: str = Field(pattern=HEX)
    room_colour: str = Field(pattern=HEX)
    clearance_colour: str = Field(pattern=HEX)
    margin_mm: float = Field(ge=0)
    max_edge_px: int = Field(ge=600)


QUESTIONS = (
    "default",
    "alternative",
    "off_walls",
    "differ",
    "only_one",
    "not_read",
    "ambiguous",
    "unreadable",
    "unlabelled",
    "window_code",
)
OPTIONS = ("confirm", "alternative", "use_model", "not_there", "schedule", "same", "different")


class Scoring(_Strict):
    unsure_takes_lower: bool
    ambiguous_best_guess: Literal["model", "code"]


class Templates(_Strict):
    item: dict[str, str]
    reading: dict[str, str]
    evidence: dict[str, str]
    sides: dict[str, list[str]]


class Readout(_Strict):
    """Readout table rules (PLAN_VISUALS_SPEC section 4)."""

    overlay_tolerance_mm: float = Field(gt=0)
    merge_match_mm: float = Field(gt=0)
    batch_size: int = Field(ge=1, le=20)
    code_label: str = Field(min_length=1)
    main_kinds: list[str] = Field(min_length=1)
    other_title: str = Field(min_length=1)
    scoring: Scoring
    templates: Templates
    kind_labels: dict[str, str]
    recess_labels: dict[str, str]
    questions: dict[str, str]
    options: dict[str, str]

    @model_validator(mode="after")
    def _complete(self) -> Readout:
        for name, have, need in (
            ("questions", self.questions, QUESTIONS),
            ("options", self.options, OPTIONS),
        ):
            missing = [k for k in need if not have.get(k)]
            if missing:
                raise ValueError(f"readout.{name} needs {', '.join(missing)}")
        return self


class TraceRules(_Strict):
    walls: Walls
    areas: Areas
    floor_plan_words: list[str] = Field(min_length=1)
    snap_mm: float = Field(ge=0)
    showers: Showers
    recesses: dict[str, Recess]
    recess_search: RecessSearch
    openings: Openings
    rooms: Rooms
    overlay_check: OverlayCheck
    clearance_label: str = Field(min_length=1)
    mirror_min_match: float = Field(gt=0, le=1)
    overlay: Overlay
    readout: Readout

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
