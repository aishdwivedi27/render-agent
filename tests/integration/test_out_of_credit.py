"""Running out of API credit (LLM or images) pauses the job with a plain message; resume continues."""

from __future__ import annotations

import json

from tenacity import wait_none

from render_agent.images import MockProvider
from render_agent.llm.base import is_out_of_credit, out_of_credit
from render_agent.pipeline.job import load_job
from render_agent.pipeline.runner import RunContext, run_job
from tests.integration.test_m2_planning import _llm, _start
from tests.integration.test_m3_rendering import CheckLLM, _approved, _index


class BrokeLLM(CheckLLM):
    """Out of credit on the first call to `stage`, fine afterwards (the account was topped up)."""

    def __init__(self, stage):
        super().__init__()
        self.broke_stage = stage

    def call_json(self, stage, prompt, images, schema, schema_name):
        if stage.startswith(self.broke_stage) and self.broke_stage:
            self.broke_stage = ""
            raise out_of_credit("OpenAI", "You exceeded your current quota (insufficient_quota)")
        return super().call_json(stage, prompt, images, schema, schema_name)


def test_phrases():
    assert is_out_of_credit("Your credit balance is too low to access the Anthropic API")
    assert is_out_of_credit("You exceeded your current quota, please check your plan and billing details")
    assert is_out_of_credit("anything", status=402)
    assert not is_out_of_credit("Rate limit reached for requests")


def test_llm_out_of_credit_pauses_extraction_then_resumes(install_settings):
    llm = BrokeLLM("evidence")
    llm.canned = _llm().canned
    pdf_job, _, _ = _start(install_settings)  # a finished job, only to reuse the drop folder
    from render_agent.pipeline.job import new_job

    job = new_job(install_settings.path("jobs"), [next((pdf_job.dir / "input").glob("*.pdf"))])
    res = run_job(RunContext(job, install_settings, extras={"llm": llm}))
    assert res.status == "PAUSED" and res.stopped_at == "s3_evidence"
    assert "out of credit" in res.message and "billing" in res.message.lower() and "resume" in res.message
    err = json.loads((job.dir / "error.json").read_text())
    assert err["code"] == "OUT_OF_CREDIT" and err["kind"] == "PauseJobError"
    state = load_job(job.dir).state
    assert state["status"] == "PAUSED" and state["stages"]["s2_sheets"]["status"] == "done"
    calls_before = len(llm.calls)

    res = run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": llm}))  # topped up
    assert res.status == "AWAITING_APPROVAL", res.message
    assert not any(c["stage"].startswith("sheet_analysis") for c in llm.calls[calls_before:])  # not redone


def test_image_out_of_credit_pauses_rendering_and_keeps_finished(install_settings):
    job = _approved(install_settings)
    s1 = install_settings.model_copy(
        update={"images": install_settings.images.model_copy(update={"concurrency": 1})}
    )
    broke = out_of_credit("OpenAI images", "Billing hard limit has been reached")
    extras = {"llm": CheckLLM(), "image_wait": wait_none()}
    res = run_job(
        RunContext(
            load_job(job.dir),
            s1,
            extras={**extras, "image_provider": MockProvider({"P1-SHARED-KIT": [broke]})},
        )
    )
    assert res.status == "PAUSED" and res.stopped_at == "s7_render"
    assert json.loads((job.dir / "error.json").read_text())["code"] == "OUT_OF_CREDIT"
    done = [k for k, v in _index(job).items() if v["status"] == "rendered"]
    assert done and "P1-SHARED-KIT" not in done
    res = run_job(RunContext(load_job(job.dir), s1, extras={**extras, "image_provider": MockProvider()}))
    assert res.status == "AWAITING_REVIEW"
    assert all(len(_index(job)[k]["versions"]) == 1 for k in done)  # nothing re-rendered or paid twice
