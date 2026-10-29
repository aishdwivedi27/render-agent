"""Check the plan (gate_plan_check; PLAN_VISUALS_SPEC sections 3 and 4).

PV2 shows the trace read-only: per dwelling the overlay, how the walls were traced and by which model,
the area table and the rows code wrote. Continue records plan/trace_seen.json; the floor plan
questions (PV3) come next, so the job then waits on their placeholder.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from render_agent.pipeline.job import Job
from render_agent.plan.rows import ZONE_LABEL
from render_agent.util.fs import atomic_write_json

SEEN = "plan/trace_seen.json"
SUMMARY = "plan/trace_summary.json"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def seen(job_dir: Path) -> bool:
    return (job_dir / SEEN).is_file()


def mark_seen(job_dir: Path, by: str) -> None:
    atomic_write_json(job_dir / SEEN, {"by": by, "time": datetime.now(UTC).isoformat(timespec="seconds")})


def reread(job: Job) -> None:
    """Read again with the model(s) that failed: the stage runs again and reuses the cached readings."""
    (job.dir / SEEN).unlink(missing_ok=True)
    job.reset_stages(["p1_trace", "gate_plan_check"])


def _method_label(summary: dict[str, Any], d: dict[str, Any], labels: list[str]) -> str:
    who = " + ".join(labels)
    if d["method"] == "raster_trace":
        return f"Walls traced by {who or 'the readout model'} (no vector walls on the page)"
    walls = "Walls traced from the vector drawing (code)"
    if summary.get("practice"):
        return walls + " · Practice run: no model reading, so rooms and fixtures are not shown"
    return f"{walls} · Rooms read by {who}" if who else walls


def view(job_dir: Path) -> dict[str, Any]:
    """What the Check the plan screen shows."""
    summary = _read(job_dir / SUMMARY)
    names = summary.get("model_labels", {})
    failed = {names.get(k, k): v for k, v in summary.get("failed", {}).items()}
    read_by = [names.get(k, k) for k in summary.get("models", []) if names.get(k, k) not in failed]
    out = []
    for d in summary.get("dwellings", []):
        folder = job_dir / d["folder"]
        geo, readout = _read(folder / "geometry.json"), _read(folder / "readout.json")
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
        out.append(
            {
                "id": d["id"],
                "overlay": f"{d['folder']}/overlay.png",
                "method": _method_label(summary, d, read_by),
                "mirror": (
                    f"Same as {mirror['of']}, {mirror['kind']} ({mirror['score']:.0%} of the walls match)"
                    if mirror
                    else ""
                ),
                "areas": table,
                "rows": [
                    {k: r[k] for k in ("item", "reading", "confidence", "question")}
                    for r in readout.get("rows", [])
                ],
                "summary": readout.get("summary", {}),
            }
        )
    return {
        "dwellings": out,
        "failed": failed,
        "notes": summary.get("notes", []),
        "practice": bool(summary.get("practice")),
    }
