"""Request and response models for the app API. Responses never carry a key or a stack trace."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Problem(BaseModel):
    what_happened: str
    what_to_do: str


class InputItemOut(BaseModel):
    name: str
    kind: str
    size_mb: float
    startable: bool
    reason: str = ""


class JobSummary(BaseModel):
    id: str
    input_name: str
    status: str
    status_text: str
    stage_text: str
    screen: str
    queued: int | None = None
    can_retry: bool = False
    error: Problem | None = None
    warnings: list[str] = Field(default_factory=list)
    updated: str = ""
    step: int = 1  # 1-5: Start, Approve the plan, Make the renders, Review, Done
    step_name: str = ""
    steps: int = 5
    part: int = 0  # part of the step that is running (0 when the step has no parts running)
    parts: int = 0
    part_name: str = ""
    output_folder: str | None = None


class Warning(BaseModel):
    kind: str
    text: str


class HomeOut(BaseModel):
    inputs: list[InputItemOut]
    jobs: list[JobSummary]
    warnings: list[Warning]
    can_start: bool
    providers: list[str]
    llm_choices: list[str]
    input_folder: str
    views: list[dict[str, Any]] = Field(default_factory=list)  # view_set.yaml views, for the checklist


class StartOptions(BaseModel):
    night: bool | None = None
    streetscape: bool | None = None
    provider: str | None = None
    llm: str | None = None
    dwellings: list[str] = Field(default_factory=list)
    views: list[str] = Field(default_factory=list)
    max_images: int | None = Field(default=None, ge=1, le=16)
    dry_run: bool | None = None


class StartIn(BaseModel):
    name: str
    options: StartOptions = Field(default_factory=StartOptions)
    start_anyway: bool = False


class StartOut(BaseModel):
    job_id: str
    history_folder: str | None
    output_folder: str
    warnings: list[str]


class Progress(BaseModel):
    done: int
    total: int


class JobOut(JobSummary):
    progress: Progress | None = None
    history_folder: str | None = None
    publish_problem: str | None = None
    folders: dict[str, str] | None = None  # Output sub-folder names (view_set.yaml output_folders)


class Correction(BaseModel):
    fact: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=1000)
    suggest_rule: bool = False


class ApproveIn(BaseModel):
    choices: dict[str, str] = Field(default_factory=dict)
    dwellings: str = "all"
    corrections: list[Correction] = Field(default_factory=list)
    render_anyway: list[str] = Field(default_factory=list, max_length=50)  # blocked render ids to make


class ChooseViewsIn(BaseModel):
    views: list[str] = Field(default_factory=list, max_length=50)  # the exact views to plan
    words: str = Field(default="", max_length=1000)  # the designer's own words, kept for the record


class InterpretIn(BaseModel):
    text: str = Field(default="", max_length=1000)


class ReplanIn(BaseModel):
    corrections: list[Correction] = Field(min_length=1, max_length=50)


class DecideIn(BaseModel):
    action: Literal["approve", "flag"]
    category: str | None = None
    note: str = Field(default="", max_length=2000)
    suggest_rule: bool = False
    apply_to_job: bool = False  # also a job correction (later renders of this job)


class SettingsIn(BaseModel):
    keys: dict[str, str] = Field(default_factory=dict)


class TestKeyIn(BaseModel):
    provider: str
    key: str = ""  # empty: test the saved key


class OpenFolderIn(BaseModel):
    which: Literal["output", "history", "input"]
