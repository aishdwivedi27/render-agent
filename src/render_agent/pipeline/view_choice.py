"""Choose views (gate between the house specification and planning; decision 21 Oct 2026).

After the drawings are read (stages 1-4) a job from the desktop app waits in AWAITING_VIEWS: the
designer picks all views, some, or says them in words, confirms what the agent will plan, and only
then does stage 5 plan the renders. The choice is written as `@views=<ids>` into the job's
instructions (ingest.json and input/start_options.txt), so planning, the validator and the record
of the job all read the same directive. Jobs run from the CLI (no designer folders) pass this gate
at once and keep using `@views` from their instruction file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from render_agent.pipeline.job import Job
from render_agent.util.directives import parse_directives
from render_agent.util.fs import atomic_write_json, atomic_write_text
from render_agent.validate.checklist_validator import qualifying_dwellings, requested_views
from render_agent.validate.view_words import interpret

CHOSEN = "views_chosen.json"
OPTIONS_FILE = "start_options.txt"


def needs_choice(job: Job) -> bool:
    """Only app jobs stop here; a CLI job keeps its instruction file's @views."""
    return bool((job.state.get("designer") or {}).get("output"))


def is_chosen(job_dir: Path) -> bool:
    return (job_dir / CHOSEN).is_file() and not _read(job_dir / CHOSEN).get("reopened")


def with_views_line(text: str, line: str) -> str:
    """`text` with its @views lines replaced by `line` ('' removes them)."""
    kept = [ln for ln in text.splitlines() if not ln.strip().lower().startswith("@views")]
    return "\n".join([*kept, *([line] if line else [])]) + "\n"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def instruction_file(job_dir: Path) -> dict[str, Any]:
    """What the designer's own instruction .txt files say about views, for the Choose views screen:
    the @views line (it pre-ticks the boxes) and the free text (which the designer can read into the
    words box and confirm). Start options written by the app are not an instruction file."""
    texts = [
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((job_dir / "input").glob("*.txt"))
        if p.name != OPTIONS_FILE
    ]
    text = "\n".join(texts)
    free = "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("@"))
    return {"present": bool(texts), "views": parse_directives(text).views, "text": free[:1000]}


def options(job_dir: Path, view_set: dict[str, Any]) -> list[dict[str, Any]]:
    """Every standard view with whether these drawings can support it, why not, and whether it starts
    ticked (from @views in the instructions, else every possible view that is not optional)."""
    spec = _read(job_dir / "house_specification.json")
    instructions = str(_read(job_dir / "ingest.json").get("instructions", ""))
    dwellings = [d["dwelling_id"] for d in spec.get("DWELLINGS", []) if d.get("dwelling_id")]
    dwellings = dwellings or ["DWELLING 1"]
    qualifying = qualifying_dwellings(spec, view_set) if spec else {}
    asked = parse_directives(instructions).views
    asked_ids = set(interpret(", ".join(asked), view_set)[0]) if asked else set()
    requested = requested_views(instructions, view_set)
    out = []
    for v in view_set["views"]:
        possible, why = True, ""
        if v.get("min_dwellings") and len(dwellings) < v["min_dwellings"]:
            possible = False
            why = f"needs {v['min_dwellings']} or more dwellings; these drawings show {len(dwellings)}"
        elif v.get("requires_documented") and v["id"] in qualifying and not qualifying[v["id"]]:
            words = ", ".join(v["requires_documented"]["words"][:3])
            possible, why = False, f"the drawings show no {words}"
        if v.get("required"):
            note = "always made"
        elif v.get("optional"):
            note = "made only when you choose it"
        elif v.get("interior"):
            note = "if the floor plan shows this room"
        else:
            note = ""
        default = not v.get("optional") or v["id"] in requested
        chosen = v["id"] in asked_ids or v["id"] in requested if asked_ids else default
        ticked = bool(v.get("required")) or (possible and chosen)
        out.append(
            {
                "id": v["id"],
                "label": v.get("label", v["id"]),
                "interior": bool(v.get("interior")),
                "required": bool(v.get("required")),
                "optional": bool(v.get("optional")),
                "possible": possible,
                "why_not": why,
                "note": note,
                "ticked": ticked,
            }
        )
    return out


def choose(job_dir: Path, ids: list[str], words: str, by: str) -> None:
    """Records the designer's views as @views (exact ids) and views_chosen.json."""
    line = f"@views={','.join(ids)}"
    info = _read(job_dir / "ingest.json")
    if info:
        info["instructions"] = with_views_line(str(info.get("instructions", "")), line)
        atomic_write_json(job_dir / "ingest.json", info)
    opts = job_dir / "input" / OPTIONS_FILE
    if opts.is_file():
        atomic_write_text(opts, with_views_line(opts.read_text(encoding="utf-8"), line))
    atomic_write_json(
        job_dir / CHOSEN,
        {"views": ids, "words": words, "by": by, "time": datetime.now(UTC).isoformat(timespec="seconds")},
    )


def reopen(job: Job) -> None:
    """Back to Choose views from the approval screen: the plan is made again after the new choice."""
    chosen = _read(job.dir / CHOSEN)
    if chosen:  # keep the last choice and words to show again
        atomic_write_json(job.dir / CHOSEN, {**chosen, "reopened": True})
    job.reset_stages(["gate_views", "s5_checklist", "gate_approval"])
