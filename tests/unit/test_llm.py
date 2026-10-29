import json
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import openai
import pytest
from PIL import Image
from tenacity import wait_none

from render_agent.config import load_config
from render_agent.errors import PauseJobError, PermanentError, TransientError
from render_agent.llm import anthropic_llm, openai_llm
from render_agent.llm.base import BaseLLM, SendResult, get_llm, map_vendor_error

SCHEMA = {
    "type": "object",
    "properties": {"n": {"type": "integer"}},
    "required": ["n"],
    "additionalProperties": False,
}


class ScriptedLLM(BaseLLM):
    """Replays a list of SendResults / exceptions instead of calling a vendor."""

    provider = "scripted"

    def __init__(self, script, raw_dir=None, retries=3):
        super().__init__("test-model", 1000, 10, retries, raw_dir)
        self.wait = wait_none()
        self.script = list(script)
        self.prompts = []

    def _send(self, prompt, images, schema, schema_name):
        self.prompts.append(prompt)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(text):
    return SendResult("complete", text=text)


def test_valid_answer_saved_raw(tmp_path):
    llm = ScriptedLLM([ok('{"n": 1}')], raw_dir=tmp_path)
    assert llm.call_json("evidence", "p", [], SCHEMA, "x") == {"n": 1}
    raw = json.loads((tmp_path / "evidence.raw.json").read_text(encoding="utf-8"))
    assert raw["model"] == "test-model" and raw["attempts"][0]["output"] == '{"n": 1}'


def test_one_repair_quotes_the_error():
    llm = ScriptedLLM([ok('{"n": "one"}'), ok('{"n": 1}')])
    assert llm.call_json("s", "original", [], SCHEMA, "x") == {"n": 1}
    assert "failed validation" in llm.prompts[1] and "is not of type 'integer'" in llm.prompts[1]
    assert llm.prompts[1].startswith("original")


def test_second_invalid_answer_fails_and_keeps_raw(tmp_path):
    llm = ScriptedLLM([ok("not json"), ok("{}")], raw_dir=tmp_path)
    with pytest.raises(PermanentError) as exc:
        llm.call_json("spec", "p", [], SCHEMA, "x")
    assert exc.value.code == "LLM_SCHEMA_INVALID"
    raw = json.loads((tmp_path / "spec.raw.json").read_text(encoding="utf-8"))
    assert [a["kind"] for a in raw["attempts"]] == ["first", "repair"]
    assert "not valid JSON" in raw["attempts"][0]["validation_error"]


def test_transient_errors_retried_then_give_up():
    t = TransientError(code="LLM_TRANSIENT", what_happened="429")
    assert ScriptedLLM([t, t, ok('{"n": 2}')]).call_json("s", "p", [], SCHEMA, "x") == {"n": 2}
    llm = ScriptedLLM([t, t, t, ok('{"n": 2}')])
    with pytest.raises(TransientError):
        llm.call_json("s", "p", [], SCHEMA, "x")
    assert len(llm.prompts) == 3


@pytest.mark.parametrize(("stop", "code"), [("refusal", "LLM_REFUSED"), ("truncated", "LLM_TRUNCATED")])
def test_refusal_and_truncation_not_retried(stop, code):
    llm = ScriptedLLM([SendResult(stop, detail="why"), ok('{"n": 1}')])
    with pytest.raises(PermanentError) as exc:
        llm.call_json("s", "p", [], SCHEMA, "x")
    assert exc.value.code == code and len(llm.prompts) == 1


def test_pause_errors_pass_straight_through():
    llm = ScriptedLLM([PauseJobError(code="KEY_INVALID", what_happened="401")])
    with pytest.raises(PauseJobError):
        llm.call_json("s", "p", [], SCHEMA, "x")
    assert len(llm.prompts) == 1


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "https://api.example/v1"))


OPENAI_CASES = [
    (
        lambda: openai.AuthenticationError("bad key", response=_resp(401), body=None),
        PauseJobError,
        "KEY_INVALID",
    ),
    (
        lambda: openai.RateLimitError("insufficient_quota", response=_resp(429), body=None),
        PauseJobError,
        "OUT_OF_CREDIT",
    ),
    (lambda: openai.RateLimitError("slow down", response=_resp(429), body=None), TransientError, None),
    (lambda: openai.APIConnectionError(request=_resp(500).request), TransientError, None),
    (lambda: openai.InternalServerError("boom", response=_resp(503), body=None), TransientError, None),
    (
        lambda: openai.BadRequestError("bad", response=_resp(400), body=None),
        PermanentError,
        "LLM_BAD_REQUEST",
    ),
]


@pytest.mark.parametrize(("make", "kind", "code"), OPENAI_CASES)
def test_openai_error_mapping(make, kind, code):
    err = map_vendor_error(make(), openai, "OpenAI")
    assert isinstance(err, kind) and (code is None or err.code == code)


ANTHROPIC_CASES = [
    (lambda: anthropic.AuthenticationError("bad", response=_resp(401), body=None), PauseJobError),
    (
        lambda: anthropic.BadRequestError("Your credit balance is too low", response=_resp(400), body=None),
        PauseJobError,
    ),
    (lambda: anthropic.InternalServerError("overloaded", response=_resp(529), body=None), TransientError),
    (lambda: anthropic.BadRequestError("bad", response=_resp(400), body=None), PermanentError),
]


@pytest.mark.parametrize(("make", "kind"), ANTHROPIC_CASES)
def test_anthropic_error_mapping(make, kind):
    assert isinstance(map_vendor_error(make(), anthropic, "Anthropic"), kind)


def test_openai_response_parsing():
    part = SimpleNamespace(type="refusal", refusal="no")
    refused = SimpleNamespace(output=[SimpleNamespace(content=[part])], status="completed", output_text="")
    assert openai_llm.parse_response(refused).stop == "refusal"
    details = SimpleNamespace(reason="max_output_tokens")
    cut = SimpleNamespace(output=[], status="incomplete", incomplete_details=details, output_text="{")
    assert openai_llm.parse_response(cut).stop == "truncated"
    done = SimpleNamespace(output=[], status="completed", output_text='{"n": 1}')
    assert openai_llm.parse_response(done) == SendResult("complete", text='{"n": 1}')


def test_anthropic_response_parsing():
    tool = SimpleNamespace(type="tool_use", name="x", input={"n": 1})
    done = SimpleNamespace(stop_reason="tool_use", content=[tool])
    assert anthropic_llm.parse_response(done, "x").data == {"n": 1}
    cut = SimpleNamespace(stop_reason="max_tokens", content=[])
    assert anthropic_llm.parse_response(cut, "x").stop == "truncated"


def test_requests_use_strict_schema_and_the_answer_tool(tmp_path):
    img = tmp_path / "p.png"
    Image.new("RGB", (5000, 100), "white").save(img)
    common = {"model": "m", "max_output_tokens": 10, "timeout_seconds": 5, "retries": 1}
    req = openai_llm.OpenAILLM(api_key="k", reasoning_effort="low", **common)._request(
        "hi", [img], SCHEMA, "x"
    )
    assert req["text"]["format"] == {"type": "json_schema", "name": "x", "schema": SCHEMA, "strict": True}
    assert req["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    req = anthropic_llm.AnthropicLLM(api_key="k", **common)._request("hi", [img], SCHEMA, "x")
    # current Claude models reject a forced tool choice (400): auto, steered by the system prompt
    assert req["tool_choice"] == {"type": "auto"} and req["tools"][0]["input_schema"] == SCHEMA
    assert "`x` tool exactly once" in req["system"]
    assert req["messages"][0]["content"][-1] == {"type": "text", "text": "hi"}


def test_get_llm_keys_and_per_stage(install, monkeypatch):
    with pytest.raises(PauseJobError) as exc:
        get_llm(load_config(install), "evidence")
    assert exc.value.code == "KEY_MISSING" and ".env" in exc.value.what_to_do
    cfg = install / "config.yaml"
    override = "per_stage: {sheet_analysis: {provider: anthropic, model: claude-sonnet-5-5}}"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("per_stage: {}", override), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0123456789abcdef")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0123456789")
    s = load_config(install)
    assert get_llm(s, "sheet_analysis").provider == "anthropic"
    assert get_llm(s, "evidence").model == s.llm.model
