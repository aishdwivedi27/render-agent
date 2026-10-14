"""OpenAI Responses API implementation of llm.base.BaseLLM (strict JSON Schema output)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import openai

from render_agent.llm.base import BaseLLM, SendResult, encode_image, map_vendor_error

MAX_IMAGE_EDGE = 2048  # "high" detail is scaled to fit 2048 px by the API


class OpenAILLM(BaseLLM):
    provider = "openai"

    def __init__(self, api_key: str, reasoning_effort: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reasoning_effort = reasoning_effort
        self.client = openai.OpenAI(api_key=api_key, timeout=self.timeout_seconds, max_retries=0)

    def _request(
        self, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for p in images:
            media, b64 = encode_image(p, MAX_IMAGE_EDGE)
            content.append(
                {"type": "input_image", "image_url": f"data:{media};base64,{b64}", "detail": "high"}
            )
        return {
            "model": self.model,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}
            },
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
        }

    def _send(self, prompt: str, images: list[Path], schema: dict[str, Any], schema_name: str) -> SendResult:
        try:
            resp = self.client.responses.create(**self._request(prompt, images, schema, schema_name))
        except openai.OpenAIError as exc:
            raise map_vendor_error(exc, openai, "OpenAI") from exc
        return parse_response(resp)


def parse_response(resp: Any) -> SendResult:
    for item in resp.output or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", "") == "refusal":
                return SendResult("refusal", detail=getattr(part, "refusal", ""))
    if resp.status == "incomplete":
        reason = getattr(resp.incomplete_details, "reason", "") or "incomplete"
        if reason == "content_filter":
            return SendResult("refusal", text=resp.output_text or "", detail=reason)
        return SendResult("truncated", text=resp.output_text or "", detail=reason)
    return SendResult("complete", text=resp.output_text or "")
