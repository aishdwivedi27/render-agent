import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from render_agent.config import load_config
from render_agent.errors import InputError, PauseJobError, PermanentError
from render_agent.pipeline.job import STAGES, SUBDIRS, load_job, new_job
from render_agent.pipeline.runner import RunContext, run_job
from tests.fakes import write_minimal_plan

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "drop" / "Some Project rev B.pdf"
    p.parent.mkdir()
    p.write_bytes(b"%PDF-1.7 fake")
    return p


@pytest.fixture
def settings(tmp_path):
    shutil.copy(ROOT / "config.example.yaml", tmp_path / "config.yaml")
    return load_config(tmp_path)


def _ok(calls, name):
    def stage(ctx):
        calls.append(name)
        return f"{name}.json"

    return stage


def test_new_job_layout_and_id(tmp_path, pdf):
    job = new_job(tmp_path / "jobs", [pdf], now=datetime(2026, 10, 12, 9, 30, 0))
    assert job.id == "20261012-093000-Some Project rev B"
    for sub in SUBDIRS:
        assert (job.dir / sub).is_dir()
    assert len(job.state["inputs"][0]["sha256"]) == 64
    assert job.first_unfinished_stage() == "s1_ingest"
    again = new_job(tmp_path / "jobs", [pdf], now=datetime(2026, 10, 12, 9, 30, 0))
    assert again.id.endswith("-2")


def test_state_round_trip_and_checkpoints(tmp_path, pdf):
    job = new_job(tmp_path / "jobs", [pdf])
    job.mark_stage_started("s1_ingest")
    job.mark_stage_done("s1_ingest", "ingest.json")
    job.mark_stage_failed("s2_sheets", PermanentError(code="BAD", what_happened="broke"))
    loaded = load_job(job.dir)
    assert loaded.state == json.loads((job.dir / "state.json").read_text(encoding="utf-8"))
    assert loaded.state == job.state
    assert loaded.stage("s1_ingest")["artifact"] == "ingest.json"
    assert loaded.first_unfinished_stage() == "s2_sheets"
    assert loaded.state["errors"][0]["code"] == "BAD"
    assert list(loaded.state["stages"]) == STAGES


def test_load_job_errors(tmp_path):
    with pytest.raises(PermanentError):
        load_job(tmp_path)
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PermanentError) as exc:
        load_job(tmp_path)
    assert exc.value.code == "STATE_CORRUPT"


def test_new_job_rejects_missing_input(tmp_path):
    with pytest.raises(InputError):
        new_job(tmp_path / "jobs", [tmp_path / "nope.pdf"])


def test_runner_resumes_from_checkpoint_and_stops_at_gate(pdf, settings):
    job = new_job(settings.path("jobs"), [pdf])
    calls: list[str] = []
    stages = {n: _ok(calls, n) for n in STAGES}

    def paused(ctx):
        raise PauseJobError(code="KEY", what_happened="key rejected", what_to_do="update the key in .env")

    stages["s3_evidence"] = paused
    plan = stages["s5_checklist"]

    def s5(ctx):
        write_minimal_plan(ctx.job.dir, ROOT / "schemas")
        return plan(ctx)

    stages["s5_checklist"] = s5
    res = run_job(RunContext(job, settings), stages)
    assert res.status == "PAUSED" and res.stopped_at == "s3_evidence"
    assert json.loads((job.dir / "error.json").read_text(encoding="utf-8"))["code"] == "KEY"

    stages["s3_evidence"] = _ok(calls, "s3_evidence")
    res = run_job(RunContext(load_job(job.dir), settings), stages)
    assert res.status == "AWAITING_APPROVAL"
    assert calls == ["s1_ingest", "s2_sheets", "s3_evidence", "s4_spec", "s5_checklist"]


def test_input_error_moves_job_to_failed(pdf, settings):
    job = new_job(settings.path("jobs"), [pdf])

    def bad(ctx):
        raise InputError(code="NOT_PDF", what_happened="not a readable PDF")

    res = run_job(RunContext(job, settings), {"s1_ingest": bad})
    assert res.status == "FAILED"
    assert job.dir.parent == settings.path("failed")
    err = json.loads((job.dir / "error.json").read_text(encoding="utf-8"))
    assert err["what_happened"] == "not a readable PDF"


def test_stub_stage_stops_without_marking_done(pdf, settings):
    job = new_job(settings.path("jobs"), [pdf])
    res = run_job(RunContext(job, settings, dry_run=True), stages={"s1_ingest": None})
    assert res.stopped_at == "s1_ingest" and "not implemented" in res.message
    assert job.stage("s1_ingest")["status"] == "pending"
