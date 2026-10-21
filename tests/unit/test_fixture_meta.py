"""Every fixture says where its delivered renders came from, and agent-approved renders record their
drawing check (passed or not_checked), so comparisons can weigh independent and self-made targets apart."""

from __future__ import annotations

import importlib.util

import pytest

from tests.conftest import ROOT

_spec = importlib.util.spec_from_file_location("compare_fixture", ROOT / "scripts" / "compare_fixture.py")
compare_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare_fixture)

FIXTURES = sorted(p for p in (ROOT / "private" / "fixtures").glob("fixture_*") if p.is_dir())


@pytest.mark.parametrize("fixture_dir", FIXTURES, ids=lambda p: p.name)
def test_fixture_records_render_source(fixture_dir):
    assert (fixture_dir / "fixture.yaml").is_file(), f"{fixture_dir.name}: add fixture.yaml"
    meta = compare_fixture.fixture_meta(fixture_dir)
    assert compare_fixture.meta_problems(fixture_dir, meta) == []


def test_missing_metadata_is_unknown_and_reported(tmp_path):
    meta = compare_fixture.fixture_meta(tmp_path)
    assert meta["renders_source"] == "unknown"
    assert compare_fixture.meta_problems(tmp_path, meta)


def test_agent_approved_render_without_a_check_is_a_problem(tmp_path):
    (tmp_path / "final_renders" / "interior").mkdir(parents=True)
    for name in ("kitchen.png", "living.png", "hall.png"):
        (tmp_path / "final_renders" / "interior" / name).write_bytes(b"x")
    (tmp_path / "fixture.yaml").write_text(
        "renders_source: agent_approved\n"
        "renders:\n  kitchen.png: {drawing_check: passed}\n  living.png: {drawing_check: not_checked}\n",
        encoding="utf-8",
    )
    meta = compare_fixture.fixture_meta(tmp_path)
    problems = compare_fixture.meta_problems(tmp_path, meta)
    assert len(problems) == 1 and problems[0].startswith("hall.png")
    target = tmp_path / "final_renders" / "interior"
    assert compare_fixture.target_label(meta, target / "kitchen.png") == "agent-approved"
    assert "weak target" in compare_fixture.target_label(meta, target / "living.png")


def test_comparison_page_shows_the_source(tmp_path):
    fixture = tmp_path / "fixture_x"
    (fixture / "final_renders" / "exterior").mkdir(parents=True)
    (fixture / "fixture.yaml").write_text("renders_source: designer\n", encoding="utf-8")
    job = tmp_path / "job"
    job.mkdir()
    page = compare_fixture.build(fixture, job, tmp_path / "runs").read_text(encoding="utf-8")
    assert "Delivered renders: designer" in page
