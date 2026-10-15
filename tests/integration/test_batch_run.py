"""`run` with several inputs or --batch: one job each, failures isolated, duplicates skipped."""

from __future__ import annotations

import json

from render_agent.cli import main
from tests.conftest import make_pdf


def _jobs(install):
    return sorted(p for p in (install / "jobs").iterdir() if (p / "state.json").is_file())


def test_batch_folder_makes_one_job_per_pdf_and_subfolder(install, monkeypatch, capsys):
    monkeypatch.setenv("RENDER_AGENT_HOME", str(install))
    drop = install / "drop"
    make_pdf(drop / "house_a.pdf", ["ELEVATIONS"])
    make_pdf(drop / "house_b.pdf", ["FLOOR PLAN"])
    (drop / "house_b.txt").write_text("@night=true", encoding="utf-8")
    make_pdf(drop / "set_c" / "plans.pdf", ["FLOOR PLAN"])
    make_pdf(drop / "set_c" / "elevations.pdf", ["ELEVATIONS"])
    (drop / "broken.pdf").write_bytes(b"not a pdf at all")

    assert main(["run", "--batch", str(drop), "--dry-run"]) == 1  # one drop failed
    out = capsys.readouterr().out
    assert "Batch summary:" in out
    for name in ("house_a.pdf", "house_b.pdf", "set_c"):
        assert any(name in line and "AWAITING_APPROVAL" in line for line in out.splitlines()), name
    assert any("broken.pdf" in line and "FAILED" in line for line in out.splitlines())

    jobs = _jobs(install)
    inputs = {
        tuple(sorted(i["name"] for i in json.loads((j / "state.json").read_text())["inputs"])) for j in jobs
    }
    assert ("house_a.pdf",) in inputs
    assert ("house_b.pdf", "house_b.txt") in inputs  # same-named instruction file goes with its PDF
    assert ("elevations.pdf", "plans.pdf") in inputs  # a folder is one drawing set
    assert (install / "failed").is_dir() and any((install / "failed").iterdir())  # broken -> failed/

    # the same PDFs again are skipped, unless --force
    assert main(["run", str(drop / "house_a.pdf"), "--dry-run"]) == 0
    assert "already processed" in capsys.readouterr().out
    assert len(_jobs(install)) == len(jobs)
    assert main(["run", str(drop / "house_a.pdf"), "--dry-run", "--force"]) == 0
    assert len(_jobs(install)) == len(jobs) + 1


def test_several_paths(install, monkeypatch, capsys):
    monkeypatch.setenv("RENDER_AGENT_HOME", str(install))
    a = make_pdf(install / "x" / "a.pdf", ["ELEVATIONS"])
    b = make_pdf(install / "x" / "b.pdf", ["PLAN"])
    assert main(["run", str(a), str(b), "--dry-run"]) == 0
    assert len(_jobs(install)) == 2
    assert main(["run"]) == 1
    assert "Nothing to run" in capsys.readouterr().out


def test_selections_yaml_travels_with_its_pdf(install, monkeypatch):
    from render_agent.pipeline.start import drop_inputs

    a = make_pdf(install / "y" / "a.pdf", ["X"])
    (install / "y" / "a.selections.yaml").write_text("roof_colour: Monument\n", encoding="utf-8")
    assert [p.name for p in drop_inputs(a)] == ["a.pdf", "a.selections.yaml"]
    (install / "y" / "selections.yaml").write_text("roof_colour: Monument\n", encoding="utf-8")
    assert "selections.yaml" in [p.name for p in drop_inputs(install / "y")]
