"""Plain-words view choice: "Only external front and back elevation, front night" -> view ids.

Deterministic, no LLM: each phrase (split on commas, "and", "&", "/", ";", new lines) is matched
against every view's id, label and view_types from knowledge/view_set.yaml. The best-scoring views
win; a phrase without "night" never picks a night view when a day view ties. "external"/"internal"
keep exterior or interior views only. Words that place no view are returned so the designer sees
them before anything starts. The view list itself always comes from view_set.yaml.
"""

from __future__ import annotations

import re
from typing import Any

# English words only; the views and their names live in view_set.yaml
SYNONYMS = {"back": "rear", "bed": "bedroom", "bath": "bathroom", "outside": "external", "inside": "internal"}
EXTERIOR_WORDS = {"external", "exterior"}
INTERIOR_WORDS = {"internal", "interior"}
FILLER = {
    "only", "also", "plus", "the", "a", "an", "of", "with", "please", "include", "including", "want",
    "elevation", "view", "render", "image", "shot", "make", "just", "both", "all", "and", "to", "for",
}  # fmt: skip
SPLIT_RE = re.compile(r"[,;/&\n]|\band\b|\bplus\b", re.IGNORECASE)


def _stem(word: str) -> str:
    w = SYNONYMS.get(word, word)
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z]+", text.lower())]


def view_words(view: dict[str, Any]) -> set[str]:
    names = [view["id"].replace("_", " "), view.get("label", ""), *view.get("view_types", [])]
    return {w for n in names for w in _words(n)} - FILLER


def interpret(text: str, view_set: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(view ids in view_set order, phrases that matched no view)."""
    views = view_set["views"]
    chosen: set[str] = set()
    unplaced: list[str] = []
    for phrase in (p.strip() for p in SPLIT_RE.split(text or "")):
        words = set(_words(phrase))
        content = words - FILLER - EXTERIOR_WORDS - INTERIOR_WORDS
        if not content:
            continue
        pool = views
        if words & EXTERIOR_WORDS:
            pool = [v for v in pool if not v.get("interior")]
        elif words & INTERIOR_WORDS:
            pool = [v for v in pool if v.get("interior")]
        scored = [(len(content & view_words(v)), v) for v in pool]
        best = max((s for s, _ in scored), default=0)
        if best == 0:
            unplaced.append(phrase)
            continue
        top = [v for s, v in scored if s == best]
        if "night" not in content and any("night" not in view_words(v) for v in top):
            top = [v for v in top if "night" not in view_words(v)]
        chosen |= {v["id"] for v in top}
    return [v["id"] for v in views if v["id"] in chosen], unplaced


def describe(view_set: dict[str, Any]) -> list[dict[str, Any]]:
    """Every view for the app's checklist, with a plain note on when the plan can include it."""
    out = []
    for v in view_set["views"]:
        when = v.get("include_when", "")
        if v.get("min_dwellings"):
            note = f"only for {v['min_dwellings']} or more dwellings"
        elif v.get("requires_documented"):
            note = "only if the drawings show a " + ", ".join(v["requires_documented"]["words"][:3])
        elif v.get("optional"):
            note = "made only when you ask for it"
        elif when == "always":
            note = "always made"
        else:
            note = "only if the drawings show it"
        out.append(
            {
                "id": v["id"],
                "label": v.get("label", v["id"]),
                "interior": bool(v.get("interior")),
                "required": bool(v.get("required")),
                "note": note,
            }
        )
    return out
