"""PV1 Start the plan check (docs/PLAN_VISUALS_SPEC.md section 2): directives, options, cost estimate,
the recorded choice and the gate for CLI jobs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from render_agent.config import load_config
from render_agent.pipeline import plan_start
from render_agent.pipeline.job import PLAN_STAGES, STAGES, new_job
from render_agent.pipeline.runner import RunContext, run_job
from render_agent.util.directives import parse_directives, with_directive_line


@pytest.fixture
def job(tmp_path):
    pdf = tmp_path / "drop" / "Some Project.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.7 fake")
    j = new_job(tmp_path / "jobs", [pdf])
    (j.dir / "input" / plan_start.OPTIONS_FILE).write_text("@night=true\n", encoding="utf-8")
    _ingest(j.dir, "free words\n")
    return j


def _ingest(job_dir: Path, instructions: str) -> None:
    (job_dir / "ingest.json").write_text(json.dumps({"instructions": instructions}), encoding="utf-8")


def _sheet(job_dir: Path, n: int, kind: str) -> None:
    (job_dir / "sheets" / f"sheet-{n}.json").write_text(json.dumps({"sheet_type": kind}), encoding="utf-8")


def _priced(install: Path) -> None:
    cfg = yaml.safe_load((install / "config.yaml").read_text(encoding="utf-8"))
    cfg["pricing"] = {
        "claude-sonnet-5-5": {"input": 3.0, "output": 15.0},
        "gpt-5.6-sol": {"input": 1.0, "output": 10.0},
    }
    cfg["plans"] = {
        "readout_llm_default": "sonnet",
        "readout_estimate_tokens": {"input": 100000, "output": 10000},
    }
    (install / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_directives_parse_and_invalid_values_are_ignored():
    d = parse_directives("@readout_llm=Both\n@plans_mode=renders_only\n")
    assert (d.readout_llm, d.plans_mode, d.ignored) == ("both", "renders_only", [])
    bad = parse_directives("@readout_llm=gemini\n@plans_mode=maybe\n")
    assert bad.readout_llm is None and bad.plans_mode is None
    assert len(bad.ignored) == 2 and all("invalid value" in i for i in bad.ignored)


def test_directive_line_is_replaced_not_duplicated():
    text = "@readout_llm=openai\n@readout_llm_x=1\nwords\n"
    assert with_directive_line(text, "readout_llm", "@readout_llm=both") == (
        "@readout_llm_x=1\nwords\n@readout_llm=both\n"
    )


def test_default_is_claude_from_config_and_cost_not_set_without_prices(job, install_settings):
    out = plan_start.options(job.dir, install_settings)
    assert (out["readout_llm"], out["plans_mode"], out["from_file"]) == ("claude", "plans", {})
    assert [o["id"] for o in out["options"]] == ["claude", "openai", "both"]
    assert all(o["available"] and o["cost"] == "price not set" for o in out["options"])
    assert out["info"].startswith("Claude, OpenAI or both read your drawing")


def test_cost_per_option_both_is_the_sum(job, install):
    _priced(install)
    settings = load_config(install)
    _sheet(job.dir, 1, "floor_plan")
    _sheet(job.dir, 2, "elevations")
    (job.dir / "sheets" / "sheet-1.notes.json").write_text("{}", encoding="utf-8")
    costs = {o["id"]: o["cost"] for o in plan_start.options(job.dir, settings)["options"]}
    # 1 floor plan sheet: 100k in + 10k out. Claude 0.30 + 0.15; OpenAI 0.10 + 0.10
    assert costs == {"claude": "about $0.45", "openai": "about $0.20", "both": "about $0.65"}
    _sheet(job.dir, 3, "mixed")
    assert plan_start.options(job.dir, settings)["options"][0]["cost"] == "about $0.90"


def test_instruction_file_preselects(job, install_settings):
    _ingest(job.dir, "@readout_llm=openai\nfree words\n")
    out = plan_start.options(job.dir, install_settings)
    assert out["readout_llm"] == "openai" and out["from_file"] == {"readout_llm": "openai"}
    _ingest(job.dir, "@plans_mode=renders_only\n")
    assert plan_start.options(job.dir, install_settings)["plans_mode"] == "renders_only"


def test_choose_plans_both_writes_file_and_directives(job, install_settings):
    data = plan_start.choose(job, install_settings, "plans", "both", by="test")
    saved = json.loads((job.dir / plan_start.CHOSEN).read_text(encoding="utf-8"))
    assert saved == data and data["readout_models"] == ["sonnet", "gpt"]
    assert plan_start.is_chosen(job.dir) and plan_start.readout_text(job.dir) == "Claude + OpenAI"
    instructions = json.loads((job.dir / "ingest.json").read_text(encoding="utf-8"))["instructions"]
    assert "@plans_mode=plans" in instructions and "@readout_llm=both" in instructions
    opts = (job.dir / "input" / plan_start.OPTIONS_FILE).read_text(encoding="utf-8")
    assert opts.startswith("@night=true\n") and "@readout_llm=both" in opts
    assert job.stage("gate_plan_check")["status"] == "pending"


def test_choose_renders_only_skips_every_plan_stage(job, install_settings):
    plan_start.choose(job, install_settings, "plans", "claude", by="test")
    plan_start.choose(job, install_settings, "renders_only", "claude", by="test")  # changed their mind
    assert plan_start.readout_text(job.dir) == "Exterior renders only"
    instructions = json.loads((job.dir / "ingest.json").read_text(encoding="utf-8"))["instructions"]
    assert "@plans_mode=renders_only" in instructions and "@readout_llm" not in instructions
    for name in PLAN_STAGES:
        if name != "gate_plan_start":
            assert job.stage(name)["status"] == "skipped"
    assert job.stage("gate_plan_check")["reason"] == plan_start.SKIP_RENDERS_ONLY


def test_unreadable_choice_shows_the_screen_again(job):
    (job.dir / "plan").mkdir()
    (job.dir / plan_start.CHOSEN).write_text("{not json", encoding="utf-8")
    assert not plan_start.is_chosen(job.dir) and plan_start.readout_text(job.dir) == ""


def _run(job, settings):
    stages = {n: (lambda ctx: None) for n in STAGES if n.startswith("s") or n == "p1_trace"}
    return run_job(RunContext(job, settings), stages)


def test_cli_job_passes_the_gate_as_renders_only(job, install_settings):
    result = _run(job, install_settings)
    assert result.stopped_at == "gate_approval"  # straight on as before (no plan needs approving here)
    assert job.stage("gate_plan_start")["status"] == job.stage("gate_views")["status"] == "done"
    assert (
        json.loads((job.dir / plan_start.CHOSEN).read_text(encoding="utf-8"))["plans_mode"] == "renders_only"
    )
    assert job.stage("gate_plan_check")["status"] == "skipped"


def test_cli_job_with_plans_mode_stops_at_the_plan_check(job, install_settings):
    _ingest(job.dir, "@plans_mode=plans\n@readout_llm=openai\n")
    result = _run(job, install_settings)
    assert (result.status, result.stopped_at) == ("AWAITING_PLAN_CHECK", "gate_plan_check")
    assert plan_start.chosen(job.dir)["readout_models"] == ["gpt"]


def test_app_job_waits_for_the_designer(job, install_settings):
    job.state["designer"] = {"output": "somewhere"}
    job.save()
    result = _run(job, install_settings)
    assert (result.status, result.stopped_at) == ("AWAITING_PLAN_START", "gate_plan_start")
    assert job.stage("gate_plan_start")["status"] == "pending"
