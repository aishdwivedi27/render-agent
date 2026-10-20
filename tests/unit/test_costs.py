"""Draft and final image settings, token capture and the cost report (decision 20 Oct 2026)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from render_agent.config import Price
from render_agent.images.openai_images import usage_of as image_usage
from render_agent.llm import anthropic_llm, openai_llm
from render_agent.output.costs import image_cost, llm_cost, money, summarise


def test_image_usage_is_read_from_the_response():
    resp = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=1200, output_tokens=6000, input_tokens_details=SimpleNamespace(image_tokens=1000)
        )
    )
    assert image_usage(resp) == {"input_tokens": 1200, "output_tokens": 6000, "image_input_tokens": 1000}
    assert image_usage(SimpleNamespace()) == {}


def test_llm_usage_is_read_from_both_providers():
    o = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5, output_tokens_details=None))
    assert openai_llm.usage_of(o) == {"input_tokens": 10, "output_tokens": 5}
    a = SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=3, cache_read_input_tokens=2))
    assert anthropic_llm.usage_of(a) == {"input_tokens": 9, "output_tokens": 3}


def test_cost_maths_and_missing_prices():
    price = Price(input=5, output=30, image_input=8)
    usage = {"input_tokens": 1200, "image_input_tokens": 1000, "output_tokens": 6000}
    assert round(image_cost(usage, price), 6) == round((200 * 5 + 1000 * 8 + 6000 * 30) / 1e6, 6)
    assert image_cost(usage, Price(output=30)) is None  # input prices missing
    assert llm_cost({"input_tokens": 1000, "output_tokens": 1000}, Price(input=1, output=2)) == 0.003
    assert llm_cost({"input_tokens": 1}, None) is None


def test_summary_and_money(tmp_path):
    (tmp_path / "budget.json").write_text(json.dumps({"calls": [
        {"render_id": "R1", "kind": "first", "provider": "openai", "paid": True,
         "usage": {"input_tokens": 100, "output_tokens": 1000}},
        {"render_id": "R1", "kind": "final", "provider": "openai", "paid": True,
         "usage": {"input_tokens": 100, "output_tokens": 4000}},
        {"render_id": "R1", "kind": "first", "provider": "mock", "paid": False},
    ]}))  # fmt: skip
    (tmp_path / "usage.json").write_text(json.dumps({"calls": [
        {"stage": "sheet_analysis.page-1", "model": "m-llm",
         "usage": {"input_tokens": 50, "output_tokens": 20}},
    ]}))  # fmt: skip
    settings = SimpleNamespace(
        images=SimpleNamespace(openai=SimpleNamespace(model="m-img")),
        pricing={"m-img": Price(input=10, output=40)},
    )
    s = summarise(tmp_path, settings)
    assert s["images"]["R1"]["final"]["output_tokens"] == 4000
    assert round(s["totals"]["images"]["cost_usd"], 4) == round((200 * 10 + 5000 * 40) / 1e6, 4)
    assert s["llm"]["sheet_analysis"]["input_tokens"] == 50 and s["totals"]["llm"]["price_missing"]
    assert money(s["totals"]["llm"]) == "price not set"
    assert money(s["totals"]["all"]).endswith("+ calls without a price")
