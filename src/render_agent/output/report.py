"""report.html: one self-contained page per job (thumbnails by dwelling, review status, drawing-check
result, materials and defaults, budget) with the compliance footer quoted verbatim from
knowledge/visualisation_defaults.md. Images are linked by relative path, so the page works when the
job folder is copied elsewhere."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from render_agent.util.fs import atomic_write_text

REPORT = "report.html"
HEADERS = ["Image", "Render", "Status", "Version", "Drawing check", "Note"]
CSS = """
:root { --bg:#fff; --fg:#1d1d1f; --muted:#666; --line:#ddd; --ok:#e6f4ea; --flag:#fdecea; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16171a; --fg:#eee; --muted:#aaa; --line:#333; --ok:#1d3324; --flag:#3b1f1d; }
}
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif;
       margin:0 auto; max-width:1100px; padding:16px; }
table { border-collapse:collapse; width:100%; }
td, th { border-bottom:1px solid var(--line); padding:6px; text-align:left; vertical-align:top; }
img { max-width:220px; height:auto; display:block; }
tr.approved { background:var(--ok); }
tr.flagged, tr.failed { background:var(--flag); }
pre { white-space:pre-wrap; }
footer { color:var(--muted); margin-top:32px; font-size:13px; }
@media (max-width:640px) { img { max-width:120px; } td:nth-child(5), td:nth-child(6) { display:none; } }
"""


def compliance_footer(knowledge_dir: Path) -> str:
    """The paragraph under '## Compliance wording' in visualisation_defaults.md."""
    text = (knowledge_dir / "visualisation_defaults.md").read_text(encoding="utf-8")
    m = re.search(r"## Compliance wording[^\n]*\n(.*?)(?:\n## |\Z)", text, re.S)
    return " ".join(m.group(1).split()) if m else ""


def write_report(
    job_dir: Path, review: dict[str, Any], manifest: dict[str, Any], knowledge_dir: Path
) -> Path:
    e = html.escape
    rows = []
    for rid, r in sorted(review["renders"].items()):
        v = r.get("version")
        img = f"output/{rid}_v{v}.png" if v else ""
        thumb = f'<a href="{e(img)}"><img src="{e(img)}" alt="{e(rid)}"></a>' if img else "&mdash;"
        rows.append(
            f"<tr class='{e(r['status'])}'><td>{thumb}</td><td>{e(rid)}</td><td>{e(r['status'])}</td>"
            f"<td>{e(str(v or ''))}</td><td>{e(r.get('check') or '')}</td>"
            f"<td>{e(r.get('note') or r.get('reason') or '')}</td></tr>"
        )
    materials = ""
    mat = job_dir / "MATERIALS_AND_DEFAULTS.txt"
    if mat.is_file():
        materials = f"<h2>Materials and colours</h2><pre>{e(mat.read_text(encoding='utf-8'))}</pre>"
    calls = manifest.get("budget", {}).get("calls", [])
    paid = sum(1 for c in calls if c.get("paid"))
    counts = (
        f"Approved and exported: {len(manifest.get('renders', []))}. "
        f"Waiting for review: {len(manifest.get('pending', []))}. "
        f"Failed: {len(manifest.get('failed', []))}. Paid image calls: {paid}."
    )
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in HEADERS) + "</tr>"
    page = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Render report</title>",
            f"<style>{CSS}</style></head><body>",
            f"<h1>Render report: {e(manifest.get('job_id', ''))}</h1>",
            f"<p>Generated {e(manifest.get('generated', ''))}. {counts}</p>",
            "<p>Images below carry the DRAFT overlay; approved renders are delivered clean "
            "in the Common and Dwelling folders.</p>",
            f"<table>{head}{''.join(rows)}</table>",
            materials,
            f"<footer>{e(compliance_footer(knowledge_dir))}</footer>",
            "</body></html>",
            "",
        ]
    )
    path = job_dir / REPORT
    atomic_write_text(path, page)
    return path
