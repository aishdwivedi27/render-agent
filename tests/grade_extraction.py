"""Grade a house_specification.json against a fixture's answer_key.yaml.

The answer key is a hand-made guide, not ground truth. Every miss is therefore triaged against the
job's own PDF text layer (pages/page-N.txt):
  matched        value found in the spec
  near           most of its words are in the spec (likely reworded; check by eye)
  model_missed   value is in the PDF text but not in the spec
  key_unverified value is in neither the spec nor the PDF text layer: the key may be wrong, or the
                 value is only in the drawing graphics. A person must look at the page image.
  manual         judgement items (relationships, booleans) that text matching cannot decide
must_not_appear hits are also checked against the PDF: a term printed on the drawings is a key
problem, not invented content.

Usage: python -m tests.grade_extraction <job_dir> <fixture_dir>   (writes <job_dir>/grade_report.md)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SKIP_SECTIONS = {"fixture", "pdf", "must_not_appear", "expected_contradictions"}
MANUAL_KEYS = {"relationships"}
NEAR_RATIO = 0.75


def norm(text: str) -> str:
    t = text.lower().replace("m²", "m2").replace("°", " deg ").replace("degrees", "deg")
    t = re.sub(r"[,;:()\"']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str) -> list[str]:
    return [w for w in norm(text).split() if len(w) > 1 or w.isdigit()]


def strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    """(json path, text) for every string, number and dict key in the spec."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append((f"{path}/{k}", str(k)))
            out += strings(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += strings(v, f"{path}[{i}]")
    elif isinstance(node, str | int | float) and not isinstance(node, bool):
        out.append((path, str(node)))
    return out


def key_items(node: Any, path: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if not path and k in SKIP_SECTIONS:
                continue
            out += key_items(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += key_items(v, f"{path}[{i}]")
    else:
        out.append((path, node))
    return out


@dataclass
class Item:
    path: str
    value: str
    status: str
    detail: str = ""


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    forbidden: list[Item] = field(default_factory=list)
    contradictions: list[Item] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for i in self.items if i.status == status)

    @property
    def hard_failures(self) -> list[Item]:
        bad = [i for i in self.forbidden if i.status == "invented"]
        return bad + [i for i in self.contradictions if i.status == "not_flagged"]

    def to_markdown(self) -> str:
        auto = [i for i in self.items if i.status != "manual"]
        lines = [
            "# Extraction grade",
            "",
            f"- expect: {self.count('matched')}/{len(auto)} matched, {self.count('near')} near, "
            f"{self.count('model_missed')} model_missed, {self.count('key_unverified')} key_unverified, "
            f"{self.count('manual')} manual",
            f"- must_not_appear: {sum(i.status == 'invented' for i in self.forbidden)} invented, "
            f"{sum(i.status == 'in_pdf' for i in self.forbidden)} printed on the drawings (key problem)",
            f"- expected contradictions flagged: "
            f"{sum(i.status == 'flagged' for i in self.contradictions)}/{len(self.contradictions)}",
            f"- hard failures: {len(self.hard_failures)}",
            "",
        ]
        for title, group in (
            ("Must not appear", self.forbidden),
            ("Expected contradictions", self.contradictions),
            ("Needs a verdict", [i for i in self.items if i.status not in ("matched",)]),
        ):
            lines += [f"## {title}", ""]
            lines += [f"- **{i.status}** `{i.path}`: {i.value!r} {i.detail}".rstrip() for i in group] or [
                "- none"
            ]
            lines.append("")
        return "\n".join(lines)


def _find(needle: str, hay: list[tuple[str, str]]) -> str | None:
    n = norm(needle)
    for path, text in hay:
        if n in norm(text):
            return path
    return None


def grade(spec: dict[str, Any], key: dict[str, Any], page_texts: dict[int, str] | None = None) -> Report:
    page_texts = page_texts or {}
    spec_strings = strings(spec)
    spec_blob = norm(" ".join(t for _, t in spec_strings))
    spec_words = set(spec_blob.split())
    pdf = [(f"page {n}", t) for n, t in sorted(page_texts.items())]
    report = Report()

    for path, value in key_items(key):
        if (
            isinstance(value, bool)
            or path.split(".")[-1].split("[")[0] in MANUAL_KEYS
            or any(part.split("[")[0] in MANUAL_KEYS for part in path.split("."))
        ):
            report.items.append(Item(path, str(value), "manual", "judge from the spec by reading it"))
            continue
        text = str(value)
        if norm(text) in spec_blob:
            report.items.append(Item(path, text, "matched", f"at {_find(text, spec_strings)}"))
            continue
        toks = tokens(text)
        ratio = sum(t in spec_words for t in toks) / len(toks) if toks else 0.0
        if ratio >= NEAR_RATIO:
            report.items.append(Item(path, text, "near", f"{ratio:.0%} of its words are in the spec"))
            continue
        where = _find(text, pdf)
        if where:
            report.items.append(Item(path, text, "model_missed", f"printed on {where}"))
        else:
            report.items.append(
                Item(path, text, "key_unverified", "not in the spec or the PDF text layer; check the image")
            )

    for term in key.get("must_not_appear", []):
        hit = _find(term, spec_strings)
        if hit is None:
            report.forbidden.append(Item("must_not_appear", term, "absent"))
            continue
        where = _find(term, pdf)
        status = "in_pdf" if where else "invented"
        detail = f"spec {hit}" + (f"; also printed on {where}, so the key is wrong" if where else "")
        report.forbidden.append(Item("must_not_appear", term, status, detail))

    flagged_blob = norm(json.dumps(spec.get("UNCERTAINTIES", spec), ensure_ascii=False))
    for c in key.get("expected_contradictions", []):
        coded = [t for t in tokens(c) if re.fullmatch(r"\d+-\d+", t)]  # label codes like 21-12
        words = [t for t in tokens(c) if len(t) >= 5]
        coded_ok = all(t in flagged_blob for t in coded)
        word_ratio = sum(w in flagged_blob for w in words) / len(words) if words else 1.0
        ok = coded_ok and word_ratio >= 0.4
        report.contradictions.append(
            Item(
                "expected_contradictions",
                c,
                "flagged" if ok else "not_flagged",
                f"codes {coded} present={coded_ok}; {word_ratio:.0%} of key words in UNCERTAINTIES",
            )
        )
    return report


def grade_job(job_dir: Path, fixture_dir: Path) -> Report:
    spec = json.loads((job_dir / "house_specification.json").read_text(encoding="utf-8"))
    key = yaml.safe_load((fixture_dir / "answer_key.yaml").read_text(encoding="utf-8"))
    texts = {
        int(p.stem.split("-")[1]): p.read_text(encoding="utf-8")
        for p in (job_dir / "pages").glob("page-*.txt")
    }
    return grade(spec, key, texts)


def main(argv: list[str]) -> int:
    job_dir, fixture_dir = Path(argv[0]), Path(argv[1])
    report = grade_job(job_dir, fixture_dir)
    md = report.to_markdown()
    (job_dir / "grade_report.md").write_text(md, encoding="utf-8")
    print(md)
    return 1 if report.hard_failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
