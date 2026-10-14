"""Reference crops from bboxes + fixed identity block + assembled prompt files (docs/SPEC.md section 6).

Prompt order: fixed identity block (identical in every prompt for a dwelling) -> reference legend ->
scene / camera / lighting from the LLM -> must-preserve and must-avoid plus global rules ->
visualisation choices labelled as undocumented.
"""

from __future__ import annotations

import json
import logging
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from render_agent.pipeline.selections import NOT_FOUND, STANDARD_DEFAULT, final_value
from render_agent.pipeline.stages._common import ingest_info, llm_for, load_schema, read_json
from render_agent.pipeline.stages.s5_checklist import view_catalog
from render_agent.util.fs import atomic_write_bytes, atomic_write_json, atomic_write_text, safe_name
from render_agent.util.prompts import load_prompt, quote_block, render, user_instructions_block
from render_agent.validate.checklist_validator import (
    Options,
    ViewRules,
    dwelling_aliases,
    load_view_set,
)

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

STAGE = "render_prompts"
ARTIFACT = "prompts/index.json"
CROP_PAD = 0.01  # fraction of the page added around each bbox
MIN_BBOX_SIDE = 0.02  # a smaller bbox is treated as invalid
NOT_REFERENCE_KINDS = {"legend", "schedule"}
LABEL_BELOW = 0.06  # dwelling labels may sit this far (page fraction) below an elevation's bbox
log = logging.getLogger("render_agent.s6")


# --- reference crops ----------------------------------------------------------------------------
def valid_bbox(b: dict[str, float]) -> bool:
    return (
        0.0 <= b["x0"] < b["x1"] <= 1.0
        and 0.0 <= b["y0"] < b["y1"] <= 1.0
        and b["x1"] - b["x0"] >= MIN_BBOX_SIDE
        and b["y1"] - b["y0"] >= MIN_BBOX_SIDE
    )


def crop_view(page_png: Path, bbox: dict[str, float], out: Path) -> bool:
    """Crops the bbox (padded) to `out`; the full sheet when the bbox is invalid. True if cropped."""
    with Image.open(page_png) as page:
        img: Image.Image = page
        ok = valid_bbox(bbox)
        if ok:
            w, h = img.size
            box = (
                max(0, int((bbox["x0"] - CROP_PAD) * w)),
                max(0, int((bbox["y0"] - CROP_PAD) * h)),
                min(w, int((bbox["x1"] + CROP_PAD) * w + 0.5)),
                min(h, int((bbox["y1"] + CROP_PAD) * h + 0.5)),
            )
            img = img.crop(box)
        buf = BytesIO()
        img.save(buf, format="PNG")
    atomic_write_bytes(out, buf.getvalue())
    return ok


def page_words(job_dir: Path, page: dict[str, Any]) -> list[tuple[float, float, float, float, str]]:
    """Words of a page's text layer as (x0, y0, x1, y1, text), normalised to 0-1."""
    import pymupdf

    pdf = job_dir / "input" / page["source_file"]
    if not pdf.is_file():
        return []
    with pymupdf.open(pdf) as doc:
        p = doc[int(page["source_page"]) - 1]
        w, h = p.rect.width, p.rect.height
        return [(a / w, b / h, c / w, d / h, str(t)) for a, b, c, d, t, *_ in p.get_text("words")]


def dwelling_regions(
    words: list[tuple[float, float, float, float, str]],
    bbox: dict[str, float],
    aliases: dict[str, list[str]],
) -> dict[str, tuple[float, float]]:
    """dwelling -> (x0, x1) of its part of a multi-dwelling elevation, from the lot/dwelling labels
    printed under it (e.g. "LOT 1"). Boundaries are the midpoints between neighbouring labels.
    Empty unless at least two different dwellings are labelled inside the view."""
    names = {
        n.upper(): d for d, ns in aliases.items() for n in ns if " " in n.strip()
    }  # multi-word labels only ("LOT 1"), never bare codes that clash with door codes
    centres: dict[str, float] = {}
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        label = f"{a[4]} {b[4]}".upper().strip(".,:;()")
        d = names.get(label)
        cx, cy = (a[0] + b[2]) / 2, (a[1] + b[3]) / 2
        if d is None or not (bbox["x0"] <= cx <= bbox["x1"]):
            continue
        if not (bbox["y0"] <= cy <= bbox["y1"] + LABEL_BELOW):
            continue
        centres.setdefault(d, cx)
    if len(centres) < 2:
        return {}
    order = sorted(centres, key=centres.get)  # type: ignore[arg-type]
    xs = [centres[d] for d in order]
    mids = [(xs[i] + xs[i + 1]) / 2 for i in range(len(xs) - 1)]
    left = max(bbox["x0"], xs[0] - (mids[0] - xs[0]))
    right = min(bbox["x1"], xs[-1] + (xs[-1] - mids[-1]))
    edges = [left, *mids, right]
    return {d: (edges[i], edges[i + 1]) for i, d in enumerate(order)}


def make_references(ctx: RunContext, spec: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """view_id -> catalog entry + `file` (relative to the job) + `cropped`; writes references/*.png.
    A multi-dwelling elevation also gets one crop per dwelling, keyed "<view_id>@<dwelling>"."""
    job = ctx.job
    pages = {p["page"]: p for p in ingest_info(ctx)["pages"]}
    aliases = dwelling_aliases(spec or {})
    out: dict[str, dict[str, Any]] = {}
    for n in sorted(pages):
        sheet = read_json(job.dir / "sheets" / f"sheet-{n}.json", STAGE)
        notes_file = job.dir / "sheets" / f"sheet-{n}.notes.json"
        notes = json.loads(notes_file.read_text(encoding="utf-8"))["notes"] if notes_file.is_file() else []
        for v in sheet.get("views", []):
            if v["view_kind"] in NOT_REFERENCE_KINDS or v["view_id"] in out:
                continue
            rel = f"references/{safe_name(v['view_id'], 60)}.png"
            cropped = crop_view(job.dir / pages[n]["image"], v["bbox"], job.dir / rel)
            # stage 2 already swapped an invalid bbox for the full sheet and noted it
            cropped = cropped and not any(nt.startswith(f"{v['view_id']}: invalid") for nt in notes)
            if not cropped:
                log.warning("job %s: view %s has an invalid bbox; full sheet used", job.id, v["view_id"])
            out[v["view_id"]] = {
                "view_id": v["view_id"],
                "page": n,
                "label": v["label"],
                "view_kind": v["view_kind"],
                "facing": v["facing"],
                "dwellings_shown": v["dwellings_shown"],
                "file": rel,
                "cropped": cropped,
                "facade_left_to_right": v.get("facade_left_to_right", []),
            }
            if v["view_kind"] != "elevation" or not cropped or len(aliases) < 2:
                continue
            regions = dwelling_regions(page_words(job.dir, pages[n]), v["bbox"], aliases)
            for d, (x0, x1) in regions.items():
                key = f"{v['view_id']}@{d}"
                sub = f"references/{safe_name(v['view_id'], 60)}-{safe_name(d, 30)}.png"
                box = {**v["bbox"], "x0": x0, "x1": x1}
                crop_view(job.dir / pages[n]["image"], box, job.dir / sub)
                out[key] = {
                    **out[v["view_id"]],
                    "view_id": key,
                    "parent": v["view_id"],
                    "dwelling": d,
                    "label": f"{v['label']}, {d} only",
                    "file": sub,
                }
            if regions:
                log.info("job %s: %s split per dwelling: %s", job.id, v["view_id"], sorted(regions))
    return out


def localise(chosen: list[str], dwelling: str, refs: dict[str, dict[str, Any]]) -> list[str]:
    """Swaps a multi-dwelling elevation for its crop of this dwelling, when one exists."""
    return [f"{v}@{dwelling}" if f"{v}@{dwelling}" in refs else v for v in chosen]


def facade_for(
    chosen: list[str], dwelling: str, refs: dict[str, dict[str, Any]], aliases: dict[str, list[str]]
) -> tuple[list[str], str]:
    """(left-to-right elements of this dwelling's face, source view label) from the first chosen
    elevation that lists them; ([], "") when none does."""
    names = {n.upper() for n in aliases.get(dwelling, [dwelling])}
    for v in chosen:
        ref = refs[v]
        for entry in ref.get("facade_left_to_right", []):
            if entry["dwelling"].strip().upper() in names and entry["items"]:
                return list(entry["items"]), f"{ref['label']} ({entry['dwelling']})"
    return [], ""


def facade_block(items: list[str], source: str) -> str:
    if not items:
        return ""
    lines = [f"FAÇADE, LEFT TO RIGHT (as drawn on {source}; exactly these, no other windows or doors)"]
    lines += [f"{i}. {x}" for i, x in enumerate(items, 1)]
    return "\n".join(lines)


def pick_references(
    asked: list[str],
    render_req: dict[str, Any],
    refs: dict[str, dict[str, Any]],
    rules: ViewRules,
    limit: int,
) -> list[str]:
    """The LLM's choice (known ids only), else the view set's reference rules."""
    chosen = [v for v in dict.fromkeys(asked) if v in refs]
    if not chosen:
        vid = rules.view_id(render_req)
        view = next((v for v in rules.views if v["id"] == vid), {})
        for pattern in view.get("references") or rules.vs.get("default_references", []):
            kind, _, facing = pattern.partition("/")
            for r in refs.values():
                if r["view_kind"] == kind and facing in (r["facing"], "any") and r["view_id"] not in chosen:
                    chosen.append(r["view_id"])
    return chosen[:limit]


# --- prompt assembly ----------------------------------------------------------------------------
def load_swatches(knowledge_dir: Path) -> dict[str, dict[str, str]]:
    """'COLORBOND Monument' -> {range, name, hex, tone} from knowledge/colour_swatches.yaml (optional)."""
    import yaml

    f = knowledge_dir / "colour_swatches.yaml"
    if not f.is_file():
        return {}
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {
        f"{rng} {name}": {"range": rng, "name": name, **vals}
        for rng, colours in (data.get("ranges") or {}).items()
        for name, vals in colours.items()
    }


def swatch_note(text: str, swatches: dict[str, dict[str, str]]) -> str:
    """' [COLORBOND Paperbark: warm light beige, approx. #CABFA4]' for each named colour in the text."""
    import re

    notes = []
    for s in swatches.values():
        if re.search(rf"(?<![A-Za-z]){re.escape(s['name'])}(?![A-Za-z])", text, re.IGNORECASE):
            notes.append(f"{s['range']} {s['name']}: {s['tone']}, approx. {s['hex']}")
    return f" [{'; '.join(notes)}]" if notes else ""


def identity_block(
    spec: dict[str, Any],
    dwelling: str,
    selections: dict[str, Any],
    swatches: dict[str, dict[str, str]] | None = None,
) -> str:
    """Built only from the spec and approved selections, so it is identical for every render of a dwelling.
    A named range colour (e.g. COLORBOND Paperbark) gets its real tone appended, so it renders true."""
    ext = spec.get("EXTERNAL_ARCHITECTURE", {})
    dw: dict[str, Any] = next((d for d in spec.get("DWELLINGS", []) if d.get("dwelling_id") == dwelling), {})
    lines = [f"FIXED IDENTITY: {dwelling} (from the drawings; identical in every image of this dwelling)"]

    def add(label: str, value: Any) -> None:
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, str) and v.strip() and v.strip().upper() != NOT_FOUND:
                lines.append(f"- {label}: {v.strip()}{swatch_note(v, swatches or {})}")

    add("Construction", spec.get("PROJECT", {}).get("construction_description"))
    add("Storeys", dw.get("number_of_storeys"))
    add("Roof", ext.get("roof_type"))
    add("Roof pitch", ext.get("roof_pitch"))
    add("Eaves", ext.get("eaves", []))
    for row in spec.get("MATERIALS_AND_COLOURS", {}).get("schedule_as_documented", []):
        add("Colour schedule", f"{row['item']}: {row['colour']}")
    for item in selections.get("items", []):
        value = final_value(item)
        if value != NOT_FOUND:
            tag = ""
            if item.get("chosen"):
                standard = item.get("chosen_by") == STANDARD_DEFAULT
                tag = " (standard default, not in the drawings)" if standard else " (designer choice)"
            add(item["label"], value + tag)
    return "\n".join(lines)


def _bullets(title: str, items: list[str]) -> str:
    items = [i for i in items if i and i.strip() and i.strip().upper() != NOT_FOUND]
    return f"{title}\n" + ("\n".join(f"- {i}" for i in items) if items else "- (none)")


def default_scene(r: dict[str, Any]) -> dict[str, Any]:
    """Used when the LLM returned nothing for a render (e.g. a dry run). Says nothing about architecture."""
    return {
        "render_id": r["render_id"],
        "scene": f"{r['view_type']}: {r['location']}.",
        "camera": "Eye-level view with corrected verticals.",
        "lighting": "Natural daylight.",
        "people_and_vehicles": "None.",
        "must_preserve": [],
        "must_avoid": [],
        "reference_view_ids": [],
        "aspect": "landscape",
        "visualisation_choices": ["Default scene text (no scene was generated for this render)."],
    }


def _legend_line(i: int, ref: dict[str, Any]) -> str:
    line = f"- Image {i} is {ref['label']} ({ref['facing'].replace('_', ' ')}), sheet {ref['page']}"
    return line + ("" if ref["cropped"] else " (full sheet: the view's outline could not be located)")


def assemble(
    identity: str,
    legend: list[str],
    scene: dict[str, Any],
    render_req: dict[str, Any],
    global_rules: str,
    facade: str = "",
) -> str:
    parts = [
        identity,
        "REFERENCE IMAGES\n" + ("\n".join(legend) if legend else "- No reference drawing is available."),
        f"VIEW: {render_req['view_type']} ({render_req['location']})\n"
        f"Scene: {scene['scene']}\nCamera: {scene['camera']}\nLighting: {scene['lighting']}\n"
        f"People and vehicles: {scene['people_and_vehicles']}",
        *([facade] if facade else []),
        _bullets("MUST PRESERVE", [*render_req.get("architectural_facts", []), *scene["must_preserve"]]),
        _bullets("MUST AVOID", scene["must_avoid"]),
        global_rules.strip(),
        _bullets("VISUALISATION CHOICES (not documented in the drawings)", scene["visualisation_choices"]),
    ]
    return "\n\n".join(parts) + "\n"


def _approved_renders(
    job_dir: Path, checklist: dict[str, Any], view_set: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Renders that stand for at least one dwelling the designer chose at the gate."""
    approval = read_json(job_dir / "approval.json", STAGE)
    renders = checklist["RENDER_REQUIREMENTS"]
    chosen = approval.get("dwellings")
    if not chosen:
        return renders, []
    everyone = [d["dwelling_id"] for d in checklist["DWELLING_SUMMARY"]]
    rules = ViewRules(view_set, Options(dwellings=everyone, limit=0))
    keep = [r for r in renders if set(rules.covers(r)) & set(chosen)]
    skipped = [r["render_id"] for r in renders if r not in keep]
    if skipped:
        log.info("dwelling choice %s: skipped %s", chosen, skipped)
    return keep, skipped


def run(ctx: RunContext) -> str:
    job = ctx.job
    prompts_dir = ctx.settings.path("prompts")
    checklist = read_json(job.dir / "checklist.json", STAGE)
    spec = read_json(job.dir / "house_specification.json", STAGE)
    selections = read_json(job.dir / "selections.json", STAGE)
    instructions = ingest_info(ctx).get("instructions", "")
    max_refs = ctx.settings.images.references_per_render
    view_set = load_view_set(ctx.settings.path("knowledge"))
    rules = ViewRules(view_set, Options(dwellings=[], limit=0))
    refs = make_references(ctx, spec)
    swatches = load_swatches(ctx.settings.path("knowledge"))
    aliases = dwelling_aliases(spec)

    renders, skipped = _approved_renders(job.dir, checklist, view_set)
    scenes: dict[str, dict[str, Any]] = {}
    if renders:
        defaults = (ctx.settings.path("knowledge") / "visualisation_defaults.md").read_text(encoding="utf-8")
        prompt = render(
            load_prompt(prompts_dir, STAGE),
            {
                "MAX_REFERENCES": str(max_refs),
                "VISUALISATION_DEFAULTS": defaults,
                "VIEW_CATALOG": json.dumps(view_catalog(ctx), indent=1, ensure_ascii=False),
                "USER_INSTRUCTIONS": user_instructions_block(instructions),
                "SELECTIONS": quote_block(
                    "SELECTIONS",
                    json.dumps(
                        {i["label"]: final_value(i) for i in selections["items"]},
                        indent=1,
                        ensure_ascii=False,
                    ),
                ),
                "CHECKLIST": quote_block("CHECKLIST", json.dumps(renders, indent=1, ensure_ascii=False)),
                "SPEC": quote_block("HOUSE_SPECIFICATION", json.dumps(spec, indent=1, ensure_ascii=False)),
            },
        )
        answer = llm_for(ctx, STAGE).call_json(
            STAGE, prompt, [], load_schema(ctx, "render_prompts"), "render_prompts"
        )
        scenes = {p["render_id"]: p for p in answer["prompts"]}
    unknown = sorted(set(scenes) - {r["render_id"] for r in renders})
    if unknown:
        log.warning("job %s: prompt LLM returned unknown render ids %s (ignored)", job.id, unknown)

    global_rules = (prompts_dir / "render_rules.md").read_text(encoding="utf-8")
    identities: dict[str, str] = {}
    index = []
    for r in renders:
        scene = scenes.get(r["render_id"])
        source = "llm"
        if scene is None:
            scene, source = default_scene(r), "default"
            log.warning("job %s: no scene for %s; default scene used", job.id, r["render_id"])
        dwelling = r["dwelling_id"]
        identity = identities.setdefault(dwelling, identity_block(spec, dwelling, selections, swatches))
        chosen = pick_references(scene["reference_view_ids"], r, refs, rules, max_refs)
        if rules.view_id(r) != "streetscape":  # the streetscape shows every dwelling: whole elevation
            chosen = localise(chosen, dwelling, refs)
        facade, facade_src = (
            facade_for(chosen, dwelling, refs, aliases) if rules.view_id(r) != "streetscape" else ([], "")
        )
        legend = [_legend_line(i, refs[v]) for i, v in enumerate(chosen, 1)]
        text = assemble(identity, legend, scene, r, global_rules, facade_block(facade, facade_src))
        rel = f"prompts/{safe_name(r['render_id'], 80)}.txt"
        atomic_write_text(job.dir / rel, text)
        index.append(
            {
                "render_id": r["render_id"],
                "dwelling_id": dwelling,
                "view_type": r["view_type"],
                "readiness": r["readiness"],
                "prompt_file": rel,
                "references": [refs[v]["file"] for v in chosen],
                "reference_view_ids": chosen,
                "facade_left_to_right": facade,
                "facade_source": facade_src,
                "aspect": scene["aspect"],
                "scene_source": source,
                "visualisation_choices": scene["visualisation_choices"],
            }
        )
    atomic_write_json(
        job.dir / ARTIFACT,
        {"renders": index, "identity_blocks": identities, "skipped_by_dwelling_choice": skipped},
    )
    return ARTIFACT
