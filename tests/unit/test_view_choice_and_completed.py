"""Owner feedback 21 Oct 2026: plain-words view choice, step/part progress, Output\\Completed."""

from __future__ import annotations

import json
from pathlib import Path

from render_agent.app import designer, plain
from render_agent.config import load_config
from render_agent.pipeline.job import new_job
from render_agent.pipeline.view_choice import with_views_line
from render_agent.validate.checklist_validator import load_view_set
from render_agent.validate.view_words import describe, interpret
from tests.conftest import make_pdf


def _vs(install: Path) -> dict:
    return load_view_set(install / "private" / "knowledge")


def test_free_text_that_dropped_views_now_names_front_and_rear(install):
    # this exact request planned only the front: '@views' matched labels word for word
    ids, unplaced = interpret("Only external front and back elevation", _vs(install))
    assert ids == ["front_day", "rear_day"] and unplaced == []


def test_night_day_and_unplaced_words(install):
    vs = _vs(install)
    ids, _ = interpret("front elevation day night, and back elevation", vs)
    assert set(ids) == {"front_day", "front_night", "rear_day"}
    assert interpret("night", vs)[0] == ["front_night"]
    assert interpret("Front day, Kitchen", vs)[0] == ["front_day", "kitchen"]
    assert interpret("garage roof", vs) == ([], ["garage roof"])
    assert interpret("", vs) == ([], [])


def test_external_and_internal_filter(install):
    vs = _vs(install)
    exterior = {v["id"] for v in vs["views"] if not v.get("interior")}
    assert set(interpret("external day", vs)[0]) <= exterior
    assert interpret("internal bedroom", vs)[0]


def test_describe_lists_every_view_with_a_plain_note(install):
    vs = _vs(install)
    out = describe(vs)
    assert [v["id"] for v in out] == [v["id"] for v in vs["views"]]
    assert all(v["label"] and v["note"] for v in out)


def test_views_line_is_replaced_not_duplicated():
    text = "@night=true\n@views=Only external front\nfree words\n"
    assert with_views_line(text, "@views=front_day,rear_day") == (
        "@night=true\nfree words\n@views=front_day,rear_day\n"
    )
    assert "@views" not in with_views_line(text, "")


def test_step_and_part_for_each_stage():
    assert plain.step_of("s3_evidence", "RUNNING") == {
        "step": 1,
        "step_name": "Read the drawings",
        "steps": 6,
        "part": 3,
        "parts": 4,
        "part_name": "Collecting the facts",
    }
    assert plain.step_of("gate_views", "AWAITING_VIEWS")["step_name"] == "Choose views"
    plan = plain.step_of("s5_checklist", "RUNNING")
    assert (plan["step"], plan["part"], plan["parts"]) == (3, 1, 2)
    assert plain.step_of("gate_approval", "AWAITING_APPROVAL")["step"] == 3
    s7 = plain.step_of("s7_render", "RUNNING")
    assert (s7["step"], s7["part"], s7["parts"]) == (4, 2, 3)
    assert plain.step_of("s9_review", "AWAITING_REVIEW")["step"] == 5
    assert plain.step_of(None, "SUCCEEDED")["step"] == 6


def _finished_job(settings, name: str, out: Path):
    pdf = make_pdf(settings.work_root / "drop" / f"{name}.pdf", ["ELEVATIONS"])
    job = new_job(settings.path("jobs"), [pdf])
    job.state["designer"] = {"input_name": name, "output": str(out)}
    job.state["status"] = "SUCCEEDED"
    job.save()
    final = out / "Dwelling 1" / "FINAL APPROVED"
    final.mkdir(parents=True)
    (final / "front_day_D1.png").write_bytes(b"png")
    return job


def _settings(install, tmp_path):
    import yaml

    cfg = yaml.safe_load((install / "config.yaml").read_text(encoding="utf-8"))
    cfg["designer"] = {"desktop": str(tmp_path / "Desktop"), "folder": "RenderAgent"}
    (install / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return load_config(install)


def test_finished_job_moves_to_completed_and_records_it(install, tmp_path):
    s = _settings(install, tmp_path)
    f = designer.folders(s)
    job = _finished_job(s, "Lot plan", f.output / "Lot plan")
    assert designer.complete(s, job) is None
    dest = f.output / "Completed" / "Lot plan"
    assert (dest / "Dwelling 1" / "FINAL APPROVED" / "front_day_D1.png").is_file()
    assert not (f.output / "Lot plan").exists()
    assert json.loads(job.state_file.read_text(encoding="utf-8"))["designer"]["output"] == str(dest)
    assert designer.complete(s, job) is None  # already there: nothing to do


def test_name_clash_in_completed_gets_a_number(install, tmp_path):
    s = _settings(install, tmp_path)
    f = designer.folders(s)
    (f.output / "Completed" / "Lot plan").mkdir(parents=True)
    job = _finished_job(s, "Lot plan", f.output / "Lot plan")
    designer.complete(s, job)
    assert (f.output / "Completed" / "Lot plan (2)").is_dir()


def test_failed_move_leaves_everything_and_says_so(install, tmp_path, monkeypatch):
    s = _settings(install, tmp_path)
    f = designer.folders(s)
    job = _finished_job(s, "Lot plan", f.output / "Lot plan")

    def refuse(src, dest):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(designer.shutil, "move", refuse)
    problem = designer.complete(s, job)
    assert problem and "could not be moved" in problem
    assert (f.output / "Lot plan").is_dir() and job.state["designer"]["output"] == str(f.output / "Lot plan")


def test_migration_moves_finished_jobs_and_loose_final_folders_only(install, tmp_path):
    s = _settings(install, tmp_path)
    f = designer.folders(s)
    _finished_job(s, "Done job", f.output / "Done job")
    running = _finished_job(s, "Running job", f.output / "Running job")
    running.state["status"] = "AWAITING_REVIEW"
    running.save()
    loose = f.output / "Old CLI set" / "Dwelling 1" / "FINAL APPROVED"
    loose.mkdir(parents=True)
    (loose / "a.png").write_bytes(b"png")
    (f.output / "Drafts only" / "Dwelling 1" / "Draft").mkdir(parents=True)

    moved = designer.migrate_completed(s)
    done = f.output / "Completed"
    assert (done / "Done job").is_dir() and (done / "Old CLI set").is_dir()
    assert (f.output / "Running job").is_dir() and (f.output / "Drafts only").is_dir()
    assert len(moved) == 2
    assert designer.migrate_completed(s) == []  # idempotent
