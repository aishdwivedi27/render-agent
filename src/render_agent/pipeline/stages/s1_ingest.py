"""Ingest: PyMuPDF rasters at config DPI, text layer per page, scanned/encrypted detection.

Outputs pages/page-N.png, pages/page-N.txt and ingest.json (docs/SPEC.md section 6). Pages are
numbered across all input PDFs in name order. Input problems raise InputError (job goes to failed/).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pymupdf

from render_agent.errors import InputError
from render_agent.util.fs import atomic_write_bytes, atomic_write_json, atomic_write_text

if TYPE_CHECKING:
    from render_agent.pipeline.runner import RunContext

log = logging.getLogger("render_agent.s1")
MIN_TEXT_CHARS = 20  # fewer characters than this on a page = treated as a scan
ARTIFACT = "ingest.json"


def _not_readable(name: str, cause: str) -> InputError:
    return InputError(
        code="NOT_A_PDF",
        what_happened=f"'{name}' is not a readable PDF.",
        likely_cause=cause,
        what_to_do=(
            "Export the drawings again as PDF from Revit, put the new file in the Input folder "
            "and press Start."
        ),
    )


def _open_pdf(path: Path, max_mb: int) -> pymupdf.Document:
    size = path.stat().st_size
    if size == 0:
        raise InputError(
            code="PDF_EMPTY",
            what_happened=f"'{path.name}' is empty (0 bytes).",
            likely_cause="The file was still being copied, or the export failed.",
            what_to_do="Put a complete copy of the PDF in the Input folder and press Start.",
        )
    if size > max_mb * 1_000_000:
        raise InputError(
            code="PDF_TOO_LARGE",
            what_happened=f"'{path.name}' is {size / 1e6:.0f} MB; the limit is {max_mb} MB.",
            likely_cause="High-resolution images embedded in the export.",
            what_to_do="Export a smaller PDF, or raise ingest.max_pdf_mb in config.yaml.",
        )
    data = path.read_bytes()  # opened from memory so no file handle blocks moving the job to failed/
    if data[:1024].find(b"%PDF") < 0:
        raise _not_readable(path.name, "The file is not a PDF (wrong type or renamed).")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several unrelated types for damaged files
        raise _not_readable(path.name, f"The file is damaged: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise InputError(
            code="PDF_ENCRYPTED",
            what_happened=f"'{path.name}' is password-protected.",
            likely_cause="The PDF was exported with a password or permissions lock.",
            what_to_do="Put an unlocked copy of the PDF (no password) in the Input folder and press Start.",
        )
    if doc.page_count == 0:
        doc.close()
        raise InputError(code="PDF_EMPTY", what_happened=f"'{path.name}' has no pages.")
    return doc


def _input_files(job_dir: Path, inputs: list[dict[str, str]], suffix: str) -> list[Path]:
    return sorted(job_dir / "input" / i["name"] for i in inputs if i["name"].lower().endswith(suffix))


def run(ctx: RunContext) -> str:
    job, settings = ctx.job, ctx.settings
    out = job.dir / ARTIFACT
    hashes = sorted(i["sha256"] for i in job.state["inputs"])
    if out.exists():
        prev = json.loads(out.read_text(encoding="utf-8"))
        if prev.get("input_hashes") == hashes:
            log.info("job %s: ingest already done, skipping", job.id)
            return ARTIFACT

    pdfs = _input_files(job.dir, job.state["inputs"], ".pdf")
    if not pdfs:
        names = ", ".join(i["name"] for i in job.state["inputs"]) or "nothing"
        raise _not_readable(names, "No .pdf file was in the drop.")
    dpi = settings.ingest.page_dpi
    pages: list[dict[str, Any]] = []
    for pdf in pdfs:
        doc = _open_pdf(pdf, settings.ingest.max_pdf_mb)
        try:
            for page in (doc[i] for i in range(doc.page_count)):
                n = len(pages) + 1
                text = page.get_text("text")
                png, txt = f"pages/page-{n}.png", f"pages/page-{n}.txt"
                atomic_write_bytes(job.dir / png, page.get_pixmap(dpi=dpi).tobytes("png"))
                atomic_write_text(job.dir / txt, text)
                chars = len(text.strip())
                pages.append(
                    {
                        "page": n,
                        "source_file": pdf.name,
                        "source_page": page.number + 1,
                        "image": png,
                        "text": txt,
                        "text_chars": chars,
                        "scanned": chars < MIN_TEXT_CHARS,
                    }
                )
        finally:
            doc.close()

    instructions = "\n\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in _input_files(job.dir, job.state["inputs"], ".txt")
    )
    scanned = [p["page"] for p in pages if p["scanned"]]
    notes = []
    if scanned:
        notes.append(
            f"Pages {scanned} have no text layer (scanned). They are read from the image only, "
            "so findings from them have lower confidence."
        )
        log.warning("job %s: scanned pages %s", job.id, scanned)
    atomic_write_json(
        out,
        {
            "input_hashes": hashes,
            "dpi": dpi,
            "page_count": len(pages),
            "pages": pages,
            "scanned_pages": scanned,
            "instructions": instructions,
            "notes": notes,
        },
    )
    log.info("job %s: ingested %d page(s) from %d PDF(s)", job.id, len(pages), len(pdfs))
    return ARTIFACT
