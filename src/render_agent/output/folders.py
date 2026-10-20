"""Output folders: Common + "Dwelling N", each with Draft, Draft/draft-final and FINAL APPROVED.

Layout (folder names from knowledge/view_set.yaml `output_folders`; decision 20 Oct 2026):
- <home>/Draft/<render>_vN.png: every version, with the DRAFT overlay, kept for the record.
- <home>/Draft/draft-final/<name>.png: the approved version, still with the DRAFT overlay.
- <dwelling>/FINAL APPROVED/<name>.png: the approved version without the overlay, scaled to
  images.delivery_size. A render standing for several dwellings has its master in
  Common/FINAL APPROVED and a copy in each dwelling's FINAL APPROVED, so every dwelling is a complete
  set. A single-house job has no Common folder.
The clean (overlay-free) copy lives only in the job's output/clean/ and only for the latest version of
each render; it is deleted once that version is delivered. Exporting again (after a regeneration)
replaces the master and every copy.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from PIL import Image

from render_agent.util.fs import atomic_write_bytes, safe_name, sha256_file
from render_agent.validate.checklist_validator import Options, ViewRules, dwelling_code


def dwelling_folder(dwelling: str, dwellings: list[str], pattern: str) -> str:
    n = dwelling_code(dwelling, dwellings)[1:]
    return safe_name(pattern.format(n=n), 60)


def names(view_set: dict[str, Any]) -> dict[str, Any]:
    cfg = view_set.get("output_folders", {})
    return {
        "shared": cfg.get("shared_folder", "Common"),
        "pattern": cfg.get("dwelling_folder_pattern", "Dwelling {n}"),
        "draft": cfg.get("draft_folder", "Draft"),
        "draft_final": cfg.get("draft_final_folder", "draft-final"),
        "final": cfg.get("final_folder", "FINAL APPROVED"),
        "single_common": cfg.get("single_house_has_common_folder", False),
    }


def covers_of(render: dict[str, Any], dwellings: list[str], rules: ViewRules) -> list[str]:
    return [d for d in dwellings if d in rules.covers(render)] or [render["dwelling_id"]]  # D1-D2-D3 order


def home_folder(render: dict[str, Any], dwellings: list[str], view_set: dict[str, Any]) -> str:
    """Common for a render that stands for several dwellings (when there is a Common), else its
    dwelling's folder. Its drafts live in <home>/Draft."""
    n = names(view_set)
    rules = ViewRules(view_set, Options(dwellings=dwellings, limit=0))
    covers = covers_of(render, dwellings, rules) if dwellings else [render["dwelling_id"]]
    use_common = len(dwellings) > 1 or n["single_common"]
    if len(covers) > 1 and use_common:
        return str(n["shared"])
    return dwelling_folder(covers[0], dwellings or covers, n["pattern"])


def file_name(render: dict[str, Any], covers: list[str], dwellings: list[str], rules: ViewRules) -> str:
    """e.g. rear_day_D2-D3.png, kitchen_D1-D2-D3.png, front_day_D1.png."""
    view = rules.view_id(render) or re.sub(r"[^a-z0-9]+", "_", render["view_type"].lower()).strip("_")
    codes = "-".join(dwelling_code(d, dwellings) for d in covers)
    return safe_name(f"{view}_{codes}.png", 100)


def deliver_png(data: bytes, size: str) -> bytes:
    """Delivery copy at exactly `size` (e.g. "1920x1080"): centre-cropped to that aspect, Lanczos
    resampled, RGB (sRGB) lossless PNG. A source of the other orientation (portrait for a landscape
    size) is kept as generated (phase 1). An empty size keeps the source."""
    if not size or "x" not in size:
        return data
    tw, th = (int(x) for x in size.split("x"))
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        if (w >= h) != (tw >= th):
            return data
        if (w, h) == (tw, th) and im.mode == "RGB":
            return data
        if w * th > h * tw:  # too wide: trim the sides
            cw = round(h * tw / th)
            box = ((w - cw) // 2, 0, (w - cw) // 2 + cw, h)
        else:  # too tall: trim top and bottom
            ch = round(w * th / tw)
            box = (0, (h - ch) // 2, w, (h - ch) // 2 + ch)
        out = im.convert("RGB").resize((tw, th), Image.Resampling.LANCZOS, box=box)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def export_approved(
    job_dir: Path,
    renders: list[dict[str, Any]],
    approved: dict[str, int],
    dwellings: list[str],
    view_set: dict[str, Any],
    delivery_size: str = "",
    sources: dict[str, str] | None = None,
    drafts: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Delivers each approved render; returns manifest entries.

    renders: checklist RENDER_REQUIREMENTS (render_id, dwelling_id, view_type, location, ...)
    approved: render_id -> approved version number
    delivery_size: FINAL APPROVED copies are scaled to exactly this size ("" keeps the source size)
    sources: render_id -> job-relative overlay-free file (default output/clean/<id>_vN.png)
    drafts: render_id -> job-relative DRAFT-overlay file of the approved version (for draft-final)
    A clean copy already deleted after an earlier delivery is fine: the delivered file is reused.
    """
    n = names(view_set)
    use_common = len(dwellings) > 1 or n["single_common"]
    rules = ViewRules(view_set, Options(dwellings=dwellings, limit=0))
    out_root = job_dir / "output"
    used: set[str] = set()
    entries = []
    for r in renders:
        rid = r["render_id"]
        if rid not in approved:
            continue
        covers = covers_of(r, dwellings, rules)
        name = file_name(r, covers, dwellings, rules)
        if name in used:  # two renders of the same view for the same dwellings
            name = name.replace(".png", f"_{safe_name(rid, 40)}.png")
        used.add(name)
        shared = len(covers) > 1 and use_common
        home = n["shared"] if shared else dwelling_folder(covers[0], dwellings or covers, n["pattern"])
        finals = [
            out_root / dwelling_folder(d, dwellings or covers, n["pattern"]) / n["final"] / name
            for d in covers
        ]
        master = out_root / n["shared"] / n["final"] / name if shared else None
        clean = (
            job_dir / sources[rid]
            if sources and rid in sources
            else out_root / "clean" / f"{safe_name(rid, 80)}_v{approved[rid]}.png"
        )
        if clean.is_file():
            data = deliver_png(clean.read_bytes(), delivery_size)
        elif (master or finals[0]).is_file():
            data = (master or finals[0]).read_bytes()  # delivered before; its clean copy was removed
        else:
            raise FileNotFoundError(f"approved render {rid} v{approved[rid]} has no clean copy: {clean}")
        if master:
            atomic_write_bytes(master, data)
        for dest in finals:
            atomic_write_bytes(dest, data)
        draft_final = None
        src_draft = job_dir / drafts[rid] if drafts and rid in drafts else None
        if src_draft and src_draft.is_file():
            draft_final = out_root / home / n["draft"] / n["draft_final"] / name
            atomic_write_bytes(draft_final, src_draft.read_bytes())
        entries.append(
            {
                "render_id": rid,
                "version": approved[rid],
                "covers": covers,
                "source": clean.relative_to(job_dir).as_posix(),
                "master": master.relative_to(job_dir).as_posix() if master else None,
                "copies": [c.relative_to(job_dir).as_posix() for c in finals],
                "draft_final": draft_final.relative_to(job_dir).as_posix() if draft_final else None,
                "sha256": sha256_file(master or finals[0]),
            }
        )
    return entries
