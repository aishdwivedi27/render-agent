"""Drawing check on doctored images (docs/MILESTONES.md M3).

Free tests prove the images are doctored as intended and that stage 8 sends the right inputs and
decides outcomes in code. The `paid` test sends each image to the configured vision LLM and is the
real exit-gate check: run it only when asked (RUN_PAID_TESTS=1, key in .env).
"""

from __future__ import annotations

import io
import json

import jsonschema
import pytest
from PIL import Image, ImageChops

from render_agent.config import load_config
from render_agent.pipeline.job import Job
from render_agent.pipeline.runner import RunContext
from render_agent.pipeline.stages.s7_render import Renderer
from render_agent.pipeline.stages.s8_check import Checker
from tests.conftest import ROOT
from tests.doctored import MUST_FAIL, MUST_PASS, VARIANTS, make_check_job, reference_png, render_png
from tests.fakes import FakeLLM

SCHEMA = json.loads((ROOT / "schemas" / "drawing_check.json").read_text(encoding="utf-8"))


def _diff(a: bytes, b: bytes) -> int:
    ia, ib = Image.open(io.BytesIO(a)).convert("RGB"), Image.open(io.BytesIO(b)).convert("RGB")
    box = ImageChops.difference(ia, ib).getbbox()
    return 0 if box is None else (box[2] - box[0]) * (box[3] - box[1])


def test_variants_are_doctored():
    base = render_png("base")
    assert Image.open(io.BytesIO(reference_png())).size == Image.open(io.BytesIO(base)).size
    for v in VARIANTS[1:]:
        assert _diff(base, render_png(v)) > 0, v


def _checker(settings, job_dir, llm):
    job = Job(job_dir, {"stages": {}, "status": "RENDERING"})
    ctx = RunContext(job, settings, extras={"llm": llm, "image_provider": object()})
    return Checker(ctx, Renderer(ctx))


@pytest.mark.parametrize("variant", MUST_FAIL + MUST_PASS)
def test_check_inputs_and_decision(install_settings, tmp_path, variant):
    expected = "fail" if variant in MUST_FAIL else "pass"
    answer = {
        "render_id": "T-D1-FRONT",
        "checks": [{"check": "extra_openings", "result": expected, "note": variant, "confidence": 0.95}],
        "image_match": 0.9,
        "overall": "pass",
        "fix_instructions": [],
    }
    llm = FakeLLM({"drawing_check": answer})
    job_dir = tmp_path / "job"
    entry = make_check_job(job_dir, render_png(variant))
    res = _checker(install_settings, job_dir, llm).check_version(
        "T-D1-FRONT",
        json.loads((job_dir / "output" / "render_index.json").read_text())["renders"]["T-D1-FRONT"][
            "versions"
        ][0],
    )
    assert res["overall"] == expected
    call = llm.calls[0]
    assert [p.name for p in call["images"]] == ["T-D1-FRONT_v1.png", "E1.png"]
    assert call["images"][0].parent.name == "clean"
    prompt = call["prompt"]
    assert "Window W1" in prompt and "Garage door GD1" in prompt and "Single storey" in prompt
    assert "number of panels" in prompt  # tolerated differences are spelled out
    assert "1. window\n2. window\n3. entry door\n4. double garage door" in prompt
    assert entry["references"] == ["references/E1.png"]


@pytest.mark.paid
@pytest.mark.parametrize("variant", MUST_FAIL + MUST_PASS)
def test_paid_vision_check_catches_doctored_image(tmp_path, variant):
    """Real call to the configured LLM (drawing_check stage). Exit gate for M3."""
    settings = load_config(ROOT)
    job_dir = tmp_path / "job"
    make_check_job(job_dir, render_png(variant))
    job = Job(job_dir, {"stages": {}, "status": "RENDERING"})
    ctx = RunContext(job, settings, extras={"image_provider": object()})
    checker = Checker(ctx, Renderer(ctx))
    ver = json.loads((job_dir / "output" / "render_index.json").read_text())["renders"]["T-D1-FRONT"]
    res = checker.check_version("T-D1-FRONT", ver["versions"][0])
    jsonschema.validate(res["answer"], SCHEMA)
    want = "fail" if variant in MUST_FAIL else "pass"
    assert res["overall"] == want, json.dumps(res["answer"], indent=1)


def test_low_confidence_pass_is_not_checked(install_settings, tmp_path):
    answer = {
        "render_id": "T-D1-FRONT",
        "checks": [{"check": "extra_openings", "result": "pass", "note": "looks right", "confidence": 0.95}],
        "image_match": 0.7,
        "overall": "pass",
        "fix_instructions": [],
    }
    job_dir = tmp_path / "job"
    make_check_job(job_dir, render_png(MUST_PASS[0]))
    ver = json.loads((job_dir / "output" / "render_index.json").read_text())["renders"]["T-D1-FRONT"][
        "versions"
    ][0]
    res = _checker(install_settings, job_dir, FakeLLM({"drawing_check": answer})).check_version(
        "T-D1-FRONT", ver
    )
    assert res["overall"] == "unsure"
    assert res["confidence"] == 0.7
    assert res["reason"] == "confidence 0.70 is below 0.85"
