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
    code_width_tol_mm: float = Field(ge=0)  # a window code's width and the measured gap may differ this much
    leaf_min_mm: float = Field(
        gt=0
    )  # door leaf sizes written beside a door (870, 720, 1020) lie between these
    leaf_max_mm: float = Field(gt=0)
    leaf_search_mm: float = Field(gt=0)
    leaf_over_mm: tuple[float, float]  # a door's opening is its leaf plus an amount in this range
    bifold_pattern: str  # the label of a pair of bi-fold leaves: "2/1020"
    bifold_words: list[str]
    bifold_label_mm: float = Field(gt=0)  # the word "Bi-folds" is this close to the label
    bifold_search_mm: float = Field(gt=0)  # gaps this close to a bi-fold label are its leaves
    bifold_join_mm: float = Field(ge=0)  # leaves touch within this
    ends: dict[str, list[str]]
    sides: dict[str, list[str]]
    evidence: dict[str, str]
    size_evidence: dict[str, str]


class Rooms(_Strict):
    tidy_mm: float = Field(ge=0)
    seal_mm: float = Field(ge=0)
    min_area_m2: float = Field(ge=0)
    label_line_mm: float = Field(gt=0)
    label_gap_mm: float = Field(gt=0)
    words: list[str] = Field(min_length=1)
    expand: dict[str, str]
    unlabelled_name: str = Field(min_length=1)
    split_open_plan: bool  # one space with several labels is cut into a zone per label
    split_min_area_m2: float = Field(ge=0)  # a zone smaller than this means the space stays whole


class OverlayCheck(_Strict):
    margin_mm: float = Field(ge=0)
    max_rows: int = Field(ge=1)
    merge_mm: float = Field(ge=0)
    look_mm: float = Field(gt=0)
    questions: dict[str, str]
    evidence: dict[str, str]


class Angled(_Strict):
    """Walls drawn off square: a staircase of short raster steps along one straight line."""

    min_steps: int = Field(ge=2)
    step_max_mm: float = Field(gt=0)  # each step of the staircase is at most this tall (or wide)
    min_run_mm: float = Field(gt=0)
    line_tol_mm: float = Field(ge=0)  # the steps' midpoints lie this close to one straight line
    min_angle_deg: float = Field(gt=0, lt=45)  # nearer than this to square is raster noise
    max_rows: int = Field(ge=1)
    question: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class FocusGroup(_Strict):
    name: str = Field(min_length=1)
    words: list[str] = Field(min_length=1)  # rooms whose name holds one of these belong to the group
    link: bool  # small rooms joined to a member by a door (a vanity, a hall) belong to it too


class Focus(_Strict):
    """Close-up areas: rooms and openings that sit tightly together, cut at higher detail (section 3a)."""

    enabled: bool
    px_mm: float = Field(gt=0)  # detail of a close-up: mm per pixel
    pad_mm: float = Field(ge=0)  # drawing kept around the group's rooms and their openings
    min_mm: float = Field(gt=0)  # the crop is at least this big on each side
    max_mm: float = Field(gt=0)
    max_areas: int = Field(ge=1)
    link_max_area_m2: float = Field(gt=0)
    hotspots: bool  # items that are not Green and fall outside every group get an area of their own
    groups: list[FocusGroup]
    llm: bool  # a model also suggests where to look closer (plan_focus); code checks every box it returns
    llm_max_areas: int = Field(ge=1)
    llm_question: str = Field(min_length=1)


class Tiles(_Strict):
    """Cropped views of the plan for the readout model (PLAN_VISUALS_SPEC section 3a)."""

    enabled: bool
    px_mm: float = Field(gt=0)  # detail of a tile: mm per pixel (6.35 is 400 dpi at 1:100)
    plan_margin_mm: float = Field(ge=0)  # drawing kept around the walls
    tile_mm: float = Field(gt=0)  # side of a zone tile
    overlap_mm: float = Field(ge=0)
    min_wall_mm: float = Field(ge=0)  # a tile with less wall inside is not cut
    max_tiles: int = Field(ge=1)
    max_edge_px: int = Field(ge=400)  # longest edge of any tile or crop given to a model
    send_plan_crop: bool  # the reading model also gets the plan cropped from the sheet, at more detail
    plan_crop_max_edge_px: int = Field(ge=800)  # longest edge of that crop
    verify: bool  # one model check per overlay tile (plan_verify)
    max_findings: int = Field(ge=1)  # findings kept per tile
    max_what_chars: int = Field(ge=20)  # characters of the model's sentence kept in a row
    finding_box_mm: float = Field(gt=0)  # half-size of the box a finding highlights on the overlay
    wall_alpha: float = Field(gt=0, le=1)
    marker_colour: str = Field(pattern=HEX)
    finding_kinds: dict[str, str]  # a finding kind the model may return -> the row wording
    focus: Focus
    verify_max_calls: int = Field(ge=1)  # model checks per dwelling (tiles and close-ups together)
    question: str = Field(min_length=1)


class Kitchen(_Strict):
    """Kitchen detail read by code (PLAN_VISUALS_SPEC section 3b): the free-standing island, the fridge
    space and the dishwasher provision in a kitchen or pantry zone. Cooktop and sink are drawn symbols the
    readout model reads."""

    zone_words: list[str] = Field(min_length=1)  # a zone whose name holds one of these is a kitchen zone
    fridge_words: list[str]
    fridge_half_mm: float = Field(gt=0)  # half-size of the box the fridge mark stands for
    dishwasher_words: list[str]
    provision_words: list[str]
    word_pair_mm: float = Field(gt=0)  # "DW" and "PROV" this close are one label
    raster_mm_per_px: float = Field(gt=0)
    dark_below: int = Field(gt=0, le=255)
    wall_clear_mm: float = Field(ge=0)  # ink this close to a wall is the wall's own, not joinery
    line_tol_mm: float = Field(ge=0)  # the four edges of a box agree within this
    island_length_mm: tuple[float, float]
    island_depth_mm: tuple[float, float]
    island_clear_mm: float = Field(ge=0)  # free all round: no wall within this of any side
    against_wall_mm: float = Field(gt=0)  # an item this close to a wall is "against" it
    evidence: dict[str, str]
    sides: dict[str, str]  # probe direction -> the side's name as the table writes it


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
    "zone",
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
    kitchen_words: list[str] = Field(min_length=1)  # fixtures in rooms with these words join the main table
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
    angled: Angled
    kitchen: Kitchen
    tiles: Tiles
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
