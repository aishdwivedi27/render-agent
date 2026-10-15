"""API keys in Windows Credential Manager (keyring), validated with no-cost calls only.

Keys never leave this module except to the provider's own SDK: the app shows only "saved" /
"missing" / "working", never the value. `.env` and environment variables stay the developer
fallback (config._load_secrets). Set RENDER_AGENT_NO_KEYRING=1 to ignore the credential store
(tests do, so a developer's real key can never leak into a test run).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from render_agent.config import KEY_ENV_VARS

if TYPE_CHECKING:
    from render_agent.config import Settings

SERVICE = "RenderAgent"
log = logging.getLogger("render_agent.keys")


def _keyring_on() -> bool:
    return os.environ.get("RENDER_AGENT_NO_KEYRING") != "1"


def stored_key(env_var: str) -> str | None:
    if not _keyring_on():
        return None
    try:
        import keyring

        return keyring.get_password(SERVICE, env_var) or None
    except Exception:  # no backend, locked store: fall back to .env quietly
        log.warning("credential store not available; using .env / environment for %s", env_var)
        return None


def save_key(env_var: str, value: str) -> None:
    import keyring

    keyring.set_password(SERVICE, env_var, value.strip())


def _openai_check(key: str) -> None:
    from openai import OpenAI

    OpenAI(api_key=key, timeout=20, max_retries=0).models.list()  # free: lists models


def _anthropic_check(key: str) -> None:
    from anthropic import Anthropic

    Anthropic(api_key=key, timeout=20, max_retries=0).models.list(limit=1)  # free


def _no_check(key: str) -> None:
    if not key.strip():
        raise ValueError("empty key")


# provider -> a no-cost authentication call that raises when the key is refused
VALIDATORS: dict[str, Callable[[str], None]] = {
    "openai": _openai_check,
    "anthropic": _anthropic_check,
    "muapi": _no_check,  # muapi-cli has no free auth call; checked when the provider is enabled
}


def test_key(provider: str, key: str) -> tuple[bool, str]:
    """(ok, plain message). The message never contains the key."""
    try:
        VALIDATORS[provider](key)
    except Exception as exc:  # any refusal or network problem
        text = str(exc).replace(key, "***") if key else str(exc)
        log.info("key test for %s failed: %s", provider, type(exc).__name__)
        if "401" in text or "auth" in text.lower() or "invalid" in text.lower():
            return False, f"The {provider} key was refused. Check it was copied in full."
        return False, f"Could not reach {provider} to test the key. Check the internet connection."
    return True, f"The {provider} key works."


def needed_providers(settings: Settings) -> list[str]:
    """Every provider that must have a key before a job may start: the LLM providers in use and
    every enabled image provider."""
    llm = {settings.llm.provider, *(s.provider for s in settings.llm.per_stage.values())}
    return sorted(llm | set(settings.images.enabled_providers))


def has_key(settings: Settings, provider: str) -> bool:
    return bool(settings.secret(KEY_ENV_VARS[provider]))


def available_image_providers(settings: Settings) -> list[str]:
    """Image providers a designer may pick: enabled in config AND holding a key."""
    return [p for p in settings.images.enabled_providers if has_key(settings, p)]


def missing_keys(settings: Settings) -> list[str]:
    return [p for p in needed_providers(settings) if not has_key(settings, p)]
