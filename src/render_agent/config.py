"""Loads config.yaml and .env and validates them into typed settings.

Unknown keys, bad values and malformed model IDs raise PermanentError(code="CONFIG_INVALID") naming
the setting, so `doctor` and the agent can refuse to start with a clear message (spec section 9).
Every path is resolved relative to the install root. Secrets come only from .env / the environment.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, ValidationError

from render_agent.errors import PermanentError

MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*[a-z0-9]$")
KEY_ENV_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "muapi": "MUAPI_API_KEY"}
SECRET_ENV_VARS = (*KEY_ENV_VARS.values(), "SMTP_USERNAME", "SMTP_PASSWORD")


def _check_model_id(value: str) -> str:
    if not MODEL_ID_RE.match(value):
        raise ValueError(f"'{value}' is not a valid model ID (lowercase letters, digits, '.', '-', '_')")
    return value


ModelId = Annotated[str, AfterValidator(_check_model_id)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Paths(_Strict):
    inbox: Path
    jobs: Path
    failed: Path
    knowledge: Path
    prompts: Path
    schemas: Path
    logs: Path


class Watcher(_Strict):
    poll_seconds: int = Field(gt=0)
    stable_seconds: int = Field(ge=0)
    min_free_disk_gb: float = Field(ge=0)


class StageLLM(_Strict):
    provider: Literal["openai", "anthropic"]
    model: ModelId


class LLM(_Strict):
    provider: Literal["openai", "anthropic"]
    model: ModelId
    reasoning_effort: Literal["minimal", "low", "medium", "high"] = "medium"
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    retries: int = Field(ge=0)
    per_stage: dict[str, StageLLM] = Field(default_factory=dict)


class OpenAIImages(_Strict):
    model: ModelId
    size: str = Field(pattern=r"^\d+x\d+$|^auto$")
    quality: Literal["low", "medium", "high", "auto"]


class MuapiImages(_Strict):
    edit_model: ModelId
    generate_model: ModelId
    timeout_seconds: int = Field(gt=0)


class Images(_Strict):
    provider: Literal["openai", "muapi", "mock"]
    openai: OpenAIImages
    muapi: MuapiImages
    references_per_render: int = Field(ge=0)
    concurrency: int = Field(ge=1)
    retries: int = Field(ge=0)


class Planning(_Strict):
    max_planned_renders_per_dwelling: int = Field(ge=1, le=16)
    auto_retries_after_failed_check: int = Field(ge=0)
    render_review_views: bool


class Gate(_Strict):
    approval_required: Literal[True]
    reminder_after_working_days: int = Field(ge=1)


class DrawingCheck(_Strict):
    enabled: bool
    strictness: Literal["standard", "strict"]


class Ingest(_Strict):
    page_dpi: int = Field(ge=72, le=600)
    max_pdf_mb: int = Field(gt=0)


class Review(_Strict):
    host: Literal["127.0.0.1"]
    port: int = Field(ge=1024, le=65535)
    open_browser: bool


class Email(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    enabled: bool
    smtp_host: str
    smtp_port: int
    from_: str = Field(alias="from")
    to: list[str]


class Notify(_Strict):
    windows_toast: bool
    email: Email


class Settings(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    paths: Paths
    watcher: Watcher
    llm: LLM
    images: Images
    planning: Planning
    gate: Gate
    drawing_check: DrawingCheck
    ingest: Ingest
    review: Review
    notify: Notify

    root: Path = Field(exclude=True)
    config_file: Path = Field(exclude=True)
    secrets: dict[str, SecretStr] = Field(default_factory=dict, exclude=True, repr=False)

    def path(self, name: str) -> Path:
        """An absolute path for a `paths:` entry, resolved against the install root."""
        p = Path(getattr(self.paths, name))
        return p if p.is_absolute() else self.root / p

    def secret(self, env_var: str) -> str | None:
        value = self.secrets.get(env_var)
        return value.get_secret_value() if value and value.get_secret_value() else None

    def required_key_vars(self) -> list[str]:
        """Env vars that must hold a key for the configured providers (mock needs none)."""
        providers: set[str] = {self.llm.provider, *(s.provider for s in self.llm.per_stage.values())}
        if self.images.provider != "mock":
            providers.add(self.images.provider)
        return sorted(KEY_ENV_VARS[p] for p in providers)


def install_root() -> Path:
    """The folder that holds config.yaml: RENDER_AGENT_HOME if set, else the working directory."""
    home = os.environ.get("RENDER_AGENT_HOME")
    return Path(home) if home else Path.cwd()


def _setting_name(loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in loc) or "(top level)"


def _load_secrets(root: Path) -> dict[str, SecretStr]:
    """Environment variables win over .env, so a host can override the file."""
    file_values = dotenv_values(root / ".env") if (root / ".env").exists() else {}
    out: dict[str, SecretStr] = {}
    for var in SECRET_ENV_VARS:
        value = os.environ.get(var) or file_values.get(var) or ""
        out[var] = SecretStr(value.strip())
    return out


def load_config(root: Path | None = None) -> Settings:
    root = (root or install_root()).resolve()
    cfg_path = root / "config.yaml"
    if not cfg_path.exists():
        raise PermanentError(
            code="CONFIG_MISSING",
            what_happened=f"No config.yaml found in {root}.",
            likely_cause="The install step that copies config.example.yaml has not been run.",
            what_to_do="Copy config.example.yaml to config.yaml in the install folder.",
        )
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PermanentError(
            code="CONFIG_INVALID",
            what_happened=f"config.yaml is not valid YAML: {exc}",
            likely_cause="A typing mistake in config.yaml (indentation or a missing colon).",
            what_to_do="Fix the line shown, or compare with config.example.yaml.",
        ) from exc
    if not isinstance(data, dict):
        raise PermanentError(code="CONFIG_INVALID", what_happened="config.yaml must be a mapping.")
    try:
        settings = Settings(**data, root=root, config_file=cfg_path, secrets=_load_secrets(root))
    except ValidationError as exc:
        errors = sorted(exc.errors(), key=lambda e: e["type"] != "extra_forbidden")
        problems = [
            f"'{_setting_name(e['loc'])}': "
            + ("unknown setting" if e["type"] == "extra_forbidden" else e["msg"])
            for e in errors
        ]
        names = ", ".join(f"'{_setting_name(e['loc'])}'" for e in errors)
        raise PermanentError(
            code="CONFIG_INVALID",
            what_happened="Invalid setting(s) in config.yaml: " + "; ".join(problems) + ".",
            likely_cause="A typo, an unsupported value, or a setting that does not exist.",
            what_to_do=f"Fix {names} in config.yaml (compare with config.example.yaml), then retry.",
        ) from exc
    _check_provider_models(settings)
    return settings


def _check_provider_models(s: Settings) -> None:
    """A Claude model ID only makes sense with the anthropic provider, and the reverse."""
    pairs = [("llm", s.llm.provider, s.llm.model)]
    pairs += [(f"llm.per_stage.{k}", v.provider, v.model) for k, v in s.llm.per_stage.items()]
    for name, provider, model in pairs:
        if (provider == "anthropic") != model.startswith("claude-"):
            raise PermanentError(
                code="CONFIG_INVALID",
                what_happened=f"Setting '{name}.model' ('{model}') does not belong to provider '{provider}'.",
                likely_cause="The provider was changed without changing the model, or vice versa.",
                what_to_do=f"Set '{name}.provider' and '{name}.model' to a matching pair.",
            )
