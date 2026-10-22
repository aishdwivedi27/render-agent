"""Every row of the error table in docs/SPEC.md section 9 has a test that injects that fault.

ROWS maps each row (area, failure) to the tests covering it. A row that only applies to a deferred
component says so. test_every_spec_row_is_covered fails when the spec gains a row without a test,
or when a named test disappears.
"""

from __future__ import annotations

import importlib
import json
import re

from render_agent.images import MockProvider
from render_agent.pipeline import gate
from render_agent.pipeline.job import load_job, new_job
from render_agent.pipeline.runner import RunContext, job_lock, run_job
from tests.conftest import ROOT, make_pdf
from tests.integration.test_m2_planning import _llm, _sheet, _start, _view

DEFERRED = "DEFERRED"
ROWS: dict[tuple[str, str], list[str]] = {
    ("Input", "Not a PDF, empty, won't open"): [
        "tests.unit.test_ingest",
        "tests.integration.test_batch_run::test_batch_folder_makes_one_job_per_pdf_and_subfolder",
    ],
    ("Input", "Password-protected"): ["tests.unit.test_ingest"],
    ("Rendering", "Paid image calls reach `images.max_images_per_round`"): [
        "tests.unit.test_images::test_round_cap_pauses_before_next_paid_call"
    ],
    ("Input", "Scan with no text layer"): ["tests.unit.test_ingest"],
    ("Input", "Item in Input still copying or locked"): [
        "tests.integration.test_m5_app::test_item_still_copying_cannot_start"
    ],
    ("Input", "Same PDF started twice"): [
        "tests.integration.test_m5_app::test_same_pdf_started_twice_opens_existing_then_start_anyway",
        "tests.integration.test_batch_run::test_batch_folder_makes_one_job_per_pdf_and_subfolder",
    ],
    ("Input", "Copy verification failed at Start"): [
        "tests.integration.test_m5_app::test_copy_verification_failure_keeps_originals"
    ],
    ("Input", "No elevation or no floor plan"): [
        "tests.integration.test_error_table::test_missing_drawing_type_makes_views_not_ready"
    ],
    ("Input", "Unknown/invalid directive"): [
        "tests.integration.test_m2_planning::test_night_and_ignored_directives_reach_the_gate"
    ],
    ("Extraction", "Invalid JSON / schema break"): ["tests.unit.test_llm"],
    ("Extraction", "Refusal or truncation"): ["tests.unit.test_llm"],
    ("Extraction", "Drawings contradict"): [
        "tests.integration.test_error_table::test_contradictions_reach_the_gate"
    ],
    ("Extraction", "Invalid/empty bbox"): [
        "tests.integration.test_m2_planning::test_gate_refuses_unresolved_then_approves_and_s6_builds_prompts"
    ],
    ("Extraction", "Room layouts from the sheets do not fit `schemas/house_spec.json` ROOMS"): [
        "tests.integration.test_m2_planning::test_room_layout_that_breaks_the_schema_stops_stage4"
    ],
    ("Prompts", "Interior render with no matching room, no openings or fixtures read, or no wall to face"): [
        "tests.unit.test_room_layout::test_room_without_layout_falls_back"
    ],
    ("Prompts", "Room bbox invalid"): ["tests.unit.test_room_layout::test_invalid_room_bbox_gives_no_crop"],
    ("Drawing check", "Interior answer without `opening_walls` and `fixture_positions`"): [
        "tests.unit.test_room_layout::test_interior_check_uses_wall_and_fixture_checks"
    ],
    ("Planning", "> 16 renders for a dwelling, or counts wrong"): [
        "tests.unit.test_checklist_validator::test_limit_trims_from_the_bottom_of_the_priority_list"
    ],
    ("Planning", "`@views` words match no view"): [
        "tests.integration.test_m5_app::test_views_words_that_match_nothing_are_listed_and_every_view_planned"
    ],
    ("Approval", "Re-plan with corrections fails (LLM error)"): [
        "tests.integration.test_m5_app::test_replan_failure_is_plain_keeps_corrections_and_retry_resumes"
    ],
    ("Output", r"Moving a finished job to Output\Completed fails (file in use)"): [
        "tests.unit.test_view_choice_and_completed::test_failed_move_leaves_everything_and_says_so"
    ],
    ("System", "Cancel cannot move the originals back to Input (file in use)"): [
        "tests.integration.test_m5_app::test_cancel_that_cannot_restore_the_drawing_keeps_the_job"
    ],
    ("Planning", "Required front view missing"): [
        "tests.unit.test_checklist_validator::test_missing_front_inserted_not_ready_with_blocker"
    ],
    ("Rendering", "Rate limit, timeout, 5xx, network"): [
        "tests.integration.test_m3_rendering::test_one_render_failing_does_not_stop_others"
    ],
    ("Rendering", "Content-policy / bad request"): [
        "tests.integration.test_m3_rendering::test_one_render_failing_does_not_stop_others"
    ],
    ("Rendering", "No image or unreadable image"): [
        "tests.unit.test_images::test_unreadable_image_retried_once"
    ],
    ("Rendering", "Fails drawing check"): [
        "tests.integration.test_m3_rendering::test_render_and_check_all",
        "tests.integration.test_m3_rendering::test_second_fail_and_model_overall_ignored",
    ],
    ("Rendering", "Drawing check errors or unsure"): [
        "tests.integration.test_m3_rendering::test_check_error_is_not_checked"
    ],
    ("Rendering", "Drawing check passes with confidence below `drawing_check.min_confidence`"): [
        "tests.integration.test_m3_drawing_check::test_low_confidence_pass_is_not_checked"
    ],
    ("Review", "Designer flags a render"): [
        "tests.integration.test_m4_review::test_flag_one_regenerates_only_that_render"
    ],
    ("Corrections", "Correction text tries to act as an instruction"): [
        "tests.integration.test_m5_app::test_correction_that_looks_like_an_instruction_is_data"
    ],
    ("Corrections", "Feedback store not writable"): [
        "tests.integration.test_m5_app::test_feedback_store_not_writable_warns_and_job_continues"
    ],
    ("Corrections", "Export feedback fails"): [
        "tests.integration.test_m5_app::test_export_feedback_failure_is_plain_and_leaves_no_partial_zip"
    ],
    ("Account", "Invalid or missing key at install or first run"): [
        "tests.integration.test_m5_app::test_missing_key_at_first_run_blocks_start",
        "tests.integration.test_m5_app::test_settings_never_returns_a_key_and_hides_providers_not_enabled",
        DEFERRED + ": M6 installer key page",
    ],
    ("Account", "Provider enabled without a key"): [
        "tests.integration.test_m5_app::test_provider_enabled_without_key_is_hidden_and_named"
    ],
    ("Account", "Key missing/invalid/expired"): [
        "tests.integration.test_m5_app::test_paused_job_has_its_own_status_and_retry_resumes",
        "tests.integration.test_m2_planning::test_llm_directive_switches_model_and_unknown_is_listed",
        "tests.unit.test_images::test_get_provider",
    ],
    ("Account", "Out of credit / quota / spending limit"): ["tests.integration.test_out_of_credit"],
    ("System", "Disk below 2 GB free"): [
        "tests.integration.test_error_table::test_low_disk_pauses_and_resumes",
        "tests.integration.test_m5_app::test_disk_low_blocks_new_jobs_with_a_message",
    ],
    ("System", "Crash, reboot, power loss"): [
        "tests.integration.test_m3_rendering::test_key_problem_pauses_then_resume_skips_finished",
        "tests.integration.test_error_table::test_lock_is_released_when_the_holder_goes_away",
        "tests.integration.test_m5_app::test_reopening_the_app_resumes_queued_and_running_jobs",
    ],
    ("System", "Second app instance"): [
        "tests.unit.test_app_launcher::test_second_app_instance_brings_the_window_forward_and_exits",
        "tests.integration.test_error_table::test_second_run_of_the_same_job_does_nothing",
    ],
    ("System", "Port in use"): ["tests.unit.test_app_launcher::test_port_in_use_gives_a_plain_message"],
    ("System", "WebView2 runtime missing"): [
        "tests.unit.test_app_launcher::test_webview2_missing_gives_a_plain_message",
        DEFERRED + ": M6 installer installs it",
    ],
    ("System", "Output or History folder not writable"): [
        "tests.integration.test_m5_app::test_output_not_writable_keeps_job_state",
        "tests.integration.test_m5_app::test_history_move_failure_still_starts_and_says_so",
    ],
    ("System", "Desktop redirected to OneDrive"): [
        "tests.integration.test_m5_app::test_onedrive_desktop_warns_once"
    ],
    ("System", "Network down"): [
        "tests.unit.test_llm",
        "tests.integration.test_m5_app::test_reopening_the_app_resumes_queued_and_running_jobs",
    ],
    ("People", "Approval/review unanswered 2 working days"): [
        "tests.integration.test_m5_app::test_status_words_are_plain_and_errors_have_no_trace",
        DEFERRED + ": notify.py (reminder toasts, phase 2)",
    ],
    ("Config", "Invalid config or unknown model ID"): ["tests.unit.test_config"],
}


def _spec_rows():
    text = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    table = text.split("## 9. Error handling")[1].split("\n## ")[0]
    rows = []
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Area", "---") and not set(cells[0]) <= {"-"}:
            rows.append((cells[0], cells[1]))
    return rows


def test_every_spec_row_is_covered():
    spec_rows = _spec_rows()
    assert len(spec_rows) >= 25
    missing = [r for r in spec_rows if not any(r[0] == k[0] and r[1].startswith(k[1][:20]) for k in ROWS)]
    assert not missing, f"error-table rows without a test entry: {missing}"
    for key, targets in ROWS.items():
        for t in targets:
            if t.startswith(DEFERRED):
                continue
            mod, _, fn = t.partition("::")
            module = importlib.import_module(mod)
            assert not fn or callable(getattr(module, fn, None)), f"{key}: test {t} not found"


# --- the rows tested here ----------------------------------------------------------------------
def test_missing_drawing_type_makes_views_not_ready(install_settings):
    """A set with elevations but no floor plan: interiors are NOT READY with a reason; job continues."""
    pdf = make_pdf(install_settings.root / "d1" / "a.pdf", ["ELEVATIONS", "SITE"])
    job = new_job(install_settings.path("jobs"), [pdf])
    llm = _llm()
    llm.canned["sheet_analysis.page-2"] = _sheet(
        [_view("S", "site_plan", "top_down", (0.1, 0.1, 0.9, 0.9), "SITE")]
    )
    res = run_job(RunContext(job, install_settings, extras={"llm": llm}))
    assert res.status == "AWAITING_APPROVAL"
    cl = json.loads((job.dir / "checklist.json").read_text())
    kitchen = next(r for r in cl["RENDER_REQUIREMENTS"] if r["view_type"] == "Kitchen")
    assert kitchen["readiness"] == "NOT READY" and any("No floor plan" in b for b in kitchen["blockers"])
    street = next(r for r in cl["RENDER_REQUIREMENTS"] if r["view_type"] == "Streetscape")
    assert street["readiness"] != "NOT READY"  # the elevation is there
    corrections = json.loads((job.dir / "checklist_validation.json").read_text())["corrections"]
    assert any(c["rule"] == "missing_drawing" for c in corrections)


def test_contradictions_reach_the_gate(install_settings):
    llm = _llm()
    llm.canned["spec"]["UNCERTAINTIES"] = [
        "Roof pitch: 22.5 degrees on Elevation 1 but 25 degrees on Section A; both kept."
    ]
    pdf = make_pdf(install_settings.root / "d2" / "a.pdf", ["ELEVATIONS", "FLOOR PLAN"])
    job = new_job(install_settings.path("jobs"), [pdf])
    run_job(RunContext(job, install_settings, extras={"llm": llm}))
    approval = json.loads((job.dir / "approval.json").read_text())
    assert any("22.5" in u and "25" in u for u in approval["drawing_uncertainties"])


def test_low_disk_pauses_and_resumes(install_settings, monkeypatch):
    from render_agent.pipeline import runner

    job, _, _ = _start(install_settings)
    assert gate.approve(job, {}, by="test") == []
    real = runner.shutil.disk_usage
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda p: real(p)._replace(free=int(0.5e9)))
    extras = {"llm": _llm(), "image_provider": MockProvider()}
    res = run_job(RunContext(load_job(job.dir), install_settings, extras=extras))
    assert res.status == "PAUSED" and "Free up disk space" in res.message
    assert json.loads((job.dir / "error.json").read_text())["code"] == "DISK_LOW"
    monkeypatch.setattr(runner.shutil, "disk_usage", real)
    from tests.integration.test_m3_rendering import CheckLLM

    res = run_job(
        RunContext(
            load_job(job.dir), install_settings, extras={"llm": CheckLLM(), "image_provider": MockProvider()}
        )
    )
    assert res.status == "AWAITING_REVIEW", res.message


def test_second_run_of_the_same_job_does_nothing(install_settings):
    job, _, _ = _start(install_settings)
    before = (job.dir / "state.json").read_bytes()
    with job_lock(job.dir):  # another process is busy with this job
        res = run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": _llm()}))
    assert "already being processed" in res.message
    assert (job.dir / "state.json").read_bytes() == before


def test_lock_is_released_when_the_holder_goes_away(install_settings):
    """After a crash the OS drops the lock, so resume works (simulated by closing the holder)."""
    job, _, _ = _start(install_settings)
    with job_lock(job.dir):
        pass
    assert not (job.dir / ".running.lock").exists()
    res = run_job(RunContext(load_job(job.dir), install_settings, extras={"llm": _llm()}))
    assert res.status == "AWAITING_APPROVAL" and "already" not in res.message
    assert re.search(r"approval", res.message, re.I)
