"""Image provider interface (docs/SPEC.md section 7). Every paid call goes through budget.py first.

Providers return PNG bytes and raise only the error taxonomy:
- TransientError: rate limit, timeout, 5xx, network (retried with backoff by `render_with_retry`)
- PermanentError: content policy / bad request (no retry); code IMAGE_UNREADABLE is retried once
- PauseJobError: key missing or invalid, quota or billing limit
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tenacity import RetryError, Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from render_agent.errors import PermanentError, TransientError

log = logging.getLogger("render_agent.images")
UNREADABLE = "IMAGE_UNREADABLE"


@dataclass
class RenderRequest:
    render_id: str
    prompt: str
    references: list[Path]
    size: str
    aspect: str = "landscape"
    quality: str = ""  # "" = the provider's configured quality
    usage: dict[str, int] = field(default_factory=dict)  # tokens the provider reports, filled in by render()


class ImageProvider(Protocol):
    name: str
    model: str
    paid: bool

    def render(self, req: RenderRequest) -> bytes: ...


def size_for(size: str, aspect: str) -> str:
    """The configured size, turned to portrait when the scene asks for it."""
    if size == "auto" or "x" not in size:
        return size
    w, h = (int(x) for x in size.split("x"))
    if aspect == "portrait" and w > h or aspect == "landscape" and h > w:
        w, h = h, w
    return f"{w}x{h}"


def verify_png(data: bytes | None, provider: str) -> bytes:
    """PNG bytes of a readable image, or PermanentError(IMAGE_UNREADABLE)."""
    from PIL import Image

    if not data:
        raise PermanentError(code=UNREADABLE, what_happened=f"{provider} returned no image.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if im.format == "PNG":
                return data
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()
    except Exception as exc:  # Pillow raises many types for a broken file
        raise PermanentError(
            code=UNREADABLE, what_happened=f"{provider} returned an unreadable image: {exc}"
        ) from exc


def render_with_retry(
    provider: ImageProvider, req: RenderRequest, retries: int, wait: Any = None
) -> tuple[bytes, int]:
    """(png, attempts). Transient errors: backoff, `retries` attempts. Unreadable image: one more try."""
    attempts = 0

    def once() -> bytes:
        nonlocal attempts
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(max(1, retries)),
                wait=wait if wait is not None else wait_exponential(multiplier=2, min=2, max=60),
                retry=retry_if_exception_type(TransientError),
                reraise=False,
            ):
                with attempt:
                    attempts += 1
                    if attempts > 1:
                        log.warning("%s: %s attempt %d", provider.name, req.render_id, attempts)
                    return verify_png(provider.render(req), provider.name)
        except RetryError as exc:
            last = exc.last_attempt.exception()
            assert isinstance(last, TransientError)
            raise last from exc
        raise AssertionError("unreachable")

    try:
        return once(), attempts
    except PermanentError as err:
        if err.code != UNREADABLE:
            raise
        log.warning("%s: %s unreadable image; retrying once", provider.name, req.render_id)
        return once(), attempts
