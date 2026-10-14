"""OpenAI images.edit with reference files; images.generate when there are none.

Model, size and quality come from config.yaml. Vendor errors are mapped to the taxonomy; a
content-policy or bad request is never retried (its reason is shown at review).
"""

from __future__ import annotations

import base64
from contextlib import ExitStack
from typing import Any

from render_agent.errors import PauseJobError, PermanentError, TransientError
from render_agent.images.base import RenderRequest
from render_agent.llm.base import map_vendor_error


class OpenAIImagesProvider:
    name = "openai"
    paid = True

    def __init__(self, api_key: str, model: str, quality: str, timeout_seconds: int, client: Any = None):
        self.model = model
        self.quality = quality
        if client is None:
            import openai

            client = openai.OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self.client = client

    def render(self, req: RenderRequest) -> bytes:
        import openai

        args = {"model": self.model, "prompt": req.prompt, "size": req.size, "quality": self.quality, "n": 1}
        try:
            with ExitStack() as stack:
                if req.references:
                    files = [stack.enter_context(p.open("rb")) for p in req.references]
                    resp = self.client.images.edit(image=files, **args)
                else:
                    resp = self.client.images.generate(**args)
        except openai.OpenAIError as exc:
            raise map_image_error(exc, openai) from exc
        data = getattr(resp, "data", None) or []
        b64 = getattr(data[0], "b64_json", None) if data else None
        return base64.b64decode(b64) if b64 else b""


def map_image_error(exc: Exception, sdk: Any) -> Exception:
    err = map_vendor_error(exc, sdk, "OpenAI images")
    if isinstance(err, PauseJobError):
        return err
    if isinstance(err, TransientError):
        return TransientError(code="IMAGE_TRANSIENT", what_happened=err.what_happened)
    low = str(exc).lower()
    policy = any(w in low for w in ("content_policy", "moderation", "safety"))
    return PermanentError(
        code="IMAGE_REJECTED",
        what_happened=f"OpenAI images rejected the request: {str(exc)[:300]}",
        likely_cause="The prompt or a reference image tripped the content filter."
        if policy
        else "An unsupported size, quality or model setting, or an oversized reference image.",
        what_to_do="Read the prompt and reason at review; edit and regenerate, or fix config.yaml.",
    )
