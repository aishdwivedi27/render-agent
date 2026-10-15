"""Settings: keys for the enabled providers only (saved in Credential Manager), Test, model choice.

A key is saved only after a no-cost check accepts it. Responses say "saved" / "missing", never the key.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent.app import keys
from render_agent.app.main import AppContext
from render_agent.app.models import SettingsIn, TestKeyIn
from render_agent.app.routes import Ctx
from render_agent.app.security import require_token
from render_agent.config import KEY_ENV_VARS
from render_agent.util.fs import atomic_write_json

router = APIRouter()
PREFS = "app_prefs.json"
LABELS = {"openai": "OpenAI", "anthropic": "Anthropic (Claude)", "muapi": "MuAPI"}


def prefs(ctx: AppContext) -> dict[str, Any]:
    path = ctx.settings.work_root / PREFS
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _shown(ctx: AppContext) -> list[str]:
    """Only providers this installation uses: the LLM ones and the enabled image ones."""
    return keys.needed_providers(ctx.settings)


def _bad_provider(provider: str) -> HTTPException:
    return HTTPException(
        400,
        {
            "what_happened": f"'{provider}' is not used here.",
            "what_to_do": "Choose one of the listed services.",
        },
    )


@router.get("/settings")
def get_settings(ctx: Ctx) -> dict[str, Any]:
    s = ctx.settings
    p = prefs(ctx)
    return {
        "providers": [
            {
                "name": name,
                "label": LABELS.get(name, name),
                "has_key": keys.has_key(s, name),
                "used_for": ", ".join(
                    x
                    for x, ok in (
                        ("reading drawings", name == s.llm.provider),
                        ("making images", name in s.images.enabled_providers),
                    )
                    if ok
                )
                or "some stages",
            }
            for name in _shown(ctx)
        ],
        "llm_choices": sorted(s.llm.choices),
        "llm_choice": p.get("llm_choice") or "",
        "image_providers": keys.available_image_providers(s),
    }


@router.post("/settings/test", dependencies=[Depends(require_token)])
def test_key(body: TestKeyIn, ctx: Ctx) -> dict[str, Any]:
    if body.provider not in _shown(ctx):
        raise _bad_provider(body.provider)
    key = body.key.strip() or ctx.settings.secret(KEY_ENV_VARS[body.provider]) or ""
    if not key:
        return {"ok": False, "message": "There is no key to test yet. Paste it in the box first."}
    ok, message = keys.test_key(body.provider, key)
    return {"ok": ok, "message": message}


@router.post("/settings", dependencies=[Depends(require_token)])
def save_settings(body: SettingsIn, ctx: Ctx) -> dict[str, Any]:
    results = {}
    for provider, value in body.keys.items():
        if provider not in _shown(ctx):
            raise _bad_provider(provider)
        if not value.strip():
            continue
        ok, message = keys.test_key(provider, value)
        if not ok:
            results[provider] = {"ok": False, "message": message + " It was not saved."}
            continue
        try:
            keys.save_key(KEY_ENV_VARS[provider], value)
        except Exception:
            results[provider] = {
                "ok": False,
                "message": "Windows Credential Manager refused to store the key. Try again, or ask IT.",
            }
            continue
        results[provider] = {"ok": True, "message": message + " Saved."}
    ctx.reload_settings()
    return {"results": results, **get_settings(ctx)}


@router.post("/settings/llm", dependencies=[Depends(require_token)])
def save_llm_choice(body: dict[str, str], ctx: Ctx) -> dict[str, Any]:
    choice = body.get("llm_choice", "")
    if choice and choice not in ctx.settings.llm.choices:
        raise _bad_provider(choice)
    atomic_write_json(ctx.settings.work_root / PREFS, {**prefs(ctx), "llm_choice": choice})
    return get_settings(ctx)
