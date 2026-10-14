"""Fail if rules, prompts, schemas or code mention a specific fixture project.

Forbidden terms are collected from every private/fixtures/*/answer_key.yaml (project number,
address words, client name), so adding a fixture automatically extends the check.
Exit code 0 = clean, 1 = leaks found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ["src", "private/prompts", "private/knowledge", "schemas", "redacted"]
SKIP_PARTS = {"reference"}  # knowledge/reference holds the original chat-assistant files
GENERIC_WORDS = {
    "rd",
    "road",
    "st",
    "street",
    "ave",
    "the",
    "and",
    "port",
    "north",
    "south",
    "east",
    "west",
    "park",
    "hill",
    "beach",
}


def forbidden_terms() -> set[str]:
    terms: set[str] = set()
    for key_file in (ROOT / "private" / "fixtures").glob("*/answer_key.yaml"):
        data = yaml.safe_load(key_file.read_text(encoding="utf-8")) or {}
        project = data.get("project", {})
        if project.get("project_number"):
            terms.add(str(project["project_number"]))
        if project.get("client"):
            terms.add(str(project["client"]))
        for word in re.split(r"[\s,]+", str(project.get("address", ""))):
            if len(word) > 3 and word.lower() not in GENERIC_WORDS and not word.isdigit():
                terms.add(word)
    return terms


def main() -> int:
    terms = forbidden_terms()
    if not terms:
        print("No fixture answer keys found; nothing to check.")
        return 0
    pattern = re.compile("|".join(rf"\b{re.escape(t)}\b" for t in sorted(terms)), re.IGNORECASE)
    leaks: list[str] = []
    for d in SCAN_DIRS:
        for path in (ROOT / d).rglob("*"):
            if not path.is_file() or SKIP_PARTS & set(path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() not in {".py", ".md", ".txt", ".yaml", ".yml", ".json"}:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if pattern.search(line):
                    leaks.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    if leaks:
        print("Fixture-specific references found (rules must stay generic):")
        print("\n".join(leaks))
        return 1
    print(f"Clean: no references to {len(terms)} fixture terms.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
