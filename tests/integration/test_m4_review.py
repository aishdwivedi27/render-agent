"""Stage 9 review with the mock provider: approve, flag one, targeted regeneration, delivery."""

from __future__ import annotations

import json

from render_agent.cli import main
from render_agent.images import MockProvider
from render_agent.pipeline.job import load_job
from render_agent.pipeline.runner import RunContext, run_job
from render_agent.pipeline.stages import s9_review
from render_agent.util.fs import sha256_file
from tests.integration.test_m3_rendering import CheckLLM, _approved, _go

KEEP_CHANGING = {
    "review.json",
    "state.json",
    "render_index.json",
    "budget.json",
    "index.json",
    "manifest.json",
    "report.html",
    "job.log",
}


def _snapshot(job_dir):
    return {
        p.relative_to(job_dir).as_posix(): sha256_file(p)
        for p in job_dir.rglob("*")
        if p.is_file() and p.name not in KEEP_CHANGING and "review.applied" not in p.name
    }


def test_flag_one_regenerates_only_that_render(install_settings):
    job = _approved(install_settings)
    res = _go(install_settings, job)
    assert res.status == "AWAITING_REVIEW" and res.stopped_at == "s9_review", res.message
    review = s9_review.load_review(job.dir)
    ids = sorted(review["renders"])
    assert len(ids) == 14 and all(e["status"] == "pending" for e in review["renders"].values())

    target = "P1-SHARED-KIT"
    (job.dir / "review.txt").write_text(
        f"{target}: make the benchtop darker stone\napprove all\n", encoding="utf-8"
    )
    before = _snapshot(job.dir)
    provider = MockProvider()
    res = _go(install_settings, job, provider=provider)
    assert [c.render_id for c in provider.calls] == [target]  # exactly one image call
    assert "DESIGNER_NOTE" in provider.calls[0].prompt and "darker stone" in provider.calls[0].prompt
    after = _snapshot(job.dir)
    changed = {k for k in after if before.get(k) != after[k]}
    new_files = {k for k in changed if k not in before}
    assert {k for k in changed if k in before} == set()  # every existing file byte-identical
    assert all(
        target in k or k.startswith("output/Dwelling") or k.startswith("output/Common") for k in new_files
    )
    assert f"output/{target}_v2.png" in new_files and f"output/clean/{target}_v2.png" in new_files

    review = s9_review.load_review(job.dir)
    assert review["renders"][target]["status"] == "pending" and review["renders"][target]["version"] == 2
    assert sum(e["status"] == "approved" for e in review["renders"].values()) == 13
    assert res.status == "AWAITING_REVIEW" and target in res.message
    budget = json.loads((job.dir / "budget.json").read_text())
    assert [c["render_id"] for c in budget["calls"] if c["kind"] == "review"] == [target]

    # approved renders are delivered clean; the flagged one is not delivered yet
    assert (job.dir / "output" / "Common").is_dir()
    assert not list((job.dir / "output").rglob("kitchen_*"))
    assert (
        s9_review.decide(review, "P1-SHARED-STREET", "flag", "x")
        == "P1-SHARED-STREET is already approved and locked"
    )

    s9_review.decide(review, target, "approve")
    s9_review.save_review(job.dir, review)
    res = run_job(
        RunContext(
            load_job(job.dir), install_settings, extras={"llm": CheckLLM(), "image_provider": MockProvider()}
        )
    )
    assert res.status == "SUCCEEDED", res.message
    manifest = json.loads((job.dir / "manifest.json").read_text())
    assert len(manifest["renders"]) == 14 and manifest["pending"] == []
    kit = next(e for e in manifest["renders"] if e["render_id"] == target)
    assert kit["version"] == 2 and kit["source"] == f"output/clean/{target}_v2.png"
    assert "knowledge_files" in manifest and "view_set.yaml" in manifest["knowledge_files"]
    report = (job.dir / "report.html").read_text(encoding="utf-8")
    assert "conceptual visualisations" in report and target in report


def test_review_cli(install, monkeypatch, capsys):
    from render_agent.config import load_config

    monkeypatch.setenv("RENDER_AGENT_HOME", str(install))
    settings = load_config(install)
    job = _approved(settings)
    _go(settings, job)
    assert main(["review", str(job.dir)]) == 0
    out = capsys.readouterr().out
    assert "P1-SHARED-KIT" in out and "pending" in out
    assert main(["review", str(job.dir), "--flag", "P1-SHARED-KIT"]) == 1  # a flag needs a note
    state = load_job(job.dir).state
    assert state["dry_run"] is False
    # approving everything finishes the job (no image calls needed)
    monkeypatch.setattr("render_agent.pipeline.stages.s7_render.get_provider", lambda *a, **k: MockProvider())
    assert main(["review", str(job.dir), "--approve", "all"]) == 0
    assert "SUCCEEDED" in capsys.readouterr().out
