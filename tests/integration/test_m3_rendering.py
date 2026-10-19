"""Stages 7-8 end to end with the mock provider and FakeLLM (no network, no spend)."""

from __future__ import annotations

import json
import re

from PIL import Image
from tenacity import wait_none

from render_agent.cli import main
from render_agent.errors import PauseJobError, PermanentError, TransientError
from render_agent.images import MockProvider
from render_agent.pipeline import gate
from render_agent.pipeline.job import load_job
from render_agent.pipeline.runner import RunContext, run_job
from tests.conftest import FIXTURE_01, fixture_pdf, make_pdf
from tests.fakes import FakeLLM
from tests.integration.test_m2_planning import _llm, _start

RID_RE = re.compile(r"^Render id: (\S+)$", re.M)


class CheckLLM(FakeLLM):
    """FakeLLM whose drawing_check answers follow a per-render script of 'pass' | 'fail' | 'unsure' |
    an exception; renders not in the script pass."""

    def __init__(self, script=None):
        super().__init__(_llm().canned)
        self.script = {k: list(v) for k, v in (script or {}).items()}

    def call_json(self, stage, prompt, images, schema, schema_name):
        if stage != "drawing_check":
            return super().call_json(stage, prompt, images, schema, schema_name)
        self.calls.append({"stage": stage, "prompt": prompt, "images": images, "schema_name": schema_name})
        rid = RID_RE.search(prompt).group(1)
        queue = self.script.get(rid)
        result = queue.pop(0) if queue else "pass"
        if isinstance(result, Exception):
            raise result
        note = "ok" if result == "pass" else "extra window on garage wall"
        checks = [{"check": "extra_openings", "result": result, "note": note, "confidence": 0.95}]
        fix = ["Remove the window on the garage wall."] if result == "fail" else []
        return {
            "render_id": rid,
            "checks": checks,
            "image_match": 0.95,
            "overall": "pass",
            "fix_instructions": fix,
        }


def _approved(settings):
    job, _, _ = _start(settings, "@select entry_door=pivot, timber")
    need = [n["key"] for n in json.loads((job.dir / "approval.json").read_text())["needs_choice"]]
    assert gate.approve(job, {k: "X" for k in need}, by="test") == []
    return load_job(job.dir)


def _go(settings, job, provider=None, llm=None):
    extras = {
        "llm": llm or CheckLLM(),
        "image_provider": provider or MockProvider(),
        "image_wait": wait_none(),
    }
    return run_job(RunContext(load_job(job.dir), settings, extras=extras))


def _index(job):
    return json.loads((job.dir / "output" / "render_index.json").read_text())["renders"]


def _checks(job):
    return json.loads((job.dir / "checks" / "index.json").read_text())["renders"]


def test_render_and_check_all(install_settings):
    job = _approved(install_settings)
    llm = CheckLLM({"P1-D1-REAR": ["fail", "pass"], "P1-SHARED-KIT": ["unsure"]})
    res = _go(install_settings, job, llm=llm)
    assert res.stopped_at == "s9_review" and res.status == "AWAITING_REVIEW", res.message
    idx = _index(job)
    assert len(idx) == 14 and all(r["status"] == "rendered" for r in idx.values())
    for rid, rec in idx.items():
        v = rec["versions"][0]
        draft, clean = job.dir / v["file"], job.dir / v["clean_file"]
        assert v["file"] == f"output/{rid}_v1.png" and clean.parent.name == "clean"
        assert Image.open(draft).tobytes() != Image.open(clean).tobytes()  # DRAFT overlay on the copy
    # failed check -> one regeneration with the fix, then pass
    rear = idx["P1-D1-REAR"]["versions"]
    assert [v["version"] for v in rear] == [1, 2] and rear[1]["kind"] == "auto_retry"
    assert "Remove the window on the garage wall." in (job.dir / "prompts" / "P1-D1-REAR_v2.txt").read_text()
    assert (job.dir / "output" / "P1-D1-REAR_v1.png").is_file()  # v1 kept
    checks = _checks(job)
    assert checks["P1-D1-REAR"]["outcome"] == "Passed drawing check" and checks["P1-D1-REAR"]["version"] == 2
    assert checks["P1-SHARED-KIT"]["outcome"] == "Not checked"
    assert checks["P1-SHARED-STREET"]["outcome"] == "Passed drawing check"
    # the check sees the clean render first, then the same references as the render
    call = next(c for c in llm.calls if c["stage"] == "drawing_check" and "P1-SHARED-STREET" in c["prompt"])
    assert call["images"][0].name == "P1-SHARED-STREET_v1.png" and call["images"][0].parent.name == "clean"
    assert call["images"][1].name == "E1.png" and "No documented elevation matches" in call["prompt"]
    assert "{{" not in call["prompt"]
    budget = json.loads((job.dir / "budget.json").read_text())
    assert sum(c["kind"] == "auto_retry" for c in budget["calls"]) == 1


def test_second_fail_and_model_overall_ignored(install_settings):
    job = _approved(install_settings)
    llm = CheckLLM({"P1-D1-REAR": ["fail", "fail"]})
    _go(install_settings, job, llm=llm)
    c = _checks(job)["P1-D1-REAR"]
    assert c["outcome"] == "Failed drawing check" and c["version"] == 2
    saved = json.loads((job.dir / c["check_file"]).read_text())
    assert saved["overall"] == "fail" and saved["overall_from_model"] == "pass"  # code decides
    assert len(_index(job)["P1-D1-REAR"]["versions"]) == 2  # only one automatic retry


def test_check_error_is_not_checked(install_settings):
    job = _approved(install_settings)
    err = PermanentError(code="LLM_SCHEMA_INVALID", what_happened="bad answer")
    _go(install_settings, job, llm=CheckLLM({"P1-SHARED-STREET": [err]}))
    c = _checks(job)["P1-SHARED-STREET"]
    assert c["outcome"] == "Not checked"
    assert "bad answer" in json.loads((job.dir / c["check_file"]).read_text())["reason"]


def test_one_render_failing_does_not_stop_others(install_settings):
    job = _approved(install_settings)
    faults = {
        "P1-D1-REAR": [PermanentError(code="IMAGE_REJECTED", what_happened="content policy")],
        "P1-SHARED-KIT": [TransientError(code="IMAGE_TRANSIENT", what_happened="503")] * 5,
        "P1-SHARED-STREET": [TransientError(code="IMAGE_TRANSIENT", what_happened="429")] * 2,
    }
    res = _go(install_settings, job, provider=MockProvider(faults))
    assert res.status == "AWAITING_REVIEW", res.message
    idx = _index(job)
    assert (
        idx["P1-D1-REAR"]["status"] == "failed"
        and "content policy" in idx["P1-D1-REAR"]["error"]["what_happened"]
    )
    assert "FIXED IDENTITY" in idx["P1-D1-REAR"]["failed_prompt"]  # prompt shown at review
    assert idx["P1-SHARED-KIT"]["status"] == "failed"
    assert idx["P1-SHARED-STREET"]["status"] == "rendered"
    assert idx["P1-SHARED-STREET"]["versions"][0]["attempts"] == 3
    assert sum(r["status"] == "rendered" for r in idx.values()) == 12
    assert _checks(job)["P1-D1-REAR"]["outcome"] is None


def test_key_problem_pauses_then_resume_skips_finished(install_settings):
    job = _approved(install_settings)
    pause = PauseJobError(code="QUOTA", what_happened="billing limit", what_to_do="Top up")
    provider = MockProvider({"P1-SHARED-KIT": [pause]})
    install_settings_1 = install_settings.model_copy(
        update={"images": install_settings.images.model_copy(update={"concurrency": 1})}
    )
    res = _go(install_settings_1, job, provider=provider)
    assert res.status == "PAUSED" and res.stopped_at == "s7_render"
    done_before = {k: v for k, v in _index(job).items() if v["status"] == "rendered"}
    assert done_before and len(done_before) < 14
    before = {k: (job.dir / v["versions"][0]["file"]).read_bytes() for k, v in done_before.items()}
    provider2 = MockProvider()
    res = _go(install_settings, job, provider=provider2)
    assert res.status == "AWAITING_REVIEW"
    assert {c.render_id for c in provider2.calls}.isdisjoint(done_before)  # not re-rendered
    after = _index(job)
    assert all(len(after[k]["versions"]) == 1 for k in done_before)
    assert all((job.dir / after[k]["versions"][0]["file"]).read_bytes() == b for k, b in before.items())


def test_not_ready_renders_are_skipped(install_settings):
    job = _approved(install_settings)
    res = run_job(RunContext(job, install_settings, extras={"llm": CheckLLM()}), stages={"s7_render": None})
    assert res.stopped_at == "s7_render"
    index = json.loads((job.dir / "prompts" / "index.json").read_text())
    for r in index["renders"]:
        if r["render_id"] == "P1-SHARED-KIT":
            r["readiness"] = "NOT READY"
    (job.dir / "prompts" / "index.json").write_text(json.dumps(index))
    provider = MockProvider()
    _go(install_settings, job, provider=provider)
    assert _index(job)["P1-SHARED-KIT"]["status"] == "skipped"
    assert "P1-SHARED-KIT" not in {c.render_id for c in provider.calls}


def test_dry_run_cli_reaches_review(install, monkeypatch, capsys):
    """`run --dry-run` on a real fixture PDF: mock renders with DRAFT overlay, checks 'Not checked'."""
    monkeypatch.setenv("RENDER_AGENT_HOME", str(install))
    pdf = fixture_pdf(FIXTURE_01) or make_pdf(install / "x.pdf", ["ELEVATION"])
    assert main(["run", str(pdf), "--dry-run"]) == 0
    job_dir = next((install / "jobs").iterdir())
    approval = json.loads((job_dir / "approval.json").read_text())
    need = [n["key"] for n in approval["needs_choice"]]
    args = ["approve", str(job_dir)] + [a for k in need for a in ("--select", f"{k}=X")]
    assert main(args) == 0
    assert "AWAITING_REVIEW" in capsys.readouterr().out
    checks = json.loads((job_dir / "checks" / "index.json").read_text())["renders"]
    assert all(c["outcome"] in ("Not checked", None) for c in checks.values())
