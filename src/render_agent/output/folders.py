"""Common + "Dwelling N" folders; shared renders copied into each dwelling; manifest of copies.

Only APPROVED renders are exported, and only their clean copy (output/clean/, no DRAFT overlay).
A render that stands for one dwelling goes to that dwelling's folder. A render that stands for
several goes to Common (the master) and is copied into each dwelling it covers, so every dwelling
folder is a complete set. A single-house job has no Common folder. Folder names come from
knowledge/view_set.yaml `output_folders`. Exporting again (after a regeneration) replaces the
master and every copy.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from render_agent.util.fs import atomic_write_bytes, safe_name, sha256_file
from render_agent.validate.checklist_validator import Options, ViewRules, dwelling_code


def dwelling_folder(dwelling: str, dwellings: list[str], pattern: str) -> str:
    n = dwelling_code(dwelling, dwellings)[1:]
    return safe_name(pattern.format(n=n), 60)


def file_name(render: dict[str, Any], covers: list[str], dwellings: list[str], rules: ViewRules) -> str:
    """e.g. rear_day_D2-D3.png, kitchen_D1-D2-D3.png, front_day_D1.png."""
    view = rules.view_id(render) or re.sub(r"[^a-z0-9]+", "_", render["view_type"].lower()).strip("_")
    codes = "-".join(dwelling_code(d, dwellings) for d in covers)
    return safe_name(f"{view}_{codes}.png", 100)


def export_approved(
    job_dir: Path,
    renders: list[dict[str, Any]],
    approved: dict[str, int],
    dwellings: list[str],
    view_set: dict[str, Any],
) -> list[dict[str, Any]]:
    """Copies each approved render's clean version out; returns manifest entries.

    renders: checklist RENDER_REQUIREMENTS (render_id, dwelling_id, view_type, location, ...)
    approved: render_id -> approved version number
    """
    cfg = view_set.get("output_folders", {})
    shared_name = cfg.get("shared_folder", "Common")
    pattern = cfg.get("dwelling_folder_pattern", "Dwelling {n}")
    single = len(dwellings) <= 1
    use_common = not single or cfg.get("single_house_has_common_folder", False)
    rules = ViewRules(view_set, Options(dwellings=dwellings, limit=0))
    out_root = job_dir / "output"
    used: set[str] = set()
    entries = []
    for r in renders:
        rid = r["render_id"]
        if rid not in approved:
            continue
        clean = out_root / "clean" / f"{safe_name(rid, 80)}_v{approved[rid]}.png"
        if not clean.is_file():
            raise FileNotFoundError(f"approved render {rid} v{approved[rid]} has no clean copy: {clean}")
        data = clean.read_bytes()
        covers = [d for d in rules.covers(r) if d in dwellings] or [r["dwelling_id"]]
        name = file_name(r, covers, dwellings, rules)
        if name in used:  # two renders of the same view for the same dwellings
            name = name.replace(".png", f"_{safe_name(rid, 40)}.png")
        used.add(name)
        master = None
        if len(covers) > 1 and use_common:
            master = out_root / shared_name / name
            atomic_write_bytes(master, data)
        copies = []
        for d in covers:
            dest = out_root / dwelling_folder(d, dwellings, pattern) / name
            atomic_write_bytes(dest, data)
            copies.append(dest)
        entries.append(
            {
                "render_id": rid,
                "version": approved[rid],
                "covers": covers,
                "source": clean.relative_to(job_dir).as_posix(),
                "master": master.relative_to(job_dir).as_posix() if master else None,
                "copies": [c.relative_to(job_dir).as_posix() for c in copies],
                "sha256": sha256_file(clean),
            }
        )
    return entries
