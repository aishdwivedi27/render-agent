"""Approval gate (always on): approval.json, the APPROVE file and the `approve` CLI command.

The job waits in AWAITING_APPROVAL until the designer approves. Approval is refused while a material
item a planned view shows is NOT FOUND in the PDF and has no choice. Changing a PDF value is a
logged override. Nothing is ever auto-approved. See docs/SPEC.md sections 6 and 8.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from render_agent.pipeline.job import Job
from render_agent.pipeline.selections import write_materials_report
from render_agent.pipeline.stages._common import read_json
from render_agent.util.fs import atomic_write_json

APPROVAL = "approval.json"
APPROVE_FILE = "APPROVE"
log = logging.getLogger("render_agent.gate")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load(job: Job) -> dict[str, Any]:
    path = job.dir / APPROVAL
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def is_approved(job: Job) -> bool:
    return _load(job).get("status") == "approved"


def request_approval(job: Job, blocking: list[str] | None = None) -> dict[str, Any]:
    """Writes the pending approval.json the gate page / CLI shows."""
    checklist = read_json(job.dir / "checklist.json", "the approval gate")
    selections = read_json(job.dir / "selections.json", "the approval gate")
    validation = read_json(job.dir / "checklist_validation.json", "the approval gate")
    previous = _load(job)
    data = {
        "status": "pending",
        "requested": previous.get("requested") or _now(),
        "plan": {
            "totals": checklist["PROJECT_TOTALS"],
            "dwellings": checklist["DWELLING_SUMMARY"],
            "renders": [
                {
                    k: r[k]
                    for k in ("render_id", "dwelling_id", "view_type", "location", "readiness", "blockers")
                }
                for r in checklist["RENDER_REQUIREMENTS"]
            ],
            "critical_blockers": checklist["CRITICAL_ARCHITECTURAL_BLOCKERS"],
            "validator_corrections": validation.get("corrections", []),
        },
        "needs_choice": [
            {"key": i["key"], "label": i["label"], "options": i["options"]}
            for i in selections["items"]
            if i["needs_choice"]
        ],
        "standard_defaults": [
            {"key": i["key"], "label": i["label"], "value": i["chosen"], "options": i["options"]}
            for i in selections["items"]
            if i.get("chosen_by") == "standard default (not in the drawings)"
        ],
        "ignored_directives": selections.get("ignored_directives", []),
        "dwellings_available": validation.get("dwellings", []),
        "warnings": selections.get("warnings", []),
        "blocking": (blocking or []) + unresolved(selections),
        "how_to_approve": (
            "Run `python -m render_agent approve <job folder> [--select key=value ...]`, "
            "or create a file named APPROVE in the job folder (one key=value choice per line). "
            "For a multi-dwelling site add --dwellings all|1|1,3 (or a line dwellings=1,3) to render "
            "only some dwellings; the default is all."
        ),
    }
    atomic_write_json(job.dir / APPROVAL, data)
    return data


def parse_dwelling_choice(text: str, available: list[str]) -> tuple[list[str], list[str]]:
    """'all', '2', '1,3' or full ids ('DWELLING 2') -> (chosen dwellings, problems)."""
    if not text.strip() or text.strip().lower() == "all":
        return list(available), []
    chosen, problems = [], []
    for part in (x.strip() for x in text.split(",") if x.strip()):
        match = [d for d in available if d.lower() == part.lower()]
        if not match and part.isdigit():
            match = [d for d in available if re.search(rf"(?<!\d){part}\s*$", d)]
            if not match and 1 <= int(part) <= len(available):
                match = [available[int(part) - 1]]
        if match:
            chosen += [d for d in match if d not in chosen]
        else:
            problems.append(f"unknown dwelling '{part}' (choose from: {', '.join(available)}, or all)")
    return [d for d in available if d in chosen], problems


def unresolved(selections: dict[str, Any]) -> list[str]:
    return [
        f"choose '{i['label']}' ({i['key']}): not in the PDF and a planned view shows it"
        for i in selections["items"]
        if i["needs_choice"]
    ]


def approve(job: Job, choices: dict[str, str], by: str, dwellings: str = "all") -> list[str]:
    """Records choices and approves. Returns the reasons approval was refused (empty = approved)."""
    sel_path = job.dir / "selections.json"
    selections = read_json(sel_path, "the approval gate")
    available = read_json(job.dir / "checklist_validation.json", "the approval gate").get("dwellings", [])
    chosen_dwellings, problems = parse_dwelling_choice(dwellings, available)
    items = {i["key"]: i for i in selections["items"]}
    problems += [f"unknown material item '{k}'" for k in choices if k not in items]
    for key, value in choices.items():
        item = items.get(key)
        if item is None or not value.strip():
            continue
        item.update(chosen=value.strip(), chosen_by=by, needs_choice=False)
        item["is_override"] = item["pdf_value"] != "NOT FOUND"
        if item["is_override"]:
            log.warning("job %s: designer override %s: %r -> %r", job.id, key, item["pdf_value"], value)
    atomic_write_json(sel_path, selections)
    write_materials_report(job.dir, selections)
    if problems or unresolved(selections):
        return request_approval(job, blocking=problems)["blocking"]  # type: ignore[no-any-return]
    data = _load(job)
    data.update(
        status="approved",
        approved=_now(),
        approved_by=by,
        choices={k: i["chosen"] for k, i in items.items() if i.get("chosen")},
        dwellings=chosen_dwellings,
        overrides=[
            {"key": k, "pdf_value": i["pdf_value"], "chosen": i["chosen"], "by": i["chosen_by"]}
            for k, i in items.items()
            if i.get("is_override")
        ],
        blocking=[],
    )
    atomic_write_json(job.dir / APPROVAL, data)
    log.info("job %s: plan approved by %s", job.id, by)
    return []


def parse_choices(lines: list[str]) -> dict[str, str]:
    out = {}
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def check_gate(job: Job) -> bool:
    """True once approved (approval.json or an APPROVE file whose choices resolve every item)."""
    if is_approved(job):
        return True
    approve_file = job.dir / APPROVE_FILE
    if approve_file.is_file():
        choices = parse_choices(approve_file.read_text(encoding="utf-8-sig").splitlines())
        dwellings = choices.pop("dwellings", "all")
        return not approve(job, choices, by="APPROVE file", dwellings=dwellings)
    request_approval(job)
    return False
