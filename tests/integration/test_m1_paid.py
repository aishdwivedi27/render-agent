"""M1 exit gate on fixture 01 with the configured LLM (real API calls). Run with RUN_PAID_TESTS=1.

Uses the real install root (config.yaml + .env). Hard failures are invented content and a missed
expected contradiction; every other miss is written to grade_report.md for a person to judge,
because the answer key itself may be wrong.
"""

import pytest

from render_agent.config import load_config
from render_agent.pipeline.job import new_job
from render_agent.pipeline.runner import RunContext, run_job
from tests.conftest import FIXTURE_01, ROOT
from tests.grade_extraction import grade_job


@pytest.mark.paid
def test_fixture01_extraction_gate():
    settings = load_config(ROOT)
    job = new_job(settings.path("jobs"), [next(FIXTURE_01.glob("*.pdf"))])
    res = run_job(RunContext(job, settings), {"s5_checklist": None})  # extraction only
    assert res.stopped_at == "s5_checklist", res.message
    report = grade_job(job.dir, FIXTURE_01)
    (job.dir / "grade_report.md").write_text(report.to_markdown(), encoding="utf-8")
    assert not report.hard_failures, report.to_markdown()
