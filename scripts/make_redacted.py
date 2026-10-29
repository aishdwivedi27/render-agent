"""Builds redacted/ : structure-only copies of the private prompts and knowledge files.

The real prompts/ and knowledge/ are the project's IP and are never committed (.gitignore).
redacted/ is committed instead so the repository shows what files exist and what the code expects,
without the wording, rules, lists or values:

- Markdown: headings and {{PLACEHOLDERS}} are kept; every other line becomes "[redacted]" (runs of
  redacted lines collapse to one).
- YAML: mapping keys are kept (they are the contract with the code, e.g. view ids and item keys);
  every scalar value becomes "<redacted>" and every list keeps one "<redacted>" entry.
- Anything else: only the file name is kept.

Run after changing a private file:  python scripts/make_redacted.py
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ["prompts", "knowledge"]  # under private/; redacted/ mirrors the same names
SRC = ROOT / "private"
OUT = ROOT / "redacted"
PLACEHOLDER = re.compile(r"\{\{[A-Z_]+\}\}")
HEADER = "Redacted copy: structure only. The real file is private and not in this repository.\n"


def redact_markdown(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            out.append(line)
        elif PLACEHOLDER.search(s):
            out.append(" ".join(PLACEHOLDER.findall(s)))
        elif s and (not out or out[-1] != "[redacted]"):
            out.append("[redacted]")
        elif not s and out and out[-1] != "":
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def redact_yaml_value(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: redact_yaml_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [redact_yaml_value(v[0])] if v else []
    if isinstance(v, bool) or v is None:
        return v
    return "<redacted>"


def redact_file(src: Path) -> str:
    if src.suffix == ".md":
        return f"<!-- {HEADER.strip()} -->\n" + redact_markdown(src.read_text(encoding="utf-8"))
    if src.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(src.read_text(encoding="utf-8"))
        body = yaml.safe_dump(redact_yaml_value(data), sort_keys=False, allow_unicode=True)
        return f"# {HEADER}" + body
    return HEADER


def build() -> list[Path]:
    if OUT.exists():
        shutil.rmtree(OUT)
    written = []
    for name in PRIVATE:
        for src in sorted((SRC / name).rglob("*")):
            if not src.is_file():
                continue
            dest = OUT / src.relative_to(SRC)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(redact_file(src), encoding="utf-8")
            written.append(dest)
    (OUT / "README.md").write_text(
        "# Redacted rules and prompts\n\n"
        "Structure-only copies of `private/prompts/` and `private/knowledge/`, which are private. Headings,\n"
        "`{{PLACEHOLDERS}}` and YAML keys are kept; all wording and values are removed.\n"
        "Regenerate with `python scripts/make_redacted.py`. To run the agent or the tests, restore\n"
        "the real `private/prompts/` and `private/knowledge/` folders from the private store.\n",
        encoding="utf-8",
    )
    return written


if __name__ == "__main__":
    missing = [n for n in PRIVATE if not (SRC / n).is_dir()]
    if missing:
        sys.exit(f"private folders missing: {missing}; nothing to redact")
    files = build()
    print(f"redacted/: {len(files)} files written")
