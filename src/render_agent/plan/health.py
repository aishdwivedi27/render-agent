"""Plan visuals health checks for `doctor` and Help (PLAN_VISUALS_SPEC sections 12 and 15).

Each check is (level, message, fix): level OK, WARN or FAIL; the fix is plain words for whoever looks
after Render Agent. Nothing here costs money: keys are only checked for presence (the no-cost key
test runs from Help → Check keys now), and the 3D check uses a local headless browser.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from render_agent.config import KEY_ENV_VARS, Settings
from render_agent.errors import AgentError

log = logging.getLogger("render_agent.doctor")
Check = tuple[str, str, str]

ENGINE_FILES = ("plan.js", "vendor/three.min.js", "vendor/GLTFLoader.js")
ASSET_FILES = ("lib/ph_asset_index.json",)
KEY_LABELS = {"anthropic": "Anthropic (Claude)", "openai": "OpenAI", "muapi": "MuAPI"}
BROWSER_FIX = (
    "Install the headless browser: pip install playwright, then python -m playwright install chromium. "
    "Essential and Sketch 2D plans still build without it."
)


def engine_check(settings: Settings) -> Check:
    engine, assets = settings.path("engine"), settings.path("assets")
    missing = [f"engine/{f}" for f in ENGINE_FILES if not (engine / f).is_file()]
    missing += [f"assets/{f}" for f in ASSET_FILES if not (assets / f).is_file()]
    if not any((engine / "models").glob("*.json")):
        missing.append("engine/models/*.json")
    try:
        from render_agent.plan.styles import load_plan_styles

        styles = load_plan_styles(settings.path("knowledge"), check_tile=False)
        missing += [
            f"knowledge/{s.thumbnail}"
            for s in styles.styles.values()
            if not (settings.path("knowledge") / s.thumbnail).is_file()
        ]
    except AgentError:
        missing.append("knowledge/plan_styles.yaml")
    if missing:
        return (
            "FAIL",
            f"Plan engine files missing: {', '.join(missing)}",
            "Reinstall Render Agent, or restore the private files (engine, assets, knowledge).",
        )
    return "OK", "Plan engine files present", ""


@lru_cache(maxsize=1)
def _webgl_probe() -> tuple[bool, str]:
    """(ok, detail). Launches local headless Chromium once per process; no network."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        return False, "the headless browser (Playwright) is not installed"
    script = (
        "() => { const c = document.createElement('canvas');"
        " return !!(c.getContext('webgl2') || c.getContext('webgl')); }"
    )
    try:
        with sync_playwright() as pw:
            for args in ([], ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]):
                browser = pw.chromium.launch(headless=True, args=args)
                try:
                    page = browser.new_page()
                    if page.evaluate(script):
                        return True, "software rendering" if args else "graphics card"
                finally:
                    browser.close()
    except Exception as exc:  # browser not downloaded, blocked by policy, ...
        log.info("headless browser check failed: %s", exc)
        return False, "the headless browser could not start"
    return False, "the headless browser cannot draw 3D (no WebGL)"


def browser_check() -> Check:
    ok, detail = _webgl_probe()
    if ok:
        return "OK", f"Headless browser can draw 3D ({detail})", ""
    return "WARN", f"3D cutaways and Showcase plans: {detail}", BROWSER_FIX


def key_checks(settings: Settings) -> list[Check]:
    out: list[Check] = []
    for provider, label in KEY_LABELS.items():
        if settings.secret(KEY_ENV_VARS[provider]):
            out.append(("OK", f"{label} key saved", ""))
        else:
            out.append(
                (
                    "WARN",
                    f"{label} key not saved",
                    f"Open Settings, paste the {label} key and press Test connection. "
                    "Practice runs need no key.",
                )
            )
    return out


def priced_models(settings: Settings) -> list[str]:
    """Every model a plan visuals job can use: the llm.choices models and the image model."""
    return sorted({c.model for c in settings.llm.choices.values()} | {settings.images.openai.model})


def pricing_check(settings: Settings) -> Check:
    unset = []
    for model in priced_models(settings):
        price = settings.pricing.get(model)
        if price is None or price.input is None or price.output is None:
            unset.append(model)
    if unset:
        return (
            "WARN",
            f"Pricing not set: {', '.join(unset)}",
            f"Fill 'pricing' in {settings.config_file} (USD per 1 million tokens, from each provider's "
            "pricing page). Until then costs show as 'price not set'.",
        )
    return "OK", "Pricing set for every model", ""


def wet_tile_check(settings: Settings) -> Check:
    from render_agent.plan.styles import load_plan_styles

    try:
        tile = load_plan_styles(settings.path("knowledge"), check_tile=False).wet_tile
    except AgentError as err:
        return "FAIL", f"Plan styles: {err.what_happened}", err.what_to_do
    ratio = tile.contrast_with_white
    if not tile.in_range:
        return (
            "FAIL",
            f"Wet-area tile {tile.colour} contrast {ratio:.1f}:1 is outside {tile.contrast_min} to "
            f"{tile.contrast_max}",
            "Choose a mid-grey for 'wet_tile.colour' in plan_styles.yaml: darker than white fixtures, "
            "lighter than charcoal.",
        )
    return (
        "OK",
        f"Wet-area tile contrast {ratio:.1f}:1 (allowed {tile.contrast_min} to {tile.contrast_max})",
        "",
    )


def memory_check(settings: Settings) -> Check:
    from render_agent.memory import ensure_memory, load_agent_rules

    try:
        ensure_memory(settings)  # like the other working folders, created on first check
    except Exception as exc:
        return (
            "WARN",
            "Memory folder could not be prepared",
            (f"Check that {settings.path('memory')} can be written to ({type(exc).__name__})."),
        )
    rules, warning = load_agent_rules(settings)
    if warning:
        return (
            "WARN",
            "Agent memory file unreadable; jobs run without remembered rules",
            (
                "Restore memory/agent_rules.json from a backup, or delete it to start again from the "
                "owner rules."
            ),
        )
    approved = sum(1 for r in rules if r["status"] == "approved")
    pending = len(rules) - approved
    return "OK", f"Agent memory: {approved} approved rule(s), {pending} other", ""


def plan_checks(settings: Settings, browser: bool = True) -> list[Check]:
    out = [engine_check(settings)]
    if browser:
        out.append(browser_check())
    out += key_checks(settings)
    out += [pricing_check(settings), wet_tile_check(settings), memory_check(settings)]
    return out
