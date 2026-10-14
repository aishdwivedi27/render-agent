"""Image providers behind one interface (docs/SPEC.md section 7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from render_agent.images.base import ImageProvider, RenderRequest, render_with_retry, size_for
from render_agent.images.mock import MockProvider

if TYPE_CHECKING:
    from render_agent.config import Settings

__all__ = ["ImageProvider", "MockProvider", "RenderRequest", "get_provider", "render_with_retry", "size_for"]
PROVIDERS = ("openai", "muapi", "mock")


def get_provider(settings: Settings, name: str | None = None, dry_run: bool = False) -> ImageProvider:
    """`name` (the @provider directive) overrides config; a dry run always uses the mock."""
    from render_agent.llm.base import key_missing

    choice = "mock" if dry_run else (name if name in PROVIDERS else settings.images.provider)
    if choice == "mock":
        return MockProvider()
    cfg = settings.images
    if choice == "openai":
        from render_agent.images.openai_images import OpenAIImagesProvider

        key = settings.secret("OPENAI_API_KEY")
        if not key:
            raise key_missing("OPENAI_API_KEY")
        return OpenAIImagesProvider(key, cfg.openai.model, cfg.openai.quality, settings.llm.timeout_seconds)
    from render_agent.images.muapi_cli import MuapiProvider

    key = settings.secret("MUAPI_API_KEY")
    if not key:
        raise key_missing("MUAPI_API_KEY")
    return MuapiProvider(key, cfg.muapi.edit_model, cfg.muapi.timeout_seconds)
