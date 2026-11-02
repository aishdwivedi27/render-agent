"""The readout as markdown (plan/<dwelling>/readout.md): the same tables the screen shows, so a reading can
be compared side by side with the Claude project's table (PLAN_VISUALS_SPEC section 4).

Columns are exactly: # | Item | What I read | Evidence | Confidence | Question for you, with one column per
reader after "What I read" only when more than one model read the drawing. Rows that are not part of the
project's table are in a second table, "Other checks", with its own summary line.
"""

from __future__ import annotations

from typing import Any

from render_agent.plan import rows
from render_agent.plan.trace_rules import TraceRules

FACE = {"GREEN": "🟢 Green", "ORANGE": "🟠 Orange", "RED": "🔴 Red"}


def _cell(text: Any) -> str:
    return str(text or "").replace("|", r"\|").replace("\n", " ")


def _reading(r: dict[str, Any]) -> str:
    text = _cell(r["reading"])
    if r.get("changed"):
        text += f" ({_cell(r['changed'])})"
    if r.get("answer"):
        text += f" Your answer: {_cell(r['answer'])}"
    return text


def table(table_rows: list[dict[str, Any]], models: list[str], start: int = 1) -> list[str]:
    per = models if len(models) > 1 else []
    head = ["#", "Item", "What I read", *per, "Evidence", "Confidence", "Question for you"]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for n, r in enumerate(table_rows, start=start):
        cells = [str(n), _cell(r["item"]), _reading(r)]
        for i in range(len(per)):
            seen = r.get("readings") or []
            cells.append(_cell(seen[i]["reading"]) if i < len(seen) else "")
        cells += [_cell(r["evidence"]), FACE.get(r["confidence"], r["confidence"]), _cell(r["question"])]
        out.append("| " + " | ".join(cells) + " |")
    return out


def markdown(readout: dict[str, Any], rules: TraceRules) -> str:
    main = [r for r in readout["rows"] if r.get("table", "main") == "main"]
    other = [r for r in readout["rows"] if r.get("table") == "other"]
    models = readout.get("models", [])
    lines = [f"# {readout['dwelling']}: floor plan readout", ""]
    lines += [rows.summary_line(readout["main_summary"]), ""]
    lines += table(main, models)
    if other:
        lines += ["", f"## {rules.readout.other_title}", "", rows.summary_line(readout["other_summary"]), ""]
        lines += table(other, models, start=len(main) + 1)
    if all(r["confidence"] == "GREEN" for r in readout["rows"]):
        lines += ["", "All items are Green."]
    return "\n".join(lines) + "\n"
