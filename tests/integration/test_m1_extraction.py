"""Stages 1-4 on fixture 01 with FakeLLM (no network), plus the M1 rows of the error table."""

import json

import pytest
from tenacity import wait_none

from render_agent.llm.base import BaseLLM, SendResult
from render_agent.pipeline.job import load_job, new_job
from render_agent.pipeline.runner import RunContext, run_job
from tests.conftest import FIXTURE_01, fixture_pdf, make_pdf
from tests.fakes import FakeLLM, minimal_instance


def _sheet(views):
    sheet = minimal_instance(
        json.loads((FIXTURE_01.parents[2] / "schemas" / "sheet_analysis.json").read_text())
    )
    sheet["views"] = views
    return sheet


def _view(view_id, bbox):
    return {
        "view_id": view_id,
        "label": "ELEVATION 1",
        "view_kind": "elevation",
        "facing": "front_street",
        "dwellings_shown": ["ALL"],
        "bbox": dict(zip(("x0", "y0", "x1", "y1"), bbox, strict=True)),
        "description": "street elevation",
    }


def _run(settings, inputs, llm, **kw):
    job = new_job(settings.path("jobs"), inputs)
    return job, run_job(RunContext(job, settings, extras={"llm": llm}, **kw))


def test_fixture01_stages_1_to_4_with_fake_llm(install_settings):
    pdf = fixture_pdf(FIXTURE_01)
    good, bad = _view("S3-ELEV1", (0.1, 0.2, 0.5, 0.6)), _view("S3-BAD", (0.7, 0.7, 0.2, 0.9))
    llm = FakeLLM({"sheet_analysis.page-3": _sheet([good, bad])})
    job, res = _run(install_settings, [pdf], llm)

    assert res.stopped_at == "gate_approval", res.message
    for name in ("s1_ingest", "s2_sheets", "s3_evidence", "s4_spec"):
        assert job.stage(name)["status"] == "done"
    for artifact in ("ingest.json", "sheets/sheet-1.json", "evidence.json", "house_specification.json"):
        assert (job.dir / artifact).is_file(), artifact

    stages = [c["stage"] for c in llm.calls]
    pages = json.loads((job.dir / "ingest.json").read_text())["page_count"]
    assert stages == [f"sheet_analysis.page-{n}" for n in range(1, pages + 1)] + [
        "evidence",
        "spec",
        "checklist",
    ]
    first = llm.calls[0]
    assert first["images"] == [job.dir / "pages" / "page-1.png"]
    assert "<<<BEGIN PAGE_TEXT" in first["prompt"] and "{{" not in first["prompt"]
    assert "<<<BEGIN SHEET_ANALYSES" in llm.calls[-3]["prompt"]
    assert "<<<BEGIN EVIDENCE" in llm.calls[-2]["prompt"]

    sheet3 = json.loads((job.dir / "sheets" / "sheet-3.json").read_text())
    assert sheet3["views"][0]["bbox"] == good["bbox"]
    assert sheet3["views"][1]["bbox"] == {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}
    notes = json.loads((job.dir / "sheets" / "sheet-3.notes.json").read_text())["notes"]
    assert any("S3-BAD" in n and "full sheet" in n for n in notes)


def test_resume_reanalyses_only_missing_sheets(install, install_settings):
    pdf = make_pdf(install / "drop" / "set.pdf", ["SHEET 1 OF 2 SITE PLAN", "SHEET 2 OF 2 ELEVATIONS"])
    job, _ = _run(install_settings, [pdf], FakeLLM())
    (job.dir / "sheets" / "sheet-2.json").unlink()
    for name in ("s2_sheets", "s3_evidence", "s4_spec"):
        job.stage(name)["status"] = "pending"
    job.save()
    llm = FakeLLM()
    run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": llm}))
    assert [c["stage"] for c in llm.calls] == ["sheet_analysis.page-2", "evidence", "spec"]


# ---- error table rows (docs/SPEC.md section 9) touched by M1 ----


def _error(folder):
    return json.loads((folder / "error.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("content", "code"), [(b"PK\x03\x04 not a pdf", "NOT_A_PDF"), (b"", "PDF_EMPTY")])
def test_unreadable_pdf_goes_to_failed(install, install_settings, content, code):
    bad = install / "drop" / "x.pdf"
    bad.parent.mkdir()
    bad.write_bytes(content)
    job, res = _run(install_settings, [bad], FakeLLM())
    assert res.status == "FAILED" and job.dir.parent == install_settings.path("failed")
    assert _error(job.dir)["code"] == code


def test_password_protected_goes_to_failed(install, install_settings):
    pdf = make_pdf(install / "drop" / "locked.pdf", ["X"], password="pw")
    job, res = _run(install_settings, [pdf], FakeLLM())
    assert res.status == "FAILED" and _error(job.dir)["code"] == "PDF_ENCRYPTED"


def test_scanned_pdf_continues_on_images(install, install_settings):
    pdf = make_pdf(install / "drop" / "scan.pdf", [None])
    llm = FakeLLM()
    job, res = _run(install_settings, [pdf], llm)
    assert res.stopped_at == "gate_approval"
    assert "No text layer" in llm.calls[0]["prompt"]
    assert json.loads((job.dir / "ingest.json").read_text())["scanned_pages"] == [1]


class _Scripted(BaseLLM):
    provider = "scripted"

    def __init__(self, results):
        super().__init__("m", 100, 5, 2)
        self.wait = wait_none()
        self.results = results

    def _send(self, prompt, images, schema, schema_name):
        r = self.results.pop(0) if self.results else None
        return r or SendResult("complete", text=json.dumps(minimal_instance(schema)))


def test_invalid_json_is_repaired_once(install, install_settings):
    pdf = make_pdf(install / "drop" / "a.pdf", ["SHEET 1 OF 1"])
    _, res = _run(install_settings, [pdf], _Scripted([SendResult("complete", text="{oops")]))
    assert res.stopped_at == "gate_approval"


def test_refusal_fails_the_stage_without_retry(install, install_settings):
    pdf = make_pdf(install / "drop" / "a.pdf", ["SHEET 1 OF 1"])
    llm = _Scripted([SendResult("refusal", detail="policy")])
    job, res = _run(install_settings, [pdf], llm)
    assert res.status == "FAILED" and res.stopped_at == "s2_sheets"
    assert _error(job.dir)["code"] == "LLM_REFUSED"


def test_missing_key_pauses_and_resumes(install, install_settings, monkeypatch):
    pdf = make_pdf(install / "drop" / "a.pdf", ["SHEET 1 OF 1"])
    job = new_job(install_settings.path("jobs"), [pdf])
    res = run_job(RunContext(job, install_settings))  # no injected LLM, no key
    assert res.status == "PAUSED" and res.stopped_at == "s2_sheets"
    assert _error(job.dir)["code"] == "KEY_MISSING"
    assert job.stage("s1_ingest")["status"] == "done" and job.stage("s2_sheets")["status"] == "pending"
    res = run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": FakeLLM()}))
    assert res.stopped_at == "gate_approval"


def test_source_text_check_tolerates_reordering():
    from render_agent.pipeline.stages.s2_sheets import unsupported_findings

    page = "COLOUR SCHEDULE\nROOF\nGUTTERS\n- WOODLAND GREY\n- WOODLAND GREY\nTOTAL 152.74m²"
    sheet = {
        "findings": [
            {"source_text": "ROOF -WOODLAND GREY"},
            {"source_text": "TOTAL 152.74m²; GUTTERS"},
            {"source_text": "SWIMMING POOL 8x4"},
        ]
    }
    notes = unsupported_findings(sheet, page)
    assert len(notes) == 1 and "SWIMMING POOL" in notes[0]
