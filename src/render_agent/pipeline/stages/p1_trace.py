"""p1_trace: floor plan trace, overlay and area check (PLAN_VISUALS_SPEC section 3).

Per floor plan page:
1. Walls: on a vector page, traced by code from the drawing (local, free); on a scan, or a page with no
   vector walls, the readout model traces them.
2. The readout model(s) chosen on Start the plan check read rooms, openings, recesses, fixtures and
   clearance zones (one call per model, cached in plan/trace_<model>_page-N.json, so Retry re-reads only
   a model that failed). With Both, the first model that answered is used for the geometry; the other
   reading is merged with it row by row in the readout (plan/readout.py). A model that fails while
   another answered is "not read".
3. The designer's answers (plan/answers.json) are applied to the primary reading before the geometry is
   built; free-text answers are read again by the primary model once per batch (plan_reread, cached in
   plan/reread_<model>_page-N_batch-B.json). Answered rows are Green.
4. Per dwelling: plan/<dwelling>/geometry.json, readout.json (Green / Orange / Red rows) and overlay.png
   (with the disclaimer). plan/trace_summary.json lists them.

A Practice run answers from the sample readings of a known test drawing (llm/practice.py), else it has
no model reading.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema
import pymupdf
import yaml
from PIL import Image

from render_agent.config import KEY_ENV_VARS
from render_agent.errors import AgentError, PauseJobError, PermanentError
from render_agent.llm.base import LLM, get_llm
from render_agent.llm.offline import OfflineLLM
from render_agent.llm.practice import PracticeLLM, find_canned
from render_agent.pipeline import plan_check, plan_start
from render_agent.pipeline.stages._common import ingest_info, load_schema, read_json
from render_agent.plan import (
    areas,
    code_items,
    code_reading,
    geometry,
    overlay,
    readout,
    readout_table,
    rows,
    vector_walls,
)
from render_agent.plan.source import ImageSource, PdfSource, Source, Word, page_words
from render_agent.plan.trace_rules import TraceRules, load_trace_rules
from render_agent.util.fs import atomic_write_json, atomic_write_text, safe_name
from render_agent.util.prompts import load_prompt, quote_block, render

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

log = logging.getLogger("render_agent.p1")
STAGE = "plan_trace"
CODE = "code"  # the reader made by code (plan/code_items.py), shown first in the table
REREAD = "plan_reread"
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


def _practice(ctx: RunContext) -> PracticeLLM | None:
    """The sample readings for a Practice run of a known test drawing (no key, no cost), else None."""
    if not ctx.dry_run or ctx.extras.get("llm") is not None:
        return None
    canned = find_canned(ctx.settings, [i["sha256"] for i in ctx.job.state.get("inputs", [])])
    return PracticeLLM(canned) if canned else None


def _llm(ctx: RunContext, name: str, stage: str = STAGE) -> LLM:
    injected = ctx.extras.get("llm")
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    if ctx.dry_run:
        return _practice(ctx) or OfflineLLM()
    return get_llm(ctx.settings, stage, raw_dir=ctx.job.dir / "llm", job_choice=name)


def _code_block(code: dict[str, Any] | None, page_mm: geometry.Point) -> str:
    """The code's items as a quoted data block: ids, kinds and positions as page fractions."""
    if not code:
        return quote_block("CODE_ITEMS", "NOT FOUND (code could not read this page)")

    def at(x: float, y: float) -> str:
        return f"[{x / page_mm[0]:.3f}, {y / page_mm[1]:.3f}]"

    lines = []
    for r in code["rooms"]:
        xs, ys = [p[0] for p in r["outline"]], [p[1] for p in r["outline"]]
        lines.append(
            f"room {r['id']}: {r['name'] or 'unlabelled'}, centre {at(sum(xs) / len(xs), sum(ys) / len(ys))}"
        )
    for o in code["openings"]:
        f = o["frame"]
        alt = f" (or {o['alternative']})" if o["alternative"] else ""
        lines.append(
            f"opening {o['id']}: {o['kind']}{alt}, {o['width_mm']} mm, "
            f"at {at((f[0] + f[2]) / 2, (f[1] + f[3]) / 2)}" + (f", code {o['code']}" if o["code"] else "")
        )
    for x in code["recesses"]:
        b = x["rect"]
        lines.append(
            f"recess {x['id']}: {x['kind']} in {x['room_name'] or x['room']}, "
            f"at {at((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)}"
        )
    return quote_block("CODE_ITEMS", "\n".join(lines) or "NOT FOUND")


def _prompt(
    ctx: RunContext,
    page: dict[str, Any],
    ratio: int | None,
    vector: bool,
    rules: TraceRules,
    code_items: str = "",
) -> str:
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
            "CODE_ITEMS": code_items or quote_block("CODE_ITEMS", "NOT FOUND"),
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


def _reread(
    ctx: RunContext,
    name: str,
    page: dict[str, Any],
    ratio: int | None,
    batch: int,
    items: dict[str, list[dict[str, Any]]],
    asked: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """The items the designer answered in free text, read again by the primary model with the answers
    quoted as data (one call per batch, cached so Retry and later re-reads never pay twice)."""
    cache = ctx.job.dir / "plan" / f"reread_{safe_name(name)}_page-{page['page']}_batch-{batch}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    lines = [
        f"- {a['item']} (id {a['ref']['item_id']}): question: {a.get('question') or 'NOT FOUND'} "
        f"| designer: {a['text'] or a['answer']}"
        for a in asked
    ]
    rules_file = ctx.settings.path("knowledge") / "drawing_reading_rules.md"
    prompt = render(
        load_prompt(ctx.settings.path("prompts"), REREAD),
        {
            "PAGE_NUMBER": str(page["page"]),
            "SCALE": f"The drawing scale is 1:{ratio}." if ratio else "No scale was found.",
            "ITEMS": quote_block("ITEMS_TO_READ_AGAIN", json.dumps(items, indent=1)),
            "ANSWERS": quote_block("DESIGNER_ANSWERS", "\n".join(lines)),
            "RULES": quote_block(
                "DRAWING_RULES",
                rules_file.read_text(encoding="utf-8") if rules_file.is_file() else "NOT FOUND",
            ),
            "PAGE_TEXT": quote_block("PAGE_TEXT", (ctx.job.dir / page["text"]).read_text(encoding="utf-8"))
            if not page["scanned"]
            else "(No text layer: a scan.)",
        },
    )
    got = _llm(ctx, name, REREAD).call_json(
        f"{REREAD}.{name}.page-{page['page']}",
        prompt,
        [ctx.job.dir / page["image"]],
        load_schema(ctx, REREAD),
        REREAD,
    )
    out = {k: list(got.get(k, [])) for k in readout.COLLECTIONS}
    atomic_write_json(cache, out)
    return out


def _targets(drows: list[dict[str, Any]], geo: dict[str, Any], rules: TraceRules) -> None:
    """Boxes for the rows code writes, so a click highlights them on the overlay."""

    def box(outlines: list[list[list[float]]]) -> list[float] | None:
        pts = [p for o in outlines for p in o]
        if not pts:
            return None
        return [
            min(p[0] for p in pts),
            min(p[1] for p in pts),
            max(p[0] for p in pts),
            max(p[1] for p in pts),
        ]

    rooms_ = {r["id"]: r for r in geo["rooms"]}
    zones = {z["id"]: z for z in geo["clearance_zones"]}
    for r in drows:
        if r["target_mm"] is not None:
            continue
        rid = r["id"]
        if rid.startswith("shower_") and rid[7:] in rooms_:
            r["target_mm"] = box([rooms_[rid[7:]]["outline"]])
        elif rid.startswith("clearance_") and rid[10:] in zones:
            r["target_mm"] = box([zones[rid[10:]]["outline"]])
        elif rid.startswith("area_") and rid[5:] in ("garage", "alfresco", "porch"):
            r["target_mm"] = box([x["outline"] for x in geo["rooms"] if rules.zone_of(x["name"]) == rid[5:]])
        elif rid.startswith("area_") or rid == "mirror":
            r["target_mm"] = [round(v, 1) for v in geo["region"]]


def _code_reading(
    ctx: RunContext,
    rules: TraceRules,
    page: dict[str, Any],
    vp: vector_walls.VectorPage,
    pdf: Path,
    ratio: int,
    mm_per_px: float,
) -> dict[str, Any]:
    """What the code reads from the drawing (plan/code_page-N.json): openings, rooms, recesses and the
    overlay check. It is cached per page and rule set, so answering a batch never reads the page again."""
    key = hashlib.sha256(
        json.dumps(
            [ratio, page["page"], page["scanned"], pdf.stat().st_size, rules.model_dump(mode="json")]
        ).encode()
    ).hexdigest()
    cache = ctx.job.dir / "plan" / f"code_page-{page['page']}.json"
    if cache.is_file():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            return saved["reading"]  # type: ignore[no-any-return]
    source: Source
    if page["scanned"]:
        words: list[Word] = []
        source = ImageSource(Image.open(ctx.job.dir / page["image"]), mm_per_px)
    else:
        words = page_words(pdf, page["source_page"], ratio)
        source = PdfSource(pdf, page["source_page"], ratio, rules.walls)
    with source:  # type: ignore[attr-defined]
        reading = code_reading.read_page(vp, source, words, rules).as_dict()
    atomic_write_json(cache, {"key": key, "reading": reading})
    return reading


def _label_dwellings(
    reading: dict[str, Any], found: list[dict[str, Any]], whole: geometry.Rect, page_mm: geometry.Point
) -> None:
    """Page with several dwellings: each of code's items belongs to the dwelling whose box holds it."""
    boxes = []
    for d in found:
        box = geometry.region_mm(d["region"], page_mm) if d.get("region") else None
        if box:
            boxes.append((d["id"], box))
    for coll in ("rooms", "openings", "recesses"):
        for item in reading.get(coll, []):
            if "outline" in item:
                pts = geometry.outline_mm(item["outline"], page_mm)
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
            else:
                cx, cy = geometry.to_mm(item["at"], page_mm)
            item["dwelling"] = next((i for i, b in boxes if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]), "")


def _box(points: list[geometry.Point]) -> geometry.Rect:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


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
    readout_schema = load_schema(ctx, "plan_readout")
    code_schema = load_schema(ctx, "plan_code_reading")
    summary: dict[str, Any] = {
        "models": names,
        "model_labels": {n: model_label(ctx, n) for n in names},
        "practice": bool(ctx.dry_run) and ctx.extras.get("llm") is None,  # no model was asked
        "practice_sample": _practice(ctx) is not None,  # ...but a test drawing's sample reading is used
        "pages": [],
        "dwellings": [],
        "failed": {},
        "notes": [],
    }
    traced: list[tuple[str, list[geometry.Rect]]] = []
    answers = plan_check.load_answers(job.dir)
    for page, sheet in pages:
        n = page["page"]
        pdf = job.dir / "input" / page["source_file"]
        text = (job.dir / page["text"]).read_text(encoding="utf-8")
        ratio = plan_check.scale_answer(answers, n) or vector_walls.scale_from_text(
            str(sheet.get("scale", "")), text
        )
        factor = vector_walls.mm_per_pt(ratio or 100)
        with pymupdf.open(pdf) as doc:
            src = doc[page["source_page"] - 1]
            page_mm = (src.rect.width * factor, src.rect.height * factor)
            words = src.get_text("words")
        mm_per_px = factor * 72 / info.get("dpi", 150)
        if page["scanned"]:  # a scan: the same mask pipeline on the page image
            with Image.open(job.dir / page["image"]) as pic:
                vp = vector_walls.from_image(pic, mm_per_px, ratio or 100, rules.walls)
        else:
            vp = vector_walls.extract(pdf, page["source_page"], ratio or 100, rules.walls)
        vector = len(vp.walls) >= MIN_VECTOR_WALLS and not page["scanned"]
        usable = len(vp.walls) >= MIN_VECTOR_WALLS
        # code reads the page first (when it has walls to read) and the models are shown its items
        code_all = _code_reading(ctx, rules, page, vp, pdf, ratio or 100, mm_per_px) if usable else None
        readings, failed = _read(
            ctx,
            names,
            page,
            _prompt(ctx, page, ratio, vector, rules, _code_block(code_all, page_mm)),
        )
        primary = next(nm for nm in names if nm in readings)
        page_answers = [a for a in answers if a.get("page") == n]
        for nm, err in failed.items():
            summary["failed"][nm] = err.what_happened
        method = "vector" if vector else "raster_trace"
        if not vector and not usable:
            # no usable mask: the model's traced outlines, rasterised as polygons (never boxes)
            outlines_mm = [
                geometry.outline_mm(w["outline"], page_mm) for w in readings[primary].get("walls", [])
            ]
            vp = vector_walls.from_polygons([o for o in outlines_mm if o], page_mm, ratio or 100, rules.walls)
            summary["notes"].append(NO_VECTOR_NOTE.format(page=n, model=model_label(ctx, primary)))
        if code_all is None:  # the walls were only known after the model traced them
            code_all = _code_reading(ctx, rules, page, vp, pdf, ratio or 100, mm_per_px)
        walls = vp.walls
        closures = vector_walls.closures(
            vp, rules.areas.footprint_max_gap_mm, rules.walls.min_wall_mm, rules.walls
        )
        exterior = _exterior(walls, closures, rules.areas.raster_mm_per_px)
        schedule = areas.parse_schedule(words, rules)
        whole = geometry.bounds(walls) if walls else (0.0, 0.0, page_mm[0], page_mm[1])
        found = [d for d in readings[primary].get("dwellings", []) if d.get("id")]
        if not found:
            spec_file = job.dir / "house_specification.json"
            spec = read_json(spec_file, STAGE) if spec_file.is_file() else {}
            ids = [d["dwelling_id"] for d in spec.get("DWELLINGS", []) if d.get("dwelling_id")]
            found = [{"id": ids[0] if ids else "DWELLING 1", "region": None, "mirror_of": None}]
        # code is the first reader; the model(s) are second readers. Where code read no rooms, the first
        # model that answered leads, as before.
        lead = primary
        all_readings = readings
        base = readings[primary]
        if code_all and code_all["rooms"]:
            lead = CODE
            base = code_items.as_reading(code_all, readings[primary], page_mm, rules)
            if len(found) > 1:
                _label_dwellings(base, found, whole, page_mm)
            all_readings = {**readings, CODE: base}
        reading = readout.apply_answers(
            base,
            all_readings,
            page_answers,
            rules,
            functools.partial(_reread, ctx, primary, page, ratio),
        )
        labels = {**summary["model_labels"], CODE: rules.readout.code_label}
        reader_names = [CODE, *names] if lead == CODE else names
        scale = {
            "ratio": f"1:{ratio}" if ratio else "NOT FOUND",
            "mm_per_pt": round(factor, 4) if ratio else None,
        }
        models_used = [model_label(ctx, nm) for nm in readings]
        table_models = [labels[CODE], *models_used] if lead == CODE and len(models_used) > 1 else models_used
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
                [o for o in vp.outlines if len(found) == 1 or geometry.inside(_box(o[0]), padded)],
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
            answered = readout.latest(page_answers, d["id"])
            code_json = code_reading.for_region(code_all, padded if len(found) > 1 else None)
            drows = [
                page_rows["scale"],
                *readout.model_rows(
                    d["id"],
                    len(found),
                    {**all_readings, lead: reading},
                    lead,
                    reader_names,
                    list(failed),
                    labels,
                    page_mm,
                    dwalls,
                    rules,
                ),
                *rows.wall_rows(code_json["wall_rows"], rules),
                *rows.clearance_rows(geo, rules),
                *rows.area_rows(results, rules.areas.tolerance, rules),
                *rows.shower_rows(geo, rules),
            ]
            mirror = None
            for other, owalls in traced:
                kind, score = geometry.match(dwalls, owalls)
                if score >= rules.mirror_min_match:
                    drows.append(rows.mirror_row(d["id"], other, kind, score, rules))
                    if answered.get("mirror", {}).get("option") != "different":
                        geo["mirror_of"] = other
                        mirror = {"of": other, "kind": kind, "score": score}
                    break
            _targets(drows, geo, rules)
            drows = rows.assign_tables(readout.finalize(drows, answered), rules)
            traced.append((d["id"], dwalls))
            jsonschema.validate(geo, schema)
            slug = safe_name(d["id"].lower().replace(" ", "-"), 40)
            folder = job.dir / "plan" / slug
            folder.mkdir(parents=True, exist_ok=True)
            atomic_write_json(folder / "geometry.json", geo)
            jsonschema.validate(code_json, code_schema)
            atomic_write_json(folder / "code_reading.json", code_json)
            mm_per_px = factor * 72 / info.get("dpi", 150)
            table = {
                "dwelling": d["id"],
                "models": table_models,
                "rows": drows,
                "summary": (counts := rows.summaries(drows))["all"],
                "main_summary": counts["main"],
                "other_summary": counts["other"],
                "frame_mm": overlay.frame_mm(job.dir / page["image"], mm_per_px, region, rules),
            }
            jsonschema.validate(table, readout_schema)
            atomic_write_json(folder / "readout.json", table)
            atomic_write_text(folder / "readout.md", readout_table.markdown(table, rules))
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
