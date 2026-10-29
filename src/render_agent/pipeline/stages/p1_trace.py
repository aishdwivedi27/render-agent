"""p1_trace: floor plan trace, overlay and area check (PLAN_VISUALS_SPEC section 3).

Per floor plan page:
1. Walls: on a vector page, traced by code from the drawing (local, free); on a scan, or a page with no
   vector walls, the readout model traces them.
2. The readout model(s) chosen on Start the plan check read rooms, openings, recesses, fixtures and
   clearance zones (one call per model, cached in plan/trace_<model>_page-N.json, so Retry re-reads only
   a model that failed). With Both, the first model that answered is used for the geometry; the other
   reading is kept for the plan check (PV3). A model that fails while another answered is "not read".
3. Per dwelling: plan/<dwelling>/geometry.json, readout.json (rows written by code) and overlay.png
   (with the disclaimer). plan/trace_summary.json lists them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema
import pymupdf
import yaml

from render_agent.config import KEY_ENV_VARS
from render_agent.errors import AgentError, PauseJobError, PermanentError
from render_agent.llm.base import LLM, get_llm
from render_agent.llm.offline import OfflineLLM
from render_agent.pipeline import plan_start
from render_agent.pipeline.stages._common import ingest_info, load_schema, read_json
from render_agent.plan import areas, geometry, overlay, rows, vector_walls
from render_agent.plan.trace_rules import TraceRules, load_trace_rules
from render_agent.util.fs import atomic_write_json, safe_name
from render_agent.util.prompts import load_prompt, quote_block, render

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

log = logging.getLogger("render_agent.p1")
STAGE = "plan_trace"
ARTIFACT = "plan/trace_summary.json"
READOUT_SHEETS = ("floor_plan", "mixed")
MIN_VECTOR_WALLS = 8  # fewer wall pieces than this: the page is treated as having no vector walls
REGION_PAD_MM = 300.0  # walls on the edge of a single dwelling's box still belong to it
PROVIDER_LABEL = {"anthropic": "Claude", "openai": "OpenAI"}
NO_VECTOR_NOTE = "No vector walls were found on page {page}: the walls were traced by {model} from the image."


def _plan_pages(
    job_dir: Path, info: dict[str, Any], rules: TraceRules
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pages the sheet reading typed as a floor plan; if none, pages whose text names a floor plan."""
    sheets = []
    for page in info["pages"]:
        sheet_file = job_dir / "sheets" / f"sheet-{page['page']}.json"
        sheets.append(
            (page, json.loads(sheet_file.read_text(encoding="utf-8")) if sheet_file.is_file() else {})
        )
    typed = [(p, s) for p, s in sheets if s.get("sheet_type") in READOUT_SHEETS]
    if typed:
        return typed
    words = [w.upper() for w in rules.floor_plan_words]
    return [
        (p, s)
        for p, s in sheets
        if any(w in (job_dir / p["text"]).read_text(encoding="utf-8").upper() for w in words)
    ]


def model_label(ctx: RunContext, name: str) -> str:
    choice = ctx.settings.llm.choices.get(name)
    return PROVIDER_LABEL.get(choice.provider, name) if choice else name


def _check_keys(ctx: RunContext, names: list[str]) -> None:
    """A chosen readout model without a key pauses the job, naming the provider (section 12)."""
    if ctx.extras.get("llm") is not None or ctx.dry_run:
        return
    for name in names:
        choice = ctx.settings.llm.choices[name]
        if not ctx.settings.secret(KEY_ENV_VARS[choice.provider]):
            label = PROVIDER_LABEL.get(choice.provider, choice.provider)
            raise PauseJobError(
                code="KEY_MISSING",
                what_happened=f"Your {label} key is not saved, so {label} cannot read the floor plan.",
                likely_cause=f"{label} was chosen as a readout model on Start the plan check.",
                what_to_do=f"Add your {label} key in Settings, then press Retry.",
            )


def _llm(ctx: RunContext, name: str) -> LLM:
    injected = ctx.extras.get("llm")
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    if ctx.dry_run:
        return OfflineLLM()
    return get_llm(ctx.settings, STAGE, raw_dir=ctx.job.dir / "llm", job_choice=name)


def _prompt(ctx: RunContext, page: dict[str, Any], ratio: int | None, vector: bool, rules: TraceRules) -> str:
    knowledge = ctx.settings.path("knowledge")
    standards = "\n\n".join(
        (knowledge / f).read_text(encoding="utf-8")
        for f in ("plan_output_spec.md", "drawing_reading_rules.md")
        if (knowledge / f).is_file()
    )
    spec_file = ctx.job.dir / "house_specification.json"
    spec = json.loads(spec_file.read_text(encoding="utf-8")) if spec_file.is_file() else {}
    dwellings = [d.get("dwelling_id", "") for d in spec.get("DWELLINGS", []) if d.get("dwelling_id")]
    text = (ctx.job.dir / page["text"]).read_text(encoding="utf-8")
    recess = {k: v.model_dump() for k, v in rules.recesses.items()}
    return render(
        load_prompt(ctx.settings.path("prompts"), STAGE),
        {
            "PAGE_NUMBER": str(page["page"]),
            "SCALE": f"The drawing scale is 1:{ratio}."
            if ratio
            else "No scale was found; read it if it is drawn.",
            "METHOD": (
                "The walls on this page are traced by code from the drawing's vector lines. Return an empty "
                "`walls` list."
                if vector
                else "This page has no vector walls (it is a scan or an image). Trace every wall outline "
                "vertex by vertex in `walls`, following every bend, jog, step and return; trace walls, "
                "not rooms."
            ),
            "RULES": quote_block("DRAWING_RULES", standards),
            "RECESS_RULES": quote_block("RECESS_RULES", yaml.safe_dump(recess, sort_keys=False)),
            "DWELLINGS": quote_block("DWELLINGS", "\n".join(dwellings) or "NOT FOUND"),
            "PAGE_TEXT": quote_block("PAGE_TEXT", text)
            if not page["scanned"]
            else "(No text layer: a scan.)",
        },
    )


def _read(
    ctx: RunContext, names: list[str], page: dict[str, Any], prompt: str
) -> tuple[dict[str, dict[str, Any]], dict[str, AgentError]]:
    """Each model's reading of the page (cached), and the models that failed."""
    schema = load_schema(ctx, STAGE)
    readings: dict[str, dict[str, Any]] = {}
    failed: dict[str, AgentError] = {}
    for name in names:
        cache = ctx.job.dir / "plan" / f"trace_{safe_name(name)}_page-{page['page']}.json"
        if cache.is_file():
            readings[name] = json.loads(cache.read_text(encoding="utf-8"))
            continue
        try:
            reading = _llm(ctx, name).call_json(
                f"{STAGE}.{name}.page-{page['page']}", prompt, [ctx.job.dir / page["image"]], schema, STAGE
            )
        except PauseJobError:
            raise  # out of credit or a key problem: the person fixes it, then Retry
        except AgentError as err:
            log.warning("job %s: %s could not read page %s: %s", ctx.job.id, name, page["page"], err)
            failed[name] = err
            continue
        atomic_write_json(cache, reading)
        readings[name] = reading
    if not readings:
        raise next(iter(failed.values()))
    return readings, failed


def _rect(outline: list[geometry.Point]) -> geometry.Rect:
    return (
        min(p[0] for p in outline),
        min(p[1] for p in outline),
        max(p[0] for p in outline),
        max(p[1] for p in outline),
    )


def _exterior(walls: list[geometry.Rect], closures: list[geometry.Rect], px_mm: float) -> list[bool]:
    """A wall is exterior when the footprint's outside touches one of its sides."""
    if not walls:
        return []
    polys = [vector_walls.polygon(r) for r in walls]
    grid, img = areas.footprint([p for poly in polys for p in poly], polys, closures, px_mm)
    pix = img.load()
    assert pix is not None
    step = 2 * px_mm
    out = []
    for x0, y0, x1, y1 in walls:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        probes = [(x0 - step, cy), (x1 + step, cy), (cx, y0 - step), (cx, y1 + step)]
        hit = False
        for p in probes:
            gx, gy = grid.pt(p)
            if 0 <= int(gx) < grid.w and 0 <= int(gy) < grid.h and pix[int(gx), int(gy)] == areas.OUTSIDE:
                hit = True
        out.append(hit)
    return out


def _schedule_for(schedule: dict[str, dict[str, float]], dwelling: str, count: int) -> dict[str, float]:
    key = " ".join(dwelling.upper().split())
    if key in schedule:
        return schedule[key]
    if count == 1 and len(schedule) == 1:
        return next(iter(schedule.values()))
    return schedule.get("", {})


def run(ctx: RunContext) -> str:
    job = ctx.job
    rules = load_trace_rules(ctx.settings.path("knowledge"))
    chosen = plan_start.chosen(job.dir)
    names = [n for n in chosen.get("readout_models", []) if n in ctx.settings.llm.choices]
    if not names:
        names = [ctx.settings.plans.readout_llm_default]
    info = ingest_info(ctx)
    pages = _plan_pages(job.dir, info, rules)
    if not pages:
        raise PermanentError(
            code="NO_FLOOR_PLAN",
            what_happened="No floor plan sheet was found in these drawings, so the plan cannot be traced.",
            likely_cause="The drawing set has no floor plan page, or it was read as another kind of sheet.",
            what_to_do="Check the PDF has a floor plan. To make exterior renders only, cancel this job and "
            "start it again choosing 'Exterior renders only (no plans)'.",
        )
    _check_keys(ctx, names)
    schema = load_schema(ctx, "plan_geometry")
    summary: dict[str, Any] = {
        "models": names,
        "model_labels": {n: model_label(ctx, n) for n in names},
        "practice": bool(ctx.dry_run) and ctx.extras.get("llm") is None,  # no model reading at all
        "pages": [],
        "dwellings": [],
        "failed": {},
        "notes": [],
    }
    traced: list[tuple[str, list[geometry.Rect]]] = []
    for page, sheet in pages:
        n = page["page"]
        pdf = job.dir / "input" / page["source_file"]
        text = (job.dir / page["text"]).read_text(encoding="utf-8")
        ratio = vector_walls.scale_from_text(str(sheet.get("scale", "")), text)
        factor = vector_walls.mm_per_pt(ratio or 100)
        with pymupdf.open(pdf) as doc:
            src = doc[page["source_page"] - 1]
            page_mm = (src.rect.width * factor, src.rect.height * factor)
            words = src.get_text("words")
        vp = (
            None
            if page["scanned"]
            else vector_walls.extract(pdf, page["source_page"], ratio or 100, rules.walls)
        )
        vector = vp is not None and len(vp.walls) >= MIN_VECTOR_WALLS
        readings, failed = _read(ctx, names, page, _prompt(ctx, page, ratio, vector, rules))
        primary = next(nm for nm in names if nm in readings)
        reading = readings[primary]
        for nm, err in failed.items():
            summary["failed"][nm] = err.what_happened
        if vector and vp is not None:
            walls = vp.walls
            closures = vector_walls.closures(vp, rules.areas.footprint_max_gap_mm, rules.walls.min_wall_mm)
            method = "vector"
        else:
            walls = [
                _rect(geometry.outline_mm(w["outline"], page_mm))
                for w in reading.get("walls", [])
                if w["outline"]
            ]
            closures = []
            method = "raster_trace"
            summary["notes"].append(NO_VECTOR_NOTE.format(page=n, model=model_label(ctx, primary)))
        exterior = _exterior(walls, closures, rules.areas.raster_mm_per_px)
        schedule = areas.parse_schedule(words, rules)
        whole = geometry.bounds(walls) if walls else (0.0, 0.0, page_mm[0], page_mm[1])
        found = [d for d in reading.get("dwellings", []) if d.get("id")]
        if not found:
            spec_file = job.dir / "house_specification.json"
            spec = read_json(spec_file, STAGE) if spec_file.is_file() else {}
            ids = [d["dwelling_id"] for d in spec.get("DWELLINGS", []) if d.get("dwelling_id")]
            found = [{"id": ids[0] if ids else "DWELLING 1", "region": None, "mirror_of": None}]
        scale = {
            "ratio": f"1:{ratio}" if ratio else "NOT FOUND",
            "mm_per_pt": round(factor, 4) if ratio else None,
        }
        models_used = [model_label(ctx, nm) for nm in readings]
        page_rows = {"scale": rows.scale_row(ratio, "Sheet reading and page text")}
        summary["pages"].append({"page": n, "method": method, "scale": scale["ratio"], "primary": primary})
        for d in found:
            region = (
                geometry.region_mm(d["region"], page_mm)
                if d.get("region")
                else whole
                if len(found) == 1
                else None
            )
            if region is None:
                continue
            pad = REGION_PAD_MM if len(found) == 1 else 0.0  # attached dwellings meet at the party wall
            padded = (region[0] - pad, region[1] - pad, region[2] + pad, region[3] + pad)
            mine = [i for i, w in enumerate(walls) if len(found) == 1 or geometry.inside(w, padded)]
            dwalls = [walls[i] for i in mine]
            region = geometry.bounds(dwalls) if dwalls else region
            geo = geometry.build(
                d["id"],
                region,
                dwalls,
                [exterior[i] for i in mine],
                reading,
                page_mm,
                scale,
                method,
                models_used,
                rules,
            )
            measured = areas.measure(
                [vector_walls.polygon(r) for r in dwalls],
                [k for c in closures if (k := geometry.clip(c, padded)) is not None],
                [(r["name"], r["outline"]) for r in geo["rooms"]],
                rules,
            )
            results = areas.compare(
                _schedule_for(schedule, d["id"], len(found)), measured, rules.areas.tolerance
            )
            geo["area_check"] = {"tolerance": rules.areas.tolerance, "results": results}
            drows = [page_rows["scale"], *rows.area_rows(results, rules.areas.tolerance)]
            drows += rows.shower_rows(geo, rules) + rows.clearance_rows(geo, rules)
            mirror = None
            for other, owalls in traced:
                kind, score = geometry.match(dwalls, owalls)
                if score >= rules.mirror_min_match:
                    geo["mirror_of"] = other
                    mirror = {"of": other, "kind": kind, "score": score}
                    drows.append(rows.mirror_row(d["id"], other, kind, score))
                    break
            traced.append((d["id"], dwalls))
            jsonschema.validate(geo, schema)
            slug = safe_name(d["id"].lower().replace(" ", "-"), 40)
            folder = job.dir / "plan" / slug
            folder.mkdir(parents=True, exist_ok=True)
            atomic_write_json(folder / "geometry.json", geo)
            readout = {
                "dwelling": d["id"],
                "models": models_used,
                "rows": drows,
                "summary": rows.summary(drows),
            }
            atomic_write_json(folder / "readout.json", readout)
            mm_per_px = factor * 72 / info.get("dpi", 150)
            overlay.save(
                overlay.draw(job.dir / page["image"], mm_per_px, geo, region, rules), folder / "overlay.png"
            )
            summary["dwellings"].append(
                {
                    "id": d["id"],
                    "folder": f"plan/{slug}",
                    "page": n,
                    "method": method,
                    "mirror": mirror,
                    "rooms": len(geo["rooms"]),
                    "walls": len(geo["walls"]),
                }
            )
    atomic_write_json(job.dir / ARTIFACT, summary)
    log.info("job %s: plan traced (%d dwelling(s))", job.id, len(summary["dwellings"]))
    return ARTIFACT
