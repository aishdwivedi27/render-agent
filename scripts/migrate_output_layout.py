"""Moves a job made before 20 Oct 2026 into the current output layout (docs/SPEC.md §4 and §8):

  output/<Common | Dwelling N>/Draft/<render>_vN.png          every version, DRAFT overlay
  output/<Common | Dwelling N>/Draft/draft-final/<name>.png   approved version, DRAFT overlay
  output/<Common | Dwelling N>/FINAL APPROVED/<name>.png      approved version, no overlay

Clean copies are kept only for the latest version of renders not yet delivered; output/final/ (the
retired generated finals) is removed. Records in render_index.json, review.json and manifest.json are
updated to the new paths. Run:  python scripts/migrate_output_layout.py jobs/<job-id>
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from render_agent.config import load_config
from render_agent.output.folders import home_folder, names
from render_agent.pipeline.stages.s7_render import prune_clean
from render_agent.util.fs import atomic_write_json
from render_agent.validate.checklist_validator import load_view_set


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _move(src: Path, dest: Path) -> None:
    if src.resolve() == dest.resolve() or not src.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)


def migrate(job_dir: Path) -> list[str]:
    settings = load_config()
    view_set = load_view_set(settings.path("knowledge"))
    n = names(view_set)
    out = job_dir / "output"
    plan = {r["render_id"]: r for r in _load(job_dir / "checklist.json").get("RENDER_REQUIREMENTS", [])}
    dwellings = _load(job_dir / "checklist_validation.json").get("dwellings", [])
    index = _load(out / "render_index.json")
    review = _load(job_dir / "review.json")
    manifest = _load(job_dir / "manifest.json")
    log = []
    # 1. every draft version into <home>/Draft
    for rid, rec in index.get("renders", {}).items():
        req = plan.get(rid) or {
            "render_id": rid,
            "dwelling_id": rec.get("dwelling_id", ""),
            "view_type": "",
            "location": "",
        }
        draft_dir = f"output/{home_folder(req, dwellings, view_set)}/{n['draft']}"
        for v in rec.get("versions", []):
            new = f"{draft_dir}/{Path(v['file']).name}"
            if v["file"] != new:
                _move(job_dir / v["file"], job_dir / new)
                log.append(f"{v['file']} -> {new}")
                v["file"] = new
        rec.pop("finals", None)
    # 2. delivered images into FINAL APPROVED; the approved version's draft into Draft/draft-final
    for e in manifest.get("renders", []):
        rid = e["render_id"]
        moved = []
        for c in [e.get("master"), *e.get("copies", [])]:
            if not c:
                continue
            p = Path(c)
            if p.parent.name == n["final"]:
                moved.append(c)
                continue
            new = (p.parent / n["final"] / p.name).as_posix()
            _move(job_dir / c, job_dir / new)
            log.append(f"{c} -> {new}")
            moved.append(new)
        if e.get("master"):
            e["master"], e["copies"] = moved[0], moved[1:]
        else:
            e["copies"] = moved
        ver = next((v for v in index["renders"][rid]["versions"] if v["version"] == e["version"]), None)
        if ver:
            home = Path(ver["file"]).parent.parent  # output/<home>
            name = Path(e["copies"][0]).name
            df = home / n["draft"] / n["draft_final"] / name
            df_path = job_dir / df
            df_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(job_dir / ver["file"], df_path)
            e["draft_final"] = df.as_posix()
            stale = job_dir / home / n["draft"] / name  # an earlier hand-made copy under Draft/
            if stale.is_file():
                stale.unlink()
            log.append(f"{ver['file']} -> {df.as_posix()} (draft-final)")
        e.pop("draft_copy", None)
        prune_clean(job_dir, rid)  # delivered: no clean copy needed
    # 3. latest-only clean copies for renders not delivered; retired generated finals removed
    delivered = {e["render_id"] for e in manifest.get("renders", [])}
    for rid, rec in index.get("renders", {}).items():
        if rid not in delivered and rec.get("versions"):
            prune_clean(job_dir, rid, keep=rec["versions"][-1]["clean_file"])
    if (out / "final").is_dir():
        shutil.rmtree(out / "final")
        log.append("output/final removed")
    for rid, entry in review.get("renders", {}).items():
        ver = next(
            (
                v
                for v in index.get("renders", {}).get(rid, {}).get("versions", [])
                if v["version"] == entry.get("version")
            ),
            None,
        )
        if ver:
            entry["file"] = ver["file"]
        entry.pop("final", None)
        entry.pop("final_problem", None)
    atomic_write_json(out / "render_index.json", index)
    if review:
        atomic_write_json(job_dir / "review.json", review)
    if manifest:
        atomic_write_json(job_dir / "manifest.json", manifest)
    return log


if __name__ == "__main__":
    for line in migrate(Path(sys.argv[1])):
        print(line)
