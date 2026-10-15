r"""Loads config.yaml and .env and validates them into typed settings.

Unknown keys, bad values and malformed model IDs raise PermanentError(code="CONFIG_INVALID") naming
the setting, so `doctor` and the agent can refuse to start with a clear message (spec section 9).
Three roots (decision 15 Oct 2026, docs/SPEC.md section 8):
- install root (read-only): code, knowledge/, prompts/, schemas/;
- working root: config.yaml, jobs/, failed/, logs/, feedback/ (%LOCALAPPDATA%\RenderAgent when installed);
- designer folders: Desktop\RenderAgent\{Input,Output,History} (see app/designer.py).
In dev mode (running from the repo) the install and working roots are the same folder.
Keys come from the environment, then Windows Credential Manager (keyring), then .env.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, ValidationError

from render_agent.errors import PermanentError

MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*[a-z0-9]$")
KEY_ENV_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "muapi": "MUAPI_API_KEY"}
SECRET_ENV_VARS = (*KEY_ENV_VARS.values(), "SMTP_USERNAME", "SMTP_PASSWORD")
READ_ONLY_PATHS = ("knowledge", "prompts", "schemas")  # resolved against the install root
APP_FOLDER = "RenderAgent"


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
    feedback: Path = Path("feedback")


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
    choices: dict[str, StageLLM] = Field(default_factory=dict)  # names for @llm=<name>


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
    enabled_providers: list[Literal["openai", "muapi"]] = Field(default=["openai"])
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
    min_confidence: float = Field(default=0.85, ge=0, le=1)


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


class Designer(_Strict):
    desktop: str = ""  # empty: the Windows Desktop known folder; tests and odd setups override it
    folder: str = Field(default=APP_FOLDER, min_length=1)


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
    designer: Designer = Field(default_factory=Designer)

    root: Path = Field(exclude=True)
    work_root: Path = Field(exclude=True)
    config_file: Path = Field(exclude=True)
    secrets: dict[str, SecretStr] = Field(default_factory=dict, exclude=True, repr=False)

    def path(self, name: str) -> Path:
        """An absolute path for a `paths:` entry: knowledge, prompts and schemas against the
        install root, everything the agent writes against the working root."""
        p = Path(getattr(self.paths, name))
        if p.is_absolute():
            return p
        return (self.root if name in READ_ONLY_PATHS else self.work_root) / p

    def secret(self, env_var: str) -> str | None:
        value = self.secrets.get(env_var)
        return value.get_secret_value() if value and value.get_secret_value() else None

    def required_key_vars(self) -> list[str]:
        """Env vars that must hold a key for the configured providers (mock needs none)."""
        providers: set[str] = {self.llm.provider, *(s.provider for s in self.llm.per_stage.values())}
        if self.images.provider != "mock":
            providers.add(self.images.provider)
        return sorted(KEY_ENV_VARS[p] for p in providers)


def installed() -> bool:
    """True in the packaged app (PyInstaller); dev mode runs from the repo."""
    return bool(getattr(sys, "frozen", False)) or os.environ.get("RENDER_AGENT_INSTALLED") == "1"


def install_root() -> Path:
    """Program, knowledge, prompts, schemas: RENDER_AGENT_HOME, else the packaged program folder,
    else the working directory (dev mode)."""
    home = os.environ.get("RENDER_AGENT_HOME")
    if home:
        return Path(home)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def working_root(install: Path) -> Path:
    r"""Jobs, config.yaml, logs, feedback: RENDER_AGENT_WORK, else %LOCALAPPDATA%\RenderAgent when
    installed, else the install root (dev mode keeps the repo layout)."""
    work = os.environ.get("RENDER_AGENT_WORK")
    if work:
        return Path(work)
    if installed():
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / APP_FOLDER
    return install


def _setting_name(loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in loc) or "(top level)"


def _load_secrets(root: Path) -> dict[str, SecretStr]:
    """Environment variables, then Windows Credential Manager, then .env (developer fallback)."""
    from render_agent.app.keys import stored_key

    file_values = dotenv_values(root / ".env") if (root / ".env").exists() else {}
    out: dict[str, SecretStr] = {}
    for var in SECRET_ENV_VARS:
        value = os.environ.get(var) or stored_key(var) or file_values.get(var) or ""
        out[var] = SecretStr(value.strip())
    return out


def load_config(root: Path | None = None, work: Path | None = None) -> Settings:
    root = (root or install_root()).resolve()
    work = (work or working_root(root)).resolve()
    cfg_path = work / "config.yaml"
    if not cfg_path.exists() and work != root and (root / "config.example.yaml").is_file():
        work.mkdir(parents=True, exist_ok=True)  # first run of the installed app
        cfg_path.write_text((root / "config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    if not cfg_path.exists():
        raise PermanentError(
            code="CONFIG_MISSING",
            what_happened=f"No config.yaml found in {work}.",
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
        settings = Settings(
            **data, root=root, work_root=work, config_file=cfg_path, secrets=_load_secrets(work)
        )
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
    _check_image_provider(settings)
    return settings


def _check_image_provider(s: Settings) -> None:
    if s.images.provider != "mock" and s.images.provider not in s.images.enabled_providers:
        raise PermanentError(
            code="CONFIG_INVALID",
            what_happened=(
                f"Setting 'images.provider' ('{s.images.provider}') is not in 'images.enabled_providers' "
                f"({', '.join(s.images.enabled_providers) or 'none'})."
            ),
            likely_cause="A provider was chosen that this installation does not offer.",
            what_to_do=(
                "Set 'images.provider' to an enabled provider, or add it to 'images.enabled_providers'."
            ),
        )


def _check_provider_models(s: Settings) -> None:
    """A Claude model ID only makes sense with the anthropic provider, and the reverse."""
    pairs = [("llm", s.llm.provider, s.llm.model)]
    pairs += [(f"llm.per_stage.{k}", v.provider, v.model) for k, v in s.llm.per_stage.items()]
    pairs += [(f"llm.choices.{k}", v.provider, v.model) for k, v in s.llm.choices.items()]
    for name, provider, model in pairs:
        if (provider == "anthropic") != model.startswith("claude-"):
            raise PermanentError(
                code="CONFIG_INVALID",
                what_happened=f"Setting '{name}.model' ('{model}') does not belong to provider '{provider}'.",
                likely_cause="The provider was changed without changing the model, or vice versa.",
                what_to_do=f"Set '{name}.provider' and '{name}.model' to a matching pair.",
            )
