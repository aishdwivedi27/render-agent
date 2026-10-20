"""Anthropic Messages API implementation of llm.base.BaseLLM using a forced tool call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic

from render_agent.llm.base import BaseLLM, SendResult, encode_image, map_vendor_error

MAX_IMAGE_EDGE = 1568  # larger images are downscaled by the API


class AnthropicLLM(BaseLLM):
    provider = "anthropic"

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.client = anthropic.Anthropic(api_key=api_key, timeout=self.timeout_seconds, max_retries=0)

    def _request(
        self, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for p in images:
            media, b64 = encode_image(p, MAX_IMAGE_EDGE)
            content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
        content.append({"type": "text", "text": prompt})
        return {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": [{"role": "user", "content": content}],
            "tools": [
                {
                    "name": schema_name,
                    "description": "Record the complete answer. Call this exactly once.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema_name},
        }

    def _send(self, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str) -> SendResult:
        try:
            resp = self.client.messages.create(**self._request(prompt, images, schema, schema_name))
        except anthropic.AnthropicError as exc:
            raise map_vendor_error(exc, anthropic, "Anthropic") from exc
        result = parse_response(resp, schema_name)
        result.usage = usage_of(resp)
        return result


def usage_of(resp: Any) -> dict[str, int]:
    """Input and output tokens of a Messages API answer ({} when not reported)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    out = {
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0)
        + int(getattr(u, "cache_read_input_tokens", 0) or 0)
        + int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
    }
    return {k: v for k, v in out.items() if v}


def parse_response(resp: Any, schema_name: str) -> SendResult:
    if resp.stop_reason == "refusal":
        return SendResult("refusal", detail="stop_reason=refusal")
    if resp.stop_reason == "max_tokens":
        return SendResult("truncated", detail="max_tokens")
    for block in resp.content:
        if block.type == "tool_use" and block.name == schema_name:
            return SendResult("complete", data=block.input)
    text = "".join(getattr(b, "text", "") for b in resp.content)
    return SendResult("complete", text=text)  # fails validation -> repair call
