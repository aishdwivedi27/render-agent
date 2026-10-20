"""One interface for every LLM call (docs/SPEC.md section 7).

call_json(stage, prompt, images, schema, schema_name) -> dict
- provider/model from config (llm.per_stage overrides the default)
- OpenAI: Responses API, text.format = json_schema strict
- Anthropic: Messages API with one tool whose input_schema is the schema, tool_choice forced
- validates with jsonschema; on failure one repair call quoting the validation error
- transient vendor errors retried with backoff; refusals and truncation are not retried
- saves raw output and request metadata (never keys) to <raw_dir>/<stage>.raw.json
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import jsonschema
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from render_agent.errors import PauseJobError, PermanentError, TransientError
from render_agent.util.fs import atomic_write_json, safe_name
from render_agent.util.prompts import quote_block

if TYPE_CHECKING:
    from render_agent.config import Settings

log = logging.getLogger("render_agent.llm")
USAGE_FILE = "usage.json"  # in the job folder: tokens of every LLM call, for the cost report
_usage_lock = threading.Lock()


class LLM(Protocol):
    def call_json(
        self, stage: str, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]: ...


@dataclass
class SendResult:
    """What a provider returned: parsed data (tool input) or text, and how the answer ended."""

    stop: Literal["complete", "refusal", "truncated"]
    text: str = ""
    data: dict[str, Any] | None = None
    detail: str = ""
    usage: dict[str, int] = field(default_factory=dict)  # tokens the provider reported for this call


class BaseLLM:
    provider = "base"

    def __init__(
        self,
        model: str,
        max_output_tokens: int,
        timeout_seconds: int,
        retries: int,
        raw_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.raw_dir = raw_dir
        self.wait: Any = wait_exponential(multiplier=2, min=2, max=60)

    # Providers implement this. It must raise TransientError / PermanentError / PauseJobError only.
    def _send(self, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str) -> SendResult:
        raise NotImplementedError

    def _send_with_retry(self, *args: Any) -> SendResult:
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(max(1, self.retries)),
                wait=self.wait,
                retry=retry_if_exception_type(TransientError),
                reraise=False,
            ):
                with attempt:
                    if attempt.retry_state.attempt_number > 1:
                        log.warning("%s: retry %d", self.provider, attempt.retry_state.attempt_number)
                    return self._send(*args)
        except RetryError as exc:
            last = exc.last_attempt.exception()
            assert isinstance(last, TransientError)
            raise last from exc
        raise AssertionError("unreachable")

    def call_json(
        self, stage: str, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "stage": stage,
            "schema_name": schema_name,
            "images": [p.name for p in images],
            "attempts": [],
        }
        current = prompt
        try:
            for kind in ("first", "repair"):
                started = time.monotonic()
                result = self._send_with_retry(current, images, schema, schema_name)
                entry: dict[str, Any] = {
                    "kind": kind,
                    "seconds": round(time.monotonic() - started, 1),
                    "stop": result.stop,
                    "output": result.data if result.data is not None else result.text,
                    "usage": result.usage,
                }
                self._record_usage(stage, kind, result.usage)
                record["attempts"].append(entry)
                if result.stop != "complete":
                    raise _stop_error(stage, result)
                data, problem = _parse_and_validate(result, schema)
                if problem is None:
                    assert data is not None
                    return data
                entry["validation_error"] = problem
                log.warning("%s: answer failed validation (%s attempt): %s", stage, kind, problem)
                current = _repair_prompt(prompt, problem, entry["output"])
            raise PermanentError(
                code="LLM_SCHEMA_INVALID",
                what_happened=f"The {stage} answer did not match its schema, even after one repair.",
                likely_cause=f"Model output problem: {record['attempts'][-1]['validation_error']}",
                what_to_do="Resume the job to try again; if it repeats, switch the model for this stage.",
            )
        finally:
            self._save_raw(stage, record)

    def _record_usage(self, stage: str, kind: str, usage: dict[str, int]) -> None:
        """Appends one call to <job>/usage.json (raw_dir is <job>/llm); nothing outside a job."""
        if self.raw_dir is None or not usage:
            return
        path = self.raw_dir.parent / USAGE_FILE
        row = {
            "stage": stage,
            "kind": kind,
            "provider": self.provider,
            "model": self.model,
            "usage": usage,
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with _usage_lock:
            calls = json.loads(path.read_text(encoding="utf-8"))["calls"] if path.is_file() else []
            atomic_write_json(path, {"calls": [*calls, row]})

    def _save_raw(self, stage: str, record: dict[str, Any]) -> None:
        if self.raw_dir is not None:
            atomic_write_json(self.raw_dir / f"{safe_name(stage)}.raw.json", record)


def _parse_and_validate(
    result: SendResult, schema: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    data: Any = result.data
    if data is None:
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as exc:
            return None, f"not valid JSON: {exc}"
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        return None, f"at {path}: {exc.message}"
    return data, None


def _repair_prompt(original: str, problem: str, previous: Any) -> str:
    shown = previous if isinstance(previous, str) else json.dumps(previous, ensure_ascii=False)
    return (
        f"{original}\n\n## Correction needed\n\nYour previous answer failed validation: {problem}\n"
        "Return the complete corrected answer. Keep every fact the same unless it caused the error.\n\n"
        + quote_block("PREVIOUS_ANSWER", shown)
    )


def _stop_error(stage: str, result: SendResult) -> PermanentError:
    if result.stop == "refusal":
        return PermanentError(
            code="LLM_REFUSED",
            what_happened=f"The model refused the {stage} request: {result.detail or 'no reason given'}.",
            likely_cause="The model's safety filter reacted to the page content or prompt.",
            what_to_do="Check the page, then resume the job or switch the model for this stage.",
        )
    return PermanentError(
        code="LLM_TRUNCATED",
        what_happened=f"The {stage} answer was cut off before it finished ({result.detail or 'length'}).",
        likely_cause="llm.max_output_tokens is too small for this drawing set.",
        what_to_do="Raise llm.max_output_tokens in config.yaml, then resume the job.",
    )


def encode_image(path: Path, max_edge: int, max_bytes: int = 4_500_000) -> tuple[str, str]:
    """(media_type, base64) scaled so the long edge is at most max_edge; JPEG if PNG is too big.

    Vendors downscale larger images anyway; doing it here keeps requests inside their size limits.
    """
    import base64
    import io

    from PIL import Image

    with Image.open(path) as src:
        im = src.convert("RGB")
        if max(im.size) > max_edge:
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        media = "image/png"
        if buf.tell() > max_bytes:
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=90)
            media = "image/jpeg"
    return media, base64.b64encode(buf.getvalue()).decode("ascii")


def key_missing(env_var: str) -> PauseJobError:
    return PauseJobError(
        code="KEY_MISSING",
        what_happened=f"{env_var} is not set.",
        likely_cause="The key was never added in Settings (or .env), or it was removed.",
        what_to_do=(
            f"Add the key in Settings, then press Retry (developers: set {env_var} in .env, "
            "then resume the job)."
        ),
    )


# Phrases vendors use when the account has no money left (checked before rate limits: OpenAI
# reports insufficient_quota as a 429).
CREDIT_PHRASES = (
    "insufficient_quota",
    "exceeded your current quota",
    "credit balance",
    "insufficient credit",
    "insufficient balance",
    "out of credit",
    "billing",
    "payment required",
    "hard limit",
    "spend limit",
    "spending limit",
)
BILLING_PAGES = {
    "openai": "the OpenAI billing page (platform.openai.com > Settings > Billing)",
    "anthropic": "the Anthropic billing page (console.anthropic.com > Plans & Billing)",
    "muapi": "your MuAPI account's credits page",
}


def is_out_of_credit(message: str, status: int | None = None) -> bool:
    low = message.lower()
    return status == 402 or any(p in low for p in CREDIT_PHRASES)


def out_of_credit(provider: str, message: str) -> PauseJobError:
    """The account ran out of money or hit its spending limit. The job pauses, nothing is lost."""
    key = provider.split()[0].lower()
    page = BILLING_PAGES.get(key, f"the {provider} billing page")
    return PauseJobError(
        code="OUT_OF_CREDIT",
        what_happened=f"The {provider} account is out of credit or at its spending limit: {message[:200]}",
        likely_cause="The prepaid balance ran out, or the monthly spending limit was reached.",
        what_to_do=(
            f"Add credit or raise the limit on {page}. The job is paused and every finished file is "
            "kept; resume the job afterwards and it continues where it stopped."
        ),
    )


def map_vendor_error(exc: Exception, sdk: Any, provider: str) -> Exception:
    """Maps an openai/anthropic SDK exception (both share these class names) to the taxonomy."""
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, sdk.AuthenticationError | sdk.PermissionDeniedError):
        return PauseJobError(
            code="KEY_INVALID",
            what_happened=f"The {provider} API rejected the key: {msg[:200]}",
            likely_cause="The key in .env is wrong, expired or revoked.",
            what_to_do="Update the key in .env, then resume the job.",
        )
    if isinstance(exc, sdk.APIStatusError) and is_out_of_credit(low, exc.status_code):
        return out_of_credit(provider, msg)
    if isinstance(exc, sdk.RateLimitError | sdk.APIConnectionError | sdk.InternalServerError):
        return TransientError(code="LLM_TRANSIENT", what_happened=f"{provider}: {msg[:200]}")
    if isinstance(exc, sdk.APIStatusError) and exc.status_code >= 500:
        return TransientError(code="LLM_TRANSIENT", what_happened=f"{provider}: {msg[:200]}")
    return PermanentError(
        code="LLM_BAD_REQUEST",
        what_happened=f"{provider} rejected the request: {msg[:300]}",
        likely_cause="An unsupported setting, an oversized image, or a content-policy block.",
        what_to_do="Check job.log for the request, fix config.yaml if needed, then resume the job.",
    )


def get_llm(
    settings: Settings, stage: str, raw_dir: Path | None = None, job_choice: str | None = None
) -> BaseLLM:
    """The configured LLM for a stage: the job's @llm choice (a name in llm.choices) wins, then
    llm.per_stage, then the default."""
    cfg = settings.llm
    choice = cfg.choices.get(job_choice or "") or cfg.per_stage.get(stage)
    provider, model = (choice.provider, choice.model) if choice else (cfg.provider, cfg.model)
    common = {
        "model": model,
        "max_output_tokens": cfg.max_output_tokens,
        "timeout_seconds": cfg.timeout_seconds,
        "retries": cfg.retries,
        "raw_dir": raw_dir,
    }
    if provider == "openai":
        from render_agent.llm.openai_llm import OpenAILLM

        key = settings.secret("OPENAI_API_KEY")
        if not key:
            raise key_missing("OPENAI_API_KEY")
        return OpenAILLM(api_key=key, reasoning_effort=cfg.reasoning_effort, **common)
    from render_agent.llm.anthropic_llm import AnthropicLLM

    key = settings.secret("ANTHROPIC_API_KEY")
    if not key:
        raise key_missing("ANTHROPIC_API_KEY")
    return AnthropicLLM(api_key=key, **common)
