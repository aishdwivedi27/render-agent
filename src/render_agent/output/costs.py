"""Tokens and estimated cost of a job, from the usage the providers reported (decision 20 Oct 2026).

Image calls: budget.json (one row per paid or free call, with `usage` and `quality`).
LLM calls: usage.json (one row per call, written by llm/base.py).
Prices: config `pricing` (USD per 1 million tokens per model id). A model without a price shows its
tokens with "price not set"; the cost is always an estimate from the configured prices, never a bill.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from render_agent.config import Price, Settings

PER = 1_000_000


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("calls", []))


def image_cost(usage: dict[str, int], price: Price | None) -> float | None:
    """Text input + image input + output tokens at the model's prices; None if a needed price is missing."""
    if not usage:
        return 0.0
    if price is None or price.output is None:
        return None
    image_in = usage.get("image_input_tokens", 0)
    text_in = max(0, usage.get("input_tokens", 0) - image_in)
    if (text_in and price.input is None) or (image_in and price.image_input is None):
        return None
    return (
        text_in * (price.input or 0)
        + image_in * (price.image_input or 0)
        + usage.get("output_tokens", 0) * price.output
    ) / PER


def llm_cost(usage: dict[str, int], price: Price | None) -> float | None:
    if not usage:
        return 0.0
    if price is None or price.input is None or price.output is None:
        return None
    return (usage.get("input_tokens", 0) * price.input + usage.get("output_tokens", 0) * price.output) / PER


def _add(total: dict[str, Any], usage: dict[str, int], cost: float | None) -> None:
    total["calls"] += 1
    for k in ("input_tokens", "output_tokens"):
        total[k] += usage.get(k, 0)
    if not usage:
        total["no_usage"] += 1
    elif cost is None:
        total["price_missing"] = True
    else:
        total["cost_usd"] += cost


def _blank() -> dict[str, Any]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "price_missing": False,
        "no_usage": 0,
    }


def summarise(job_dir: Path, settings: Settings) -> dict[str, Any]:
    """{"images": {render_id: {kind: totals}}, "llm": {stage: totals}, "totals": {...}}."""
    image_model = settings.images.openai.model
    images: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(_blank))
    llm: dict[str, dict[str, Any]] = defaultdict(_blank)
    totals = {"images": _blank(), "llm": _blank()}
    for c in _load(job_dir / "budget.json"):
        if not c.get("paid"):
            continue
        usage = c.get("usage", {})
        price = settings.pricing.get(image_model) if c.get("provider") == "openai" else None
        cost = image_cost(usage, price)
        _add(images[c["render_id"]][c["kind"]], usage, cost)
        _add(totals["images"], usage, cost)
    for c in _load(job_dir / "usage.json"):
        stage = c["stage"].split(".")[0]  # sheet_analysis.page-2 -> sheet_analysis
        cost = llm_cost(c.get("usage", {}), settings.pricing.get(c.get("model", "")))
        _add(llm[stage], c.get("usage", {}), cost)
        _add(totals["llm"], c.get("usage", {}), cost)
    grand = _blank()
    for part in totals.values():
        for k in ("calls", "input_tokens", "output_tokens", "no_usage"):
            grand[k] += part[k]
        grand["cost_usd"] += part["cost_usd"]
        grand["price_missing"] = grand["price_missing"] or part["price_missing"]
    return {
        "images": {rid: dict(kinds) for rid, kinds in images.items()},
        "llm": dict(llm),
        "totals": {**totals, "all": grand},
        "note": "Estimate from the prices in config.yaml (USD per 1M tokens); check the provider's bill.",
    }


def money(t: dict[str, Any]) -> str:
    """'$1.23', '$1.23 + calls without a price' or 'price not set'."""
    if t["price_missing"] and not t["cost_usd"]:
        return "price not set"
    text = f"${t['cost_usd']:.2f}"
    if t["price_missing"]:
        text += " + calls without a price"
    if t["no_usage"]:
        text += f" ({t['no_usage']} call(s) reported no tokens)"
    return text
