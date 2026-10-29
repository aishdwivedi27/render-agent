"""`@key=value` directives from the instruction file (docs/SPEC.md section 8).

Only these documented directives change behaviour; everything else in the file is free text (data).
Unknown or invalid directives are ignored and listed at the gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DIRECTIVE_RE = re.compile(r"^\s*@(\w+)\s*(?:=\s*(.*?)|\s+(\w+)\s*=\s*(.*?))\s*$")
TRUE_WORDS = {"true", "yes", "1", "on"}
FALSE_WORDS = {"false", "no", "0", "off"}


@dataclass
class Directives:
    night: bool | None = None
    streetscape: bool | None = None
    provider: str | None = None
    llm: str | None = None
    dwellings: list[str] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    max_images: int | None = None
    select: dict[str, str] = field(default_factory=dict)
    dry_run: bool | None = None
    readout_llm: str | None = None  # claude | openai | both (PLAN_VISUALS_SPEC section 2)
    plans_mode: str | None = None  # plans | renders_only
    ignored: list[str] = field(default_factory=list)


def _bool(value: str) -> bool | None:
    v = value.strip().lower()
    return True if v in TRUE_WORDS else False if v in FALSE_WORDS else None


def _list(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def parse_directives(text: str) -> Directives:
    d = Directives()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("@"):
            continue
        m = DIRECTIVE_RE.match(line)
        if not m:
            d.ignored.append(f"{line} (not in @key=value form)")
            continue
        key = m.group(1).lower()
        if m.group(3) is not None:  # @select item=value
            if key == "select" and m.group(4):
                d.select[m.group(3).lower()] = m.group(4).strip()
            else:
                d.ignored.append(f"{line} (unknown directive)")
            continue
        value = (m.group(2) or "").strip()
        ok = True
        if key in ("night", "streetscape", "dry_run"):
            b = _bool(value)
            ok = b is not None
            setattr(d, key, b)
        elif key in ("provider", "llm"):
            ok = bool(value)
            setattr(d, key, value.lower() or None)
        elif key in ("dwellings", "views"):
            ok = bool(_list(value))
            setattr(d, key, _list(value))
        elif key in _CHOICES:
            ok = value.lower() in _CHOICES[key]
            setattr(d, key, value.lower() if ok else None)
        elif key == "max_images":
            ok = value.isdigit() and int(value) > 0
            d.max_images = int(value) if ok else None
        else:
            ok = False
        if not ok:
            d.ignored.append(f"{line} ({'unknown directive' if key not in _KNOWN else 'invalid value'})")
    return d


_CHOICES = {"readout_llm": ("claude", "openai", "both"), "plans_mode": ("plans", "renders_only")}
_KNOWN = {
    "night",
    "streetscape",
    "provider",
    "llm",
    "dwellings",
    "views",
    "max_images",
    "select",
    "dry_run",
    *_CHOICES,
}


def with_directive_line(text: str, key: str, line: str) -> str:
    """`text` with its @<key> lines replaced by `line` ('' removes them)."""
    kept = [ln for ln in text.splitlines() if not re.match(rf"\s*@{key}\b", ln, re.IGNORECASE)]
    return "\n".join([*kept, *([line] if line else [])]) + "\n"


def night_requested(text: str, request_words: list[str]) -> bool:
    return view_requested(text, "night", request_words)


def view_requested(text: str, directive: str, request_words: list[str]) -> bool:
    """An optional view: @<directive>=true wins; =false refuses; otherwise plain words in the free text."""
    value = getattr(parse_directives(text), directive, None)
    if value is not None:
        return bool(value)
    free = " ".join(ln for ln in text.lower().splitlines() if not ln.strip().startswith("@"))
    return any(w.lower() in free for w in request_words)
