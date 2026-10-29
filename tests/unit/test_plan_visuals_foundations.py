"""PV0 foundations: stages, statuses, config defaults, rules loaders, memory, health checks
(docs/PLAN_VISUALS_SPEC.md sections 1, 11, 12, 13 and 15)."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

from render_agent.app import plain
from render_agent.app.routes.jobs import progress
from render_agent.config import Price, load_config
from render_agent.errors import PermanentError
from render_agent.memory import AGENT_RULES, approved_rules, ensure_memory, load_agent_rules
from render_agent.pipeline.job import (
    ENABLED_PLAN_STAGES,
    JOB_STATUSES,
    PLAN_STAGES,
    SKIP_ADDED_LATER,
    SKIP_NOT_ENABLED,
    STAGES,
    load_job,
    new_job,
)
from render_agent.plan import health
from render_agent.plan.furnishing import load_furnishing
from render_agent.plan.styles import load_plan_styles

ROOT = Path(__file__).resolve().parents[2]
PLAN_ORDER = [
    "gate_plan_start",
    "p1_trace",
    "gate_plan_check",
    "gate_elevation_choice",
    "p1e_elevations",
    "gate_elevation_check",
    "gate_styles",
    "p2_furnish",
    "gate_furnish_check",
    "p3_plan_build",
    "gate_plan_review",
    "gate_continue",
]


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "drop" / "Some Project.pdf"
    p.parent.mkdir()
    p.write_bytes(b"%PDF-1.7 fake")
    return p


def _set(install: Path, **sections) -> None:
    cfg = yaml.safe_load((install / "config.yaml").read_text(encoding="utf-8"))
    for key, value in sections.items():
        cfg[key] = {**cfg.get(key, {}), **value}
    (install / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


# --- stages and statuses (section 1) -------------------------------------------------------------
def test_stage_order_follows_the_spec():
    assert STAGES[:4] == ["s1_ingest", "s2_sheets", "s3_evidence", "s4_spec"]
    assert STAGES[4:16] == PLAN_ORDER == PLAN_STAGES
    assert STAGES[16:] == [
        "gate_views",
        "s5_checklist",
        "gate_approval",
        "s6_prompts",
        "s7_render",
        "s8_check",
        "s9_review",
    ]


def test_new_job_skips_plan_stages_not_enabled_yet(tmp_path, pdf):
    job = new_job(tmp_path / "jobs", [pdf])
    for name in PLAN_STAGES:
        if name in ENABLED_PLAN_STAGES:
            assert job.stage(name)["status"] == "pending"
        else:
            assert (job.stage(name)["status"], job.stage(name)["reason"]) == ("skipped", SKIP_NOT_ENABLED)
    assert job.first_unfinished_stage() == "s1_ingest"
    for name in STAGES[:4]:
        job.mark_stage_done(name)
    assert job.first_unfinished_stage() == "gate_plan_start"  # PV1: Start the plan check


def test_older_job_gets_plan_stages_added_after_it_started(tmp_path, pdf):
    job = new_job(tmp_path / "jobs", [pdf])
    old = json.loads(job.state_file.read_text(encoding="utf-8"))
    for name in PLAN_STAGES:
        del old["stages"][name]
    for name in STAGES[:4] + ["gate_views"]:
        old["stages"][name]["status"] = "done"
    job.state_file.write_text(json.dumps(old), encoding="utf-8")
    loaded = load_job(job.dir)
    assert list(loaded.state["stages"]) == STAGES  # pipeline order
    for name in PLAN_STAGES:
        assert (loaded.stage(name)["status"], loaded.stage(name)["reason"]) == ("skipped", SKIP_ADDED_LATER)
    assert loaded.stage("gate_views")["status"] == "done"  # what it did keeps its state
    assert loaded.first_unfinished_stage() == "s5_checklist"


def test_progress_does_not_count_skipped_stages(tmp_path, pdf):
    job = new_job(tmp_path / "jobs", [pdf])
    job.mark_stage_done("s1_ingest")
    p = progress(job.dir, job.state)
    assert (p.done, p.total) == (1, len(STAGES) - len(PLAN_STAGES) + len(ENABLED_PLAN_STAGES))


def test_every_status_and_stage_has_plain_words():
    assert set(plain.STATUS_TEXT) >= JOB_STATUSES
    assert all(name in plain.STAGE_TEXT for name in STAGES)
    assert plain.status_text("PLAN_COMPLETE") == "Finished: plans only"
    assert plain.status_text("AWAITING_CONTINUE") == "Plans are ready"
    for status, step in plain.COMING_IN.items():
        assert status in JOB_STATUSES
        assert plain.screen_for(status) == "coming"
        assert plain.coming_text(status) == f"This screen is coming in PV{step}."
    assert plain.screen_for("AWAITING_VIEWS") == "views" and plain.coming_text("AWAITING_VIEWS") == ""


# --- config (section 12) -------------------------------------------------------------------------
def test_config_defaults(install_settings):
    s = install_settings
    assert s.plans.readout_llm_default == "sonnet"
    assert s.renders.prompt_writers_default == ["sonnet", "gpt"]
    assert s.renders.check_llm_default == "gpt"
    assert s.images.offered_providers == ["openai", "muapi"]
    assert {"gpt-5.6-sol", "claude-sonnet-5-5", "gpt-image-2"} <= set(s.pricing)
    assert s.path("memory") == s.work_root / "memory"
    assert s.path("engine") == s.root / "private" / "engine"


def test_config_without_new_sections_still_loads(install):
    cfg = yaml.safe_load((install / "config.yaml").read_text(encoding="utf-8"))
    for key in ("plans", "renders"):
        cfg.pop(key)
    cfg["images"].pop("offered_providers")
    (install / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    s = load_config(install)
    assert s.plans.readout_llm_default == "sonnet" and s.images.offered_providers == ["openai", "muapi"]


@pytest.mark.parametrize(
    "section,value,setting",
    [
        ("plans", {"readout_llm_default": "gemini"}, "plans.readout_llm_default"),
        ("renders", {"prompt_writers_default": ["sonnet", "nope"]}, "renders.prompt_writers_default"),
        ("renders", {"check_llm_default": "nope"}, "renders.check_llm_default"),
    ],
)
def test_unknown_model_name_is_a_plain_config_error(install, section, value, setting):
    _set(install, **{section: value})
    with pytest.raises(PermanentError) as exc:
        load_config(install)
    assert exc.value.code == "CONFIG_INVALID" and setting in exc.value.what_happened


# --- rules loaders (sections 2, 6, 7, 11) --------------------------------------------------------
def test_plan_styles_and_furnishing_load(install_settings):
    k = install_settings.path("knowledge")
    styles = load_plan_styles(k)
    assert list(styles.styles) == ["V1", "V2A", "V2B"]
    assert [s.name for s in styles.styles.values()] == ["Essential", "Sketch", "Showcase"]
    assert not styles.styles["V1"].furnished and styles.styles["V2B"].furnished
    assert 55 <= styles.cutaway.elevation_deg <= 60 and styles.cutaway.wall_cut_mm == 1000
    assert styles.exports.png_width_px == 4000 and styles.exports.jpg_quality == 92
    assert styles.wet_tile.in_range
    furn = load_furnishing(k)
    shower = [p for r in furn.rooms.values() for p in r.pieces if "shower" in p.type]
    assert shower and all(p.only_if == "shower" for p in shower)  # never added where none is drawn


def _edit_yaml(path: Path, fn) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_wet_tile_outside_range_is_a_config_error_named_by_doctor(install):
    _edit_yaml(
        install / "private/knowledge/plan_styles.yaml", lambda d: d["wet_tile"].update(colour="#F4F4F4")
    )
    s = load_config(install)
    with pytest.raises(PermanentError) as exc:
        load_plan_styles(s.path("knowledge"))
    assert "wet_tile.colour" in exc.value.what_happened
    level, msg, fix = health.wet_tile_check(s)
    assert level == "FAIL" and "#F4F4F4" in msg and "wet_tile.colour" in fix


def test_bad_rules_files_name_the_setting(install):
    k = install / "private/knowledge"
    _edit_yaml(k / "plan_styles.yaml", lambda d: d["cutaway"].update(elevation_deg=70))
    with pytest.raises(PermanentError, match="cutaway"):
        load_plan_styles(k)
    _edit_yaml(k / "furnishing.yaml", lambda d: d["rooms"]["entry"]["pieces"][0].update(only_if="pool"))
    with pytest.raises(PermanentError, match="pool"):
        load_furnishing(k)


# --- memory (section 13) -------------------------------------------------------------------------
def test_agent_memory_seeded_once_with_three_owner_rules(install_settings):
    root = ensure_memory(install_settings)
    assert (root / "projects").is_dir()
    rules, warning = approved_rules(install_settings)
    assert warning is None and len(rules) == 3
    assert all(r["source"] == "owner" and r["status"] == "approved" for r in rules)
    assert any("glass screen" in r["text"] for r in rules)
    data = json.loads((root / AGENT_RULES).read_text(encoding="utf-8"))
    data["rules"].append({**data["rules"][0], "id": "x", "status": "pending", "source": "flag"})
    (root / AGENT_RULES).write_text(json.dumps(data), encoding="utf-8")
    ensure_memory(install_settings)  # never overwrites
    assert len(load_agent_rules(install_settings)[0]) == 4
    assert len(approved_rules(install_settings)[0]) == 3  # pending rules are never quoted


def test_unreadable_agent_memory_warns_and_continues(install_settings):
    root = ensure_memory(install_settings)
    (root / AGENT_RULES).write_text("{broken", encoding="utf-8")
    rules, warning = approved_rules(install_settings)
    assert rules == [] and "could not be read" in warning
    assert health.memory_check(install_settings)[0] == "WARN"


# --- health checks (sections 12 and 15) ----------------------------------------------------------
def test_engine_check_names_missing_files(install, install_settings):
    level, msg, fix = health.engine_check(install_settings)  # the test install has no engine
    assert level == "FAIL" and "engine/plan.js" in msg and fix
    shutil.copytree(ROOT / "private/engine", install / "private/engine")
    shutil.copytree(ROOT / "private/assets/lib", install / "private/assets/lib")
    assert health.engine_check(install_settings)[0] == "OK"


def test_pricing_not_set_until_filled(install_settings):
    level, msg, fix = health.pricing_check(install_settings)
    assert level == "WARN" and "Pricing not set" in msg and "claude-sonnet-5-5" in msg and "pricing" in fix
    filled = {m: Price(input=1.0, output=2.0) for m in health.priced_models(install_settings)}
    s = install_settings.model_copy(update={"pricing": filled})
    assert health.pricing_check(s)[0] == "OK"


def test_key_checks_say_saved_or_not_never_the_key(install_settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    s = load_config(install_settings.root)
    checks = health.key_checks(s)
    assert [c[1] for c in checks] == [
        "Anthropic (Claude) key not saved",
        "OpenAI key saved",
        "MuAPI key not saved",
    ]
    assert "sk-secret-value" not in json.dumps(checks)


def test_browser_check_without_playwright_gives_the_fix(monkeypatch):
    monkeypatch.setattr(
        health, "_webgl_probe", lambda: (False, "the headless browser (Playwright) is not installed")
    )
    level, msg, fix = health.browser_check()
    assert level == "WARN" and "playwright install chromium" in fix


# --- fixture leakage (section 16) ----------------------------------------------------------------
def test_fixture_check_covers_engine_and_new_knowledge():
    spec = importlib.util.spec_from_file_location("cnfr", ROOT / "scripts" / "check_no_fixture_refs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "private/engine" in mod.SCAN_DIRS and "private/knowledge" in mod.SCAN_DIRS
    assert ".js" in mod.SUFFIXES and ".yaml" in mod.SUFFIXES
    assert mod.skipped(Path("private/engine/tour_reference.js"))
    assert mod.skipped(Path("private/engine/gen_data_reference.py"))
    assert mod.skipped(Path("private/engine/vendor/three.min.js"))
    assert not mod.skipped(Path("private/engine/plan.js"))
    assert not mod.skipped(Path("private/knowledge/plan_styles.yaml"))
    assert not mod.skipped(Path("private/knowledge/furnishing.yaml"))
    assert not mod.skipped(Path("src/render_agent/x_reference.py"))  # only the engine copies are exempt
