"""Loads and validates knowledge/plan_styles.yaml (PLAN_VISUALS_SPEC sections 2, 7 and 11).

A bad file raises PermanentError(code="CONFIG_INVALID") naming the setting; the wet-tile colour is
checked against its own contrast range so `doctor` can name it (section 15).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from render_agent.errors import PermanentError
from render_agent.util.colour import contrast, hex_to_rgb

FILE = "plan_styles.yaml"
WHITE = (255, 255, 255)
HEX = r"^#[0-9A-Fa-f]{6}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Style(_Strict):
    name: str = Field(min_length=1)
    description: str
    thumbnail: str
    furnished: bool
    zoned_view: bool = False
    pdf: Literal["vector", "image"]
    cutaway_look: Literal["soft_shadows", "white_clay", "textured"]
    garage_cars: bool = False


class Cutaway(_Strict):
    projection: Literal["orthographic"]
    elevation_deg: float
    elevation_min_deg: float
    elevation_max_deg: float
    azimuth_deg: float = Field(ge=0, lt=360)
    wall_cut_mm: int = Field(gt=0)
    windows: str
    exterior_faces: str
    hide: list[str]
    background: str = Field(pattern=HEX)
    pdf: Literal["vector", "image"]

    @model_validator(mode="after")
    def _angle_in_range(self) -> Cutaway:
        if not self.elevation_min_deg <= self.elevation_deg <= self.elevation_max_deg:
            raise ValueError(
                f"elevation_deg {self.elevation_deg} is outside "
                f"{self.elevation_min_deg}..{self.elevation_max_deg}"
            )
        return self


class Exports(_Strict):
    formats: list[Literal["png", "jpg", "pdf"]] = Field(min_length=1)
    png_width_px: int = Field(ge=1000)
    jpg_quality: int = Field(ge=50, le=100)
    jpg_background: str = Field(pattern=HEX)


class FileNames(_Strict):
    plan: str
    plan_zones: str
    cutaway: str


class Folders(_Strict):
    root: str = Field(min_length=1)
    style: str
    draft: str
    final: str


class WetTile(_Strict):
    colour: str = Field(pattern=HEX)
    contrast_min: float = Field(gt=1)
    contrast_max: float = Field(gt=1)
    prompt_wording: str = Field(min_length=1)

    @property
    def contrast_with_white(self) -> float:
        return contrast(hex_to_rgb(self.colour), WHITE)

    @property
    def in_range(self) -> bool:
        return self.contrast_min <= self.contrast_with_white <= self.contrast_max


class PlanStyles(_Strict):
    styles: dict[str, Style] = Field(min_length=1)
    cutaway: Cutaway
    exports: Exports
    file_names: FileNames
    folders: Folders
    wet_tile: WetTile


def _invalid(what: str, fix: str) -> PermanentError:
    return PermanentError(
        code="CONFIG_INVALID",
        what_happened=what,
        likely_cause=f"{FILE} in the knowledge folder was edited or is incomplete.",
        what_to_do=fix,
    )


def load_plan_styles(knowledge_dir: Path, check_tile: bool = True) -> PlanStyles:
    """The validated file; `check_tile=False` lets doctor report the wet tile on its own line."""
    path = knowledge_dir / FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise _invalid(f"{FILE} could not be read.", "Reinstall Render Agent, or restore the file.") from exc
    except yaml.YAMLError as exc:
        raise _invalid(f"{FILE} is not valid YAML: {exc}", "Fix the line shown.") from exc
    try:
        styles = PlanStyles.model_validate(data)
    except ValidationError as exc:
        names = "; ".join(f"'{'.'.join(str(p) for p in e['loc'])}': {e['msg']}" for e in exc.errors())
        raise _invalid(
            f"Invalid setting(s) in {FILE}: {names}.", f"Fix the named setting(s) in {FILE}."
        ) from exc
    if check_tile and not styles.wet_tile.in_range:
        raise wet_tile_error(styles.wet_tile)
    return styles


def wet_tile_error(tile: WetTile) -> PermanentError:
    return _invalid(
        f"Setting 'wet_tile.colour' ({tile.colour}) has contrast {tile.contrast_with_white:.2f}:1 against "
        f"white; it must be between {tile.contrast_min} and {tile.contrast_max}.",
        f"Choose a mid-grey for 'wet_tile.colour' in {FILE}: darker than white fixtures, lighter "
        "than charcoal.",
    )
