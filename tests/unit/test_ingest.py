import json

import pytest

from render_agent.errors import InputError
from render_agent.pipeline.job import new_job
from render_agent.pipeline.runner import RunContext
from render_agent.pipeline.stages import s1_ingest
from tests.conftest import FIXTURE_01, make_pdf


def _ingest(settings, inputs):
    job = new_job(settings.path("jobs"), inputs)
    s1_ingest.run(RunContext(job, settings))
    return job, json.loads((job.dir / "ingest.json").read_text(encoding="utf-8"))


def test_text_and_scanned_pages(install, install_settings):
    pdf = make_pdf(install / "drop" / "set.pdf", ["SITE PLAN  SCALE 1:200  SHEET 1 OF 2", None])
    notes = install / "drop" / "instructions.txt"
    notes.write_text("@night=true", encoding="utf-8")
    job, info = _ingest(install_settings, [pdf, notes])
    assert info["page_count"] == 2 and info["dpi"] == install_settings.ingest.page_dpi
    assert [p["scanned"] for p in info["pages"]] == [False, True]
    assert info["scanned_pages"] == [2] and "no text layer" in info["notes"][0]
    assert info["instructions"] == "@night=true"
    assert (job.dir / "pages" / "page-1.png").read_bytes()[:4] == b"\x89PNG"
    assert "SITE PLAN" in (job.dir / "pages" / "page-1.txt").read_text(encoding="utf-8")


def test_rerun_skips_when_inputs_unchanged(install, install_settings):
    job, _ = _ingest(install_settings, [make_pdf(install / "d" / "a.pdf", ["PLAN SHEET ONE OF ONE"])])
    png = job.dir / "pages" / "page-1.png"
    png.write_bytes(b"sentinel")
    s1_ingest.run(RunContext(job, install_settings))
    assert png.read_bytes() == b"sentinel"


@pytest.mark.parametrize(
    ("content", "code"),
    [(b"", "PDF_EMPTY"), (b"PK\x03\x04 a zip file", "NOT_A_PDF"), (b"%PDF-1.7 truncated", "NOT_A_PDF")],
)
def test_unreadable_inputs(install, install_settings, content, code):
    bad = install / "drop" / "bad.pdf"
    bad.parent.mkdir()
    bad.write_bytes(content)
    with pytest.raises(InputError) as exc:
        _ingest(install_settings, [bad])
    assert exc.value.code == code and exc.value.what_to_do


def test_password_protected(install, install_settings):
    pdf = make_pdf(install / "drop" / "locked.pdf", ["SECRET"], password="pw")
    with pytest.raises(InputError) as exc:
        _ingest(install_settings, [pdf])
    assert exc.value.code == "PDF_ENCRYPTED" and "unlocked" in exc.value.what_to_do


def test_size_limit(install):
    pdf = make_pdf(install / "drop" / "big.pdf", ["X"])
    with pytest.raises(InputError) as exc:
        s1_ingest._open_pdf(pdf, max_mb=0)
    assert exc.value.code == "PDF_TOO_LARGE"


def test_real_fixture_has_text_layer(install, install_settings):
    pdf = next(FIXTURE_01.glob("*.pdf"))
    _, info = _ingest(install_settings, [pdf])
    assert info["page_count"] >= 3 and info["scanned_pages"] == []
