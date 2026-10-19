"""M5.5 set-up tools end to end with FakeLLM and the mock provider (no network, no spend):
the per-round image cap, answer-key drafting and the fixture comparison page."""

from __future__ import annotations

import importlib.util
import json
import shutil

import pytest
import yaml

from render_agent.images import MockProvider
from tests.conftest import FIXTURE_01, ROOT
from tests.grade_extraction import grade_job, is_verified, key_items
from tests.integration.test_m3_rendering import _approved, _go


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


draft_answer_key = _script("draft_answer_key")
compare_fixture = _script("compare_fixture")


class PaidMock(MockProvider):
    """Mock images counted as paid, so the round cap applies (still no network, no spend)."""

    paid = True


def _capped(settings, cap):
    return settings.model_copy(
        update={"images": settings.images.model_copy(update={"max_images_per_round": cap, "concurrency": 1})}
    )


def test_round_cap_pauses_then_resumes_after_owner_raises_it(install_settings):
    capped = _capped(install_settings, 3)
    job = _approved(capped)
    res = _go(capped, job, provider=PaidMock())
    assert res.status == "PAUSED", res.message
    err = json.loads((job.dir / "error.json").read_text(encoding="utf-8"))
    assert err["code"] == "ROUND_CAP_REACHED"
    budget = json.loads((job.dir / "budget.json").read_text(encoding="utf-8"))
    assert budget["round_cap"] == 3 and sum(c["paid"] for c in budget["calls"]) == 3
    raised = _capped(install_settings, 100)  # owner approves a higher cap, then Retry
    res = _go(raised, job, provider=PaidMock())
    assert res.status == "AWAITING_REVIEW", res.message
    budget = json.loads((job.dir / "budget.json").read_text(encoding="utf-8"))
    assert budget["round_cap"] == 100
    rendered = json.loads((job.dir / "output" / "render_index.json").read_text(encoding="utf-8"))["renders"]
    assert all(len(r["versions"]) == 1 for r in rendered.values())  # nothing paid twice


def test_draft_compare_on_a_fake_job(install_settings, tmp_path):
    job = _approved(install_settings)
    res = _go(install_settings, job)
    assert res.status == "AWAITING_REVIEW", res.message

    # 1. answer-key draft: every value unverified, nothing NOT FOUND, readable by the grader
    fixture = tmp_path / "fixture_new"
    (fixture / "final_renders" / "exterior").mkdir(parents=True)
    out = draft_answer_key.write_draft(job.dir, fixture)
    key = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert key["status"] == "unverified" and key["must_not_appear"] == []
    assert not (fixture / "answer_key.yaml").exists()
    items = key_items(key)
    assert items and all(str(v).strip().upper() != "NOT FOUND" for _, v in items)
    body = " ".join(line for line in out.read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
    assert "verified: false" in body and "verified: true" not in body
    assert not is_verified(key)
    shutil.copy(out, fixture / "answer_key.yaml")
    report = grade_job(job.dir, fixture)
    assert report.count("matched") == len([i for i in report.items if i.status != "manual"])

    # 2. comparison page on a fixture with a hostile delivered file name and a review flag
    (fixture / "final_renders" / "exterior" / "Front & 'street' view.png").write_bytes(
        (job.dir / "output" / "clean").glob("*.png").__next__().read_bytes()
    )
    review = json.loads((job.dir / "review.json").read_text(encoding="utf-8"))
    rid = next(iter(review["renders"]))
    review["renders"][rid]["history"].append({"action": "flag", "category": "<b>wrong</b>", "note": "x"})
    (job.dir / "review.json").write_text(json.dumps(review), encoding="utf-8")
    page = compare_fixture.build(fixture, job.dir, tmp_path / "runs").read_text(encoding="utf-8")
    for section in ("1. Extraction grade", "2. Planned vs delivered", "Drawing check:", "Designer review:"):
        assert section in page
    assert "NOT verified" in page and "<b>wrong" not in page and "&lt;b&gt;wrong" in page
    assert "Front &amp; &#x27;street&#x27; view.png" in page
    assert list((tmp_path / "runs" / "fixture_new" / "img").glob("agent_*.png"))


def test_compare_survives_a_job_with_nothing_in_it(tmp_path):
    job = tmp_path / "empty_job"
    job.mkdir()
    page = compare_fixture.build(tmp_path / "no_fixture", job, tmp_path / "runs").read_text(encoding="utf-8")
    assert page.count("not available") >= 2


@pytest.mark.skipif(not FIXTURE_01.is_dir(), reason="private fixtures not present")
def test_compare_on_fixture_01(install_settings, tmp_path):
    job = _approved(install_settings)
    _go(install_settings, job)
    page = compare_fixture.build(FIXTURE_01, job.dir, tmp_path / "runs").read_text(encoding="utf-8")
    assert "Fixture fixture_01" in page and "must_not_appear hits" in page
    delivered = compare_fixture.delivered(FIXTURE_01)
    assert delivered and all(p.parent.name in ("exterior", "interior") for p in delivered)
