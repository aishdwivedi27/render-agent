"""Check the plan (gate_plan_check; PLAN_VISUALS_SPEC sections 3 and 4).

The screen shows per dwelling the overlay, how the walls were traced and by which model, the area table
and the readout table (Green / Orange / Red). Questions come in batches of up to `readout.batch_size`,
Red first. Answers are saved in plan/answers.json, added to the job's corrections and the project's
memory, and the plan is read again (p1_trace runs again on the cached readings; only free-text answers
are sent to the model). Nothing moves on while any row is Orange or Red; all Green writes
plan/readout_approved.json and the job goes on to the elevation choice.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from render_agent import memory
from render_agent.config import Settings
from render_agent.pipeline import corrections
from render_agent.pipeline.job import Job
from render_agent.plan import readout
from render_agent.plan.rows import ZONE_LABEL
from render_agent.plan.trace_rules import load_trace_rules
from render_agent.util.fs import atomic_write_json, read_text

SUMMARY = "plan/trace_summary.json"
ANSWERS = "plan/answers.json"
APPROVED = "plan/readout_approved.json"
CONTINUED = "plan/readout_continue.json"  # the designer's Continue on the final tables
SCALE = re.compile(r"^\s*1\s*:\s*(\d{1,5})\s*$")


class AnswerError(ValueError):
    """An answer the screen should not have sent (the row is Green, unknown, or the text is empty)."""


def _read(path: Path) -> Any:
    try:
        return json.loads(read_text(path)) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def load_answers(job_dir: Path) -> list[dict[str, Any]]:
    return list(_read(job_dir / ANSWERS).get("answers", []))


def scale_answer(answers: list[dict[str, Any]], page: int) -> int | None:
    """The scale the designer gave for a page (the last answer), else None."""
    ratio = None
    for a in answers:
        if a["row_id"] == "scale" and a.get("page") == page and (m := SCALE.match(a.get("text") or "")):
            ratio = int(m.group(1))
    return ratio


def reread(job: Job) -> None:
    """Read again with the model(s) that failed: the stage runs again and reuses the cached readings."""
    job.reset_stages(["p1_trace", "gate_plan_check"])


def _dwellings(job_dir: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(trace summary entry, readout.json) per dwelling."""
    summary = _read(job_dir / SUMMARY)
    return [(d, _read(job_dir / d["folder"] / "readout.json")) for d in summary.get("dwellings", [])]


def all_green(job_dir: Path) -> bool:
    ds = _dwellings(job_dir)
    return bool(ds) and all(r["confidence"] == "GREEN" for _, ro in ds for r in ro.get("rows", []))


def open_rows(job_dir: Path) -> int:
    return sum(r["confidence"] != "GREEN" for _, ro in _dwellings(job_dir) for r in ro.get("rows", []))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _readout_shas(job_dir: Path) -> dict[str, str]:
    return {d["id"]: _sha(job_dir / d["folder"] / "readout.json") for d, _ in _dwellings(job_dir)}


def confirmed(job_dir: Path) -> bool:
    """The designer pressed Continue on the final all-Green tables, and nothing changed since."""
    marker = job_dir / CONTINUED
    if not marker.is_file():
        return False
    return bool(_read(marker).get("readouts") == _readout_shas(job_dir))


def press_continue(job: Job, by: str) -> None:
    """plan/readout_continue.json: the designer's Continue on the final tables (always asked, section 4)."""
    atomic_write_json(
        job.dir / CONTINUED,
        {
            "readouts": _readout_shas(job.dir),
            "by": by,
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )


def approve(job: Job, by: str) -> None:
    """plan/readout_approved.json: the floor plan readout is all Green (section 4)."""
    summary = _read(job.dir / SUMMARY)
    atomic_write_json(
        job.dir / APPROVED,
        {
            "dwellings": [
                {
                    "id": d["id"],
                    "geometry_sha256": _sha(job.dir / d["folder"] / "geometry.json"),
                    "readout_sha256": _sha(job.dir / d["folder"] / "readout.json"),
                    "rows": len(ro.get("rows", [])),
                }
                for d, ro in _dwellings(job.dir)
            ],
            "models": [summary.get("model_labels", {}).get(m, m) for m in summary.get("models", [])],
            "answers": len(load_answers(job.dir)),
            "by": by,
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )


def _method_label(summary: dict[str, Any], d: dict[str, Any], labels: list[str]) -> str:
    who = " + ".join(labels)
    if d["method"] == "raster_trace":
        return f"Walls traced by {who or 'the readout model'} (no vector walls on the page)"
    walls = "Walls traced from the vector drawing (code)"
    if summary.get("practice") and not summary.get("practice_sample"):
        return walls + " · Practice run: no model reading, so rooms and fixtures are not shown"
    if summary.get("practice_sample"):
        return f"{walls} · Practice run: sample reading for {who}, no model was asked"
    return f"{walls} · Rooms read by {who}" if who else walls


def _fraction(target: list[float] | None, frame: list[float] | None) -> list[float] | None:
    """A row's box as fractions of the overlay image (0 to 1), for the highlight."""
    if not target or not frame:
        return None
    w, h = frame[2] - frame[0], frame[3] - frame[1]
    if w <= 0 or h <= 0:
        return None

    def clamp(v: float) -> float:
        return round(min(1.0, max(0.0, v)), 4)

    return [
        clamp((target[0] - frame[0]) / w),
        clamp((target[1] - frame[1]) / h),
        clamp((target[2] - frame[0]) / w),
        clamp((target[3] - frame[1]) / h),
    ]


def view(job_dir: Path, settings: Settings | None = None) -> dict[str, Any]:
    """What the Check the plan screen shows."""
    summary = _read(job_dir / SUMMARY)
    names = summary.get("model_labels", {})
    failed = {names.get(k, k): v for k, v in summary.get("failed", {}).items()}
    read_by = [names.get(k, k) for k in summary.get("models", []) if names.get(k, k) not in failed]
    trace_rules = load_trace_rules(settings.path("knowledge")) if settings else None
    size = trace_rules.readout.batch_size if trace_rules else 5
    other_title = trace_rules.readout.other_title if trace_rules else "Other checks"
    out = []
    every: list[tuple[str, dict[str, Any]]] = []
    for d, ro in _dwellings(job_dir):
        geo = _read(job_dir / d["folder"] / "geometry.json")
        table = []
        for r in geo.get("area_check", {}).get("results", []):
            drawn, traced = r["schedule_m2"], r["traced_m2"] if r["read"] else None
            diff = (traced - drawn) / drawn if traced is not None and drawn else None
            table.append(
                {
                    "area": ZONE_LABEL.get(r["area"], r["area"].title()),
                    "drawing": drawn,
                    "traced": traced,
                    "difference": None if diff is None else round(diff, 4),
                    "over": diff is not None and not r["within"],
                }
            )
        mirror = d.get("mirror")
        frame = ro.get("frame_mm")
        overlay = job_dir / d["folder"] / "overlay.png"
        rows = []
        for n, r in enumerate(ro.get("rows", []), start=1):
            rows.append(
                {
                    "n": n,
                    **{
                        k: r.get(k)
                        for k in (
                            "id",
                            "item",
                            "category",
                            "table",
                            "reading",
                            "alternative",
                            "evidence",
                            "confidence",
                            "question",
                            "answer",
                            "changed",
                            "options",
                            "readings",
                        )
                    },
                    "target": _fraction(r.get("target_mm"), frame),
                }
            )
            every.append((d["id"], r))
        out.append(
            {
                "id": d["id"],
                "overlay": f"{d['folder']}/overlay.png",
                "overlay_version": overlay.stat().st_mtime_ns if overlay.is_file() else 0,
                "method": _method_label(summary, d, read_by),
                "mirror": (
                    f"Same as {mirror['of']}, {mirror['kind']} ({mirror['score']:.0%} of the walls match)"
                    if mirror
                    else ""
                ),
                "areas": table,
                "rows": rows,
                "models": ro.get("models", []),
                "summary": ro.get("summary", {}),
                "main_summary": ro.get("main_summary", {}),
                "other_summary": ro.get("other_summary", {}),
                "other_title": other_title,
            }
        )
    totals = {k: sum(d["summary"].get(k, 0) for d in out) for k in ("green", "orange", "red")}
    return {
        "dwellings": out,
        "models": (out[0]["models"] if out and out[0].get("models") else None)
        or [names.get(m, m) for m in summary.get("models", [])],
        "summary": totals,
        "summary_text": f"{totals['green']} Green, {totals['orange']} Orange, {totals['red']} Red",
        "batch": [{"dwelling": dw, "row_id": r["id"]} for dw, r in readout.batch(every, size)],
        "batch_size": size,
        "all_green": bool(out) and totals["orange"] == 0 and totals["red"] == 0,
        "failed": failed,
        "notes": summary.get("notes", []),
        "practice": bool(summary.get("practice")),
    }


def answer(
    job: Job, settings: Settings, given: list[dict[str, Any]], by: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Saves one batch of answers and sets the plan to be read again. Returns (the saved answers, plain
    warnings). Raises AnswerError for an answer the screen should not have sent."""
    size = load_trace_rules(settings.path("knowledge")).readout.batch_size
    if not given:
        raise AnswerError("No answers were sent.")
    if len(given) > size:
        raise AnswerError(f"Answer at most {size} questions at a time.")
    ds = {d["id"]: (d, ro) for d, ro in _dwellings(job.dir)}
    previous = load_answers(job.dir)
    batch_no = max((int(a.get("batch", 0)) for a in previous), default=0) + 1
    now = datetime.now(UTC).isoformat(timespec="seconds")
    saved: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for g in given:
        dwelling, rid = str(g.get("dwelling", "")), str(g.get("row_id", ""))
        option, text = str(g.get("option") or ""), " ".join(str(g.get("text") or "").split())
        if (dwelling, rid) in seen:
            raise AnswerError(f"The question '{rid}' was answered twice.")
        seen.add((dwelling, rid))
        if dwelling not in ds:
            raise AnswerError(f"There is no {dwelling or 'dwelling'} in this plan.")
        d, ro = ds[dwelling]
        row = next((r for r in ro.get("rows", []) if r["id"] == rid), None)
        if row is None:
            raise AnswerError(f"There is no question '{rid}' for {dwelling}.")
        if row["confidence"] == "GREEN":
            raise AnswerError(f"'{row['item']}' is already Green and has no question.")
        labels = {o["key"]: o["label"] for o in row.get("options", [])}
        if option == "text" or (not option and text):
            option = "text"
            if not text:
                raise AnswerError(f"Write an answer for '{row['item']}', or pick one of the choices.")
            if rid == "scale" and not SCALE.match(text):
                raise AnswerError("Write the scale as 1:N, for example 1:100.")
        elif option not in labels:
            raise AnswerError(f"Pick one of the choices for '{row['item']}', or write an answer.")
        shown = text if option == "text" else labels[option]
        said = (
            text
            if option == "text"
            else (str(row.get("alternative") or "") if option == "alternative" else "")
        )
        record = {
            "dwelling": dwelling,
            "page": d.get("page"),
            "row_id": rid,
            "ref": row.get("ref"),
            "option": option,
            "text": said,  # what the model reads again for a free-text answer or an alternative
            "answer": shown + (f" ({text})" if text and option != "text" else ""),
            "item": row["item"],
            "category": row["category"],
            "reading_before": row["reading"],
            "question": row.get("question"),
            "target_mm": row.get("target_mm"),
            "batch": batch_no,
            "by": by,
            "time": now,
        }
        saved.append(record)
    atomic_write_json(job.dir / ANSWERS, {"answers": previous + saved})
    warnings: list[str] = []
    for a in saved:
        fact = f"Floor plan, {a['dwelling']}: {a['item']}"
        corrections.add(
            job.dir,
            fact,
            a["answer"],
            note=f"read as: {a['reading_before']}",
            by="designer",
            where="plan_check",
        )
        w = memory.remember(
            settings,
            job.dir,
            job.id,
            a["dwelling"],
            "readout_answer",
            f"{a['item']}: {a['answer']}",
            "floor plan",
        )
        if w and w not in warnings:
            warnings.append(w)
    job.reset_stages(["p1_trace", "gate_plan_check"])
    return saved, warnings
