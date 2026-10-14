"""Sheet analysis: one vision call per page with image + text layer, schemas/sheet_analysis.json.

Writes sheets/sheet-N.json per page (skipped on resume if already present) and
sheets/sheet-N.notes.json with code-side corrections: invalid bboxes are replaced by the full sheet
(docs/SPEC.md error table), and findings whose source_text is not in the page's text layer are noted.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from render_agent.pipeline.stages._common import drawing_rules, ingest_info, llm_for, load_schema
from render_agent.util.fs import atomic_write_json
from render_agent.util.prompts import load_prompt, quote_block, render, user_instructions_block

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

log = logging.getLogger("render_agent.s2")
STAGE = "sheet_analysis"
FULL_SHEET = {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}
MIN_BBOX_SIDE = 0.01


MIN_SOURCE_COVERAGE = 0.6  # share of a source_text's words that must occur on the page


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[./-][a-z0-9]+)*", text.lower())


def normalise_bboxes(sheet: dict[str, Any]) -> list[str]:
    """Clamps bboxes to [0, 1] in place; replaces empty or inverted ones with the full sheet."""
    notes = []
    for view in sheet.get("views", []):
        b = view["bbox"]
        clamped = {k: min(1.0, max(0.0, float(b[k]))) for k in ("x0", "y0", "x1", "y1")}
        if clamped != {k: float(b[k]) for k in clamped}:
            notes.append(f"{view['view_id']}: bbox clamped to the page")
        too_small = (clamped["x1"] - clamped["x0"] < MIN_BBOX_SIDE) or (
            clamped["y1"] - clamped["y0"] < MIN_BBOX_SIDE
        )
        if too_small:
            notes.append(f"{view['view_id']}: invalid or empty bbox {b}; full sheet image used instead")
            clamped = dict(FULL_SHEET)
        view["bbox"] = clamped
    return notes


def unsupported_findings(sheet: dict[str, Any], page_text: str) -> list[str]:
    """Findings whose source_text words are mostly absent from the page text layer.

    Logged for a person, never removed: models join labels with separators and text layers store
    table cells out of order, so only a low word coverage is worth a note.
    """
    page_words = set(_words(page_text))
    out = []
    for f in sheet.get("findings", []):
        src = str(f.get("source_text", ""))
        words = _words(src)
        if not words or src == "NOT FOUND":
            continue
        coverage = sum(w in page_words for w in words) / len(words)
        if coverage < MIN_SOURCE_COVERAGE:
            out.append(f"source_text mostly not in the text layer ({coverage:.0%}): {src[:120]!r}")
    return out


def run(ctx: RunContext) -> str:
    job = ctx.job
    info = ingest_info(ctx)
    schema = load_schema(ctx, STAGE)
    template = load_prompt(ctx.settings.path("prompts"), STAGE)
    instructions = user_instructions_block(info.get("instructions", ""))
    llm = None
    for page in info["pages"]:
        n = page["page"]
        out = job.dir / "sheets" / f"sheet-{n}.json"
        if out.exists():
            log.info("job %s: sheet %d already analysed, skipping", job.id, n)
            continue
        text = (job.dir / page["text"]).read_text(encoding="utf-8")
        page_text = (
            quote_block("PAGE_TEXT", text)
            if not page["scanned"]
            else "(No text layer: this page is a scan. Read everything from the image and lower confidence.)"
        )
        prompt = render(
            template,
            {
                "PAGE_NUMBER": str(n),
                "PAGE_COUNT": str(info["page_count"]),
                "SOURCE_FILE": page["source_file"],
                "PAGE_TEXT": page_text,
                "USER_INSTRUCTIONS": instructions,
                "DRAWING_RULES": drawing_rules(ctx),
            },
        )
        llm = llm or llm_for(ctx, STAGE)
        sheet = llm.call_json(f"{STAGE}.page-{n}", prompt, [job.dir / page["image"]], schema, STAGE)
        notes = normalise_bboxes(sheet)
        if not page["scanned"]:
            notes += unsupported_findings(sheet, text)
        for note in notes:
            log.warning("job %s: sheet %d: %s", job.id, n, note)
        atomic_write_json(job.dir / "sheets" / f"sheet-{n}.notes.json", {"page": n, "notes": notes})
        atomic_write_json(out, sheet)  # written last: its presence marks the page as done
        log.info("job %s: sheet %d analysed (%d views)", job.id, n, len(sheet.get("views", [])))
    return "sheets/"
