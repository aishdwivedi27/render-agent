"""Write a comparison page for one fixture after a refinement round (M5.5).

Reads a finished (or partly finished) job and the fixture it came from, and writes
runs/<YYYY-MM-DD>/<fixture>/comparison.html with:
  1. extraction grade against the answer key (and whether the key is verified)
  2. planned views beside the delivered views (final_renders/exterior|interior)
  3. each agent render beside the best-matching delivered render
  4. the drawing-check result per render
  5. designer review status, flags and notes
The fixture's fixture.yaml says where its delivered renders came from (renders_source: designer or
agent_approved); every target shows its source, and agent-approved targets that were not drawing-checked
are flagged as weak.
Missing job files show "not available"; the page never fails because a stage did not run.
Delivered renders are evaluation targets only: they are copied next to the page for viewing and
never go near the image pipeline. All text from the job (PDF text, notes) is HTML-escaped.

Usage: python scripts/compare_fixture.py <fixture_dir> <job_dir> [--out runs/<YYYY-MM-DD>]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from render_agent.util.fs import atomic_write_text  # noqa: E402
from tests.grade_extraction import grade_job, is_verified  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
NA = '<p class="na">not available</p>'
STOP = {"the", "view", "external", "internal", "render", "day", "dwelling", "shared", "v1", "v2", "v3"}


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


def _words(name: str) -> set[str]:
    words = re.split(r"[^a-z0-9]+", name.lower())
    out = set()
    for w in words:
        if not w or w.isdigit() or w in STOP:
            continue
        out.add({"bedroom": "bed", "bath": "bath", "bathroom": "bath", "lounge": "living"}.get(w, w))
    return out


def delivered(fixture_dir: Path) -> list[Path]:
    root = fixture_dir / "final_renders"
    return (
        sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
        if root.is_dir()
        else []
    )


SOURCES = {"designer", "agent_approved"}
CHECKS = {"passed", "not_checked"}


def fixture_meta(fixture_dir: Path) -> dict[str, Any]:
    """fixture.yaml: renders_source (designer | agent_approved; "unknown" when absent) and, for
    agent_approved, a per-file drawing_check (passed | not_checked)."""
    f = fixture_dir / "fixture.yaml"
    data: dict[str, Any] = {}
    if f.is_file():
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
    return {
        "renders_source": str(data.get("renders_source") or "unknown"),
        "renders": data.get("renders") or {},
        **{k: v for k, v in data.items() if k not in ("renders_source", "renders")},
    }


def meta_problems(fixture_dir: Path, meta: dict[str, Any]) -> list[str]:
    """Rule breaks in fixture.yaml: unknown source; an agent-approved render with no recorded
    drawing check, or an invalid one."""
    src = meta["renders_source"]
    if src not in SOURCES:
        return [f"renders_source is {src!r}; expected one of {sorted(SOURCES)} in fixture.yaml"]
    if src != "agent_approved":
        return []
    out = []
    for img in delivered(fixture_dir):
        check = (meta["renders"].get(img.name) or {}).get("drawing_check")
        if check not in CHECKS:
            out.append(f"{img.name}: agent-approved render needs drawing_check passed or not_checked")
    return out


def target_label(meta: dict[str, Any], target: Path) -> str:
    """'designer', 'agent-approved' or 'agent-approved, NOT drawing-checked (weak target)'."""
    src = meta["renders_source"]
    if src != "agent_approved":
        return src
    check = (meta["renders"].get(target.name) or {}).get("drawing_check")
    return "agent-approved" if check == "passed" else "agent-approved, NOT drawing-checked (weak target)"


def _source_html(fixture_dir: Path, meta: dict[str, Any]) -> str:
    src = meta["renders_source"]
    text = {
        "designer": "Delivered renders: designer (independent targets).",
        "agent_approved": "Delivered renders: agent-approved (the agent's own renders, approved by the "
        "owner). Matching them shows consistency with past approved output, not correctness; judge "
        "placement against the plan and the answer key.",
    }.get(src, "Delivered renders: source unknown (no fixture.yaml).")
    cls = "ok" if src == "designer" else "warn"
    problems = meta_problems(fixture_dir, meta)
    extra = (
        '<ul class="bad">' + "".join(f"<li>{escape(p)}</li>" for p in problems) + "</ul>" if problems else ""
    )
    return f'<p class="{cls}">{escape(text)}</p>' + extra


def best_match(view: str, candidates: list[Path]) -> Path | None:
    """Delivered render whose file name shares the most words with the planned view (or None)."""
    want = _words(view)
    scored = [(len(want & _words(c.stem)), c) for c in candidates]
    scored = [s for s in scored if s[0] > 0]
    return max(scored, key=lambda s: s[0])[1] if scored else None


def _copy(src: Path, img_dir: Path, prefix: str) -> str:
    img_dir.mkdir(parents=True, exist_ok=True)
    dest = img_dir / f"{prefix}_{re.sub(r'[^A-Za-z0-9._-]+', '_', src.name)}"
    shutil.copy2(src, dest)
    return f"img/{dest.name}"


def _figure(src: str | None, caption: str) -> str:
    body = f'<img src="{escape(src)}" alt="">' if src else NA
    return f"<figure>{body}<figcaption>{escape(caption)}</figcaption></figure>"


def _grade_html(job_dir: Path, fixture_dir: Path) -> str:
    key_file = fixture_dir / "answer_key.yaml"
    if not key_file.is_file() or not (job_dir / "house_specification.json").is_file():
        return NA
    key = yaml.safe_load(key_file.read_text(encoding="utf-8")) or {}
    verified = is_verified(key)
    report = grade_job(job_dir, fixture_dir)
    auto = [i for i in report.items if i.status != "manual"]
    invented = [i for i in report.forbidden if i.status == "invented"]
    flagged = sum(i.status == "flagged" for i in report.contradictions)
    rows = "".join(
        f"<tr><td>{escape(i.status)}</td><td><code>{escape(i.path)}</code></td>"
        f"<td>{escape(i.value)}</td><td>{escape(i.detail)}</td></tr>"
        for i in [*report.forbidden, *report.contradictions, *report.items]
        if i.status not in ("matched", "absent", "flagged")
    )
    return (
        f'<p class="{"ok" if verified else "warn"}">Answer key: '
        f"{'verified' if verified else 'NOT verified (scores are provisional)'}</p>"
        f"<ul><li>expect: {report.count('matched')}/{len(auto)} matched, {report.count('near')} near, "
        f"{report.count('model_missed')} model_missed, {report.count('key_unverified')} key_unverified, "
        f"{report.count('manual')} manual</li>"
        f'<li class="{"bad" if invented else "ok"}">must_not_appear hits: {len(invented)}</li>'
        f"<li>expected contradictions flagged: {flagged}/{len(report.contradictions)}</li></ul>"
        + (
            f"<table><tr><th>status</th><th>item</th><th>value</th><th>detail</th></tr>{rows}</table>"
            if rows
            else ""
        )
    )


def build(fixture_dir: Path, job_dir: Path, out_dir: Path) -> Path:
    page_dir = out_dir / fixture_dir.name
    img_dir = page_dir / "img"
    prompts = (_json(job_dir / "prompts" / "index.json") or {}).get("renders", [])
    renders = (_json(job_dir / "output" / "render_index.json") or {}).get("renders", {})
    checks = (_json(job_dir / "checks" / "index.json") or {}).get("renders", {})
    review = (_json(job_dir / "review.json") or {}).get("renders", {})
    targets = delivered(fixture_dir)
    meta = fixture_meta(fixture_dir)

    planned_rows, used = [], set()
    for p in prompts:
        rid, view = p.get("render_id", ""), p.get("view_type", "")
        match = best_match(f"{view} {rid}", targets)
        if match:
            used.add(match)
        planned_rows.append(
            f"<tr><td>{escape(rid)}</td><td>{escape(view)}</td><td>{escape(p.get('readiness', ''))}</td>"
            f"<td>{escape(match.relative_to(fixture_dir).as_posix()) if match else '—'}</td>"
            f"<td>{escape(target_label(meta, match)) if match else '—'}</td></tr>"
        )
    unmatched = [t for t in targets if t not in used]
    views_html = (
        "<table><tr><th>render</th><th>view</th><th>readiness</th><th>delivered match</th>"
        "<th>target source</th></tr>" + "".join(planned_rows) + "</table>"
        if planned_rows
        else NA
    ) + (
        "<p>Delivered with no planned match:</p><ul>"
        + "".join(f"<li>{escape(t.relative_to(fixture_dir).as_posix())}</li>" for t in unmatched)
        + "</ul>"
        if unmatched
        else ""
    )

    cards = []
    for p in prompts:
        rid = p.get("render_id", "")
        r = renders.get(rid, {})
        versions = r.get("versions") or []
        agent = job_dir / versions[-1]["file"] if versions else None
        agent_src = _copy(agent, img_dir, "agent") if agent and agent.is_file() else None
        match = best_match(f"{p.get('view_type', '')} {rid}", targets)
        target_src = _copy(match, img_dir, "delivered") if match else None
        c = checks.get(rid, {})
        rv = review.get(rid, {})
        flags = [h for h in rv.get("history", []) if h.get("action") == "flag" or h.get("category")]
        flag_html = "".join(
            f"<li>{escape(str(h.get('category') or ''))}: {escape(str(h.get('note') or ''))}</li>"
            for h in flags
        )
        conf = c.get("confidence")
        agent_cap = f"agent v{len(versions)}" if agent_src else f"agent ({r.get('status', 'not rendered')})"
        target_cap = (
            f"delivered ({target_label(meta, match)}): {match.name}"
            if target_src and match
            else "no delivered match"
        )
        cards.append(
            f'<section class="card"><h3>{escape(rid)} <small>{escape(p.get("view_type", ""))}</small></h3>'
            f'<div class="pair">{_figure(agent_src, agent_cap)}{_figure(target_src, target_cap)}' + "</div>"
            f"<p>Drawing check: <b>{escape(str(c.get('outcome') or 'not available'))}</b>"
            + (f" (confidence {conf})" if conf is not None else "")
            + f"</p><p>Designer review: <b>{escape(str(rv.get('status') or 'not available'))}</b>"
            + (f" — {escape(str(rv['note']))}" if rv.get("note") else "")
            + "</p>"
            + (f"<ul>{flag_html}</ul>" if flag_html else "")
            + "</section>"
        )

    html = PAGE.format(
        title=escape(f"Comparison: {fixture_dir.name}"),
        fixture=escape(fixture_dir.name),
        job=escape(job_dir.name),
        today=date.today().isoformat(),
        source=_source_html(fixture_dir, meta),
        grade=_grade_html(job_dir, fixture_dir),
        views=views_html,
        cards="".join(cards) or NA,
    )
    out = page_dir / "comparison.html"
    atomic_write_text(out, html)
    return out


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#fafaf7; --fg:#1d1d1b; --muted:#6b6b66; --line:#e2e0d8;
  --ok:#2f7d4a; --warn:#a86b00; --bad:#b3261e; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#17171a; --fg:#ececea; --muted:#9a9a95; --line:#33333a; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif;
  margin:0 auto; max-width:1200px; padding:16px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
td,th {{ border-bottom:1px solid var(--line); padding:4px 6px;
   text-align:left; vertical-align:top; }}
.card {{ border:1px solid var(--line); border-radius:8px; padding:12px; margin:12px 0; }}
.pair {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:12px; }}
figure {{ margin:0; }}
img {{ width:100%; height:auto; border-radius:4px; }}
figcaption,small,.na {{ color:var(--muted); }}
.ok {{ color:var(--ok); }}
.warn {{ color:var(--warn); }}
.bad {{ color:var(--bad); font-weight:600; }}
</style></head><body>
<h1>Fixture {fixture}</h1><p><small>job {job} · written {today}</small></p>
{source}
<h2>1. Extraction grade</h2>{grade}
<h2>2. Planned vs delivered views</h2>{views}
<h2>3–5. Renders, drawing check, designer review</h2>{cards}
</body></html>
"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("fixture_dir", type=Path)
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "runs" / date.today().isoformat())
    a = ap.parse_args(argv)
    out = build(a.fixture_dir, a.job_dir, a.out)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
