"""Enforces knowledge/view_set.yaml on the checklist LLM output (M2).

In code, after the LLM answers:
- drop views outside the standard set, and front night unless the instruction file asked for it
- never give the pantry (or minor site services) a render of its own
- keep at most `max_count` of a view per dwelling (one secondary bedroom)
- separate subdivisions: split any shared render into one per dwelling
- insert a missing required front view (NOT READY with a blocker) and a missing documented WIR (REVIEW)
- cap planned renders per dwelling, trimming from the bottom of the priority list (OMITTED_VIEWS)
- recompute DWELLING_SUMMARY, PROJECT_TOTALS and readiness so every count adds up

A shared render (one that stands for several dwellings) counts toward the limit of every dwelling
it covers. Each change is returned as a correction for checklist_validation.json.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

NOT_FOUND = "NOT FOUND"
READINESS_ORDER = ["READY", "REVIEW", "NOT READY"]


def load_view_set(knowledge_dir: Path) -> dict[str, Any]:
    return yaml.safe_load((knowledge_dir / "view_set.yaml").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@dataclass
class Options:
    dwellings: list[str]  # every dwelling id, in order (from the spec)
    limit: int  # already min(config, view_set, @max_images)
    night_requested: bool = False
    views_only: list[str] = field(default_factory=list)  # @views
    dwellings_only: list[str] = field(default_factory=list)  # @dwellings
    documented: dict[str, set[str]] = field(default_factory=dict)  # spec_field -> dwellings documenting it
    project_number: str = NOT_FOUND
    share_groups: dict[str, list[set[str]]] = field(default_factory=dict)  # view id -> groups (from the spec)
    requested: set[str] = field(default_factory=set)  # optional view ids the instruction file asked for
    declined: set[str] = field(default_factory=set)  # view ids turned off by their opt_out_directive
    drawings_present: set[str] | None = None  # view kinds found by stage 2; None = not checked
    qualifying: dict[str, set[str]] = field(
        default_factory=dict
    )  # view id -> dwellings (requires_documented)

    def __post_init__(self) -> None:
        if self.night_requested:
            self.requested.add("front_night")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _mentions(text: str, dwelling: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(dwelling)}(?![\w])", text, re.IGNORECASE) is not None


def dwelling_code(dwelling: str, dwellings: list[str]) -> str:
    m = re.search(r"(\d+)\s*$", dwelling)
    n = m.group(1) if m else str(dwellings.index(dwelling) + 1 if dwelling in dwellings else 1)
    return f"D{n}"


class ViewRules:
    def __init__(self, view_set: dict[str, Any], opts: Options) -> None:
        self.vs, self.o = view_set, opts
        self.views: list[dict[str, Any]] = view_set["views"]
        self.corrections: list[dict[str, Any]] = []
        self.no_own_render_words = [
            _norm(w) for w in [*view_set.get("no_own_render", []), *view_set.get("ignore_for_planning", [])]
        ]

    # --- classification -------------------------------------------------------------------
    def view_id(self, r: dict[str, Any]) -> str | None:
        """The standard view a render stands for, or None when it is outside the set."""
        vt = _norm(r["view_type"])
        candidates = [v for v in self.views if vt in {_norm(x) for x in v.get("view_types", [])}]
        if not candidates:
            return None
        if len(candidates) == 1:
            return str(candidates[0]["id"])
        loc = _norm(f"{r['render_id'].replace('-', ' ').replace('_', ' ')} {r['location']}")
        for v in candidates:
            if v.get("match_words") and any(w in loc for w in v["match_words"]):
                return str(v["id"])
        return str(next(v for v in candidates if not v.get("match_words"))["id"])

    def no_own_render(self, r: dict[str, Any]) -> bool:
        """A pantry or minor site service named as the subject; the kitchen may still show the pantry."""
        if _norm(r["view_type"]) == "kitchen":
            return False
        loc = _norm(r["location"])
        return any(w in loc for w in self.no_own_render_words)

    def rank(self, r: dict[str, Any]) -> int:
        vid = self.view_id(r)
        ids = [v["id"] for v in self.views]
        return ids.index(vid) if vid in ids else len(ids)

    def covers(self, r: dict[str, Any]) -> list[str]:
        """Dwellings a render stands for: its own dwelling, every dwelling named in `location`
        and every dwelling it is marked identical in."""
        own = r["dwelling_id"]
        same = {_norm(x) for x in r.get("identical_in_dwellings", [])}
        named = [
            d for d in self.o.dwellings if d != own and (_mentions(r["location"], d) or _norm(d) in same)
        ]
        return ([own] if own in self.o.dwellings or not self.o.dwellings else []) + named

    def log(self, rule: str, r: dict[str, Any] | None, detail: str, dwelling: str | None = None) -> None:
        self.corrections.append(
            {
                "rule": rule,
                "render_id": r["render_id"] if r else None,
                "dwelling_id": dwelling or (r["dwelling_id"] if r else None),
                "detail": detail,
            }
        )

    def omit(self, cl: dict[str, Any], r: dict[str, Any], reason: str, budget: bool = False) -> None:
        cl["OMITTED_VIEWS"].append(
            {
                "dwelling_id": r["dwelling_id"],
                "view_type": r["view_type"],
                "reason_omitted": reason,
                "budget_limited": budget,
            }
        )

    # --- rules ----------------------------------------------------------------------------
    def drop_outside_set(self, cl: dict[str, Any]) -> None:
        wanted = {_norm(v) for v in self.o.views_only}
        labels = {
            v["id"]: {_norm(v["id"]), _norm(v["label"]), *(_norm(x) for x in v.get("view_types", []))}
            for v in self.views
        }
        keep = []
        for r in cl["RENDER_REQUIREMENTS"]:
            vid = self.view_id(r)
            view = next((v for v in self.views if v["id"] == vid), None)
            reason = None
            if vid is None:
                reason = f"'{r['view_type']}' is not in the standard view set"
            elif view and len(self.o.dwellings) < view.get("min_dwellings", 0):
                reason = f"only for {view['min_dwellings']} or more dwellings"
            elif view and vid in self.o.declined:
                reason = f"turned off with @{view['opt_out_directive']}=false"
            elif view and view.get("optional") and view["id"] not in self.o.requested:
                reason = "optional view not requested in the instruction file"
            elif (
                view
                and view["id"] in self.o.qualifying
                and not set(self.covers(r)) & self.o.qualifying[view["id"]]
            ):
                reason = (
                    f"{view['label'].lower()}: the drawings show no "
                    + "/".join(view["requires_documented"]["words"][:3])
                    + " for this dwelling"
                )
            elif self.no_own_render(r):
                reason = "never gets a render of its own"
            elif wanted and not (view and view.get("required")) and not (labels.get(vid, set()) & wanted):
                reason = "not in the @views directive"
            elif self.o.dwellings_only and not any(
                _norm(d) in {_norm(x) for x in self.o.dwellings_only} for d in self.covers(r)
            ):
                reason = "dwelling not in the @dwellings directive"
            if reason:
                self.log("drop_view", r, reason)
                self.omit(cl, r, reason)
            else:
                keep.append(r)
        cl["RENDER_REQUIREMENTS"] = keep

    def split_shared_for_separate(self, cl: dict[str, Any]) -> None:
        out = []
        for r in cl["RENDER_REQUIREMENTS"]:
            cov = self.covers(r)
            if len(cov) <= 1 or self.whole_site(r):
                out.append(r)
                continue
            self.log(
                "separate_subdivision", r, f"split a shared render into one per dwelling: {', '.join(cov)}"
            )
            for d in cov:
                c = copy.deepcopy(r)
                c["dwelling_id"] = d
                c["render_id"] = _rid(r["render_id"], d, self.o)
                c["location"] = (
                    re.sub(r"(?i)\brepresents\b.*", "", r["location"]).strip(" ;,.") or r["location"]
                )
                for other in cov:
                    if other != d:
                        c["location"] = re.sub(rf"(?i)\b{re.escape(other)}\b", "", c["location"])
                c["location"] = re.sub(r"\s{2,}", " ", c["location"]).strip(" ;,.()") or r["view_type"]
                out.append(c)
        cl["RENDER_REQUIREMENTS"] = out

    def merge_identical(self, cl: dict[str, Any]) -> None:
        """Attached dwellings: renders of the same standard view marked identical become one shared render."""
        rs = cl["RENDER_REQUIREMENTS"]
        parent = list(range(len(rs)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, a in enumerate(rs):
            for j in range(i + 1, len(rs)):
                b = rs[j]
                if self.view_id(a) is None or self.view_id(a) != self.view_id(b):
                    continue
                if _norm(a["view_type"]) != _norm(b["view_type"]):
                    continue
                if (
                    b["dwelling_id"] in self.covers(a)
                    or a["dwelling_id"] in self.covers(b)
                    or self.spec_says_same(str(self.view_id(a)), self.covers(a) + self.covers(b))
                ):
                    parent[find(j)] = find(i)
        groups: dict[int, list[int]] = {}
        for i in range(len(rs)):
            groups.setdefault(find(i), []).append(i)
        out = []
        for root in sorted(groups):
            members = [rs[i] for i in groups[root]]
            if len(members) == 1:
                out.append(members[0])
                continue
            keep = copy.deepcopy(members[0])
            cov = [d for d in self.o.dwellings if any(d in self.covers(m) for m in members)]
            keep["identical_in_dwellings"] = [d for d in cov if d != keep["dwelling_id"]]
            keep["location"] = re.sub(r"(?i)\s*represents\b.*", "", keep["location"]).strip(" ;,.")
            keep["location"] = f"{keep['location']}. Represents {', '.join(cov)}".lstrip(". ")
            if "SHARED" not in keep["render_id"]:
                code = dwelling_code(keep["dwelling_id"], self.o.dwellings)
                keep["render_id"] = re.sub(rf"\b{code}\b", "SHARED", keep["render_id"], count=1)
                if "SHARED" not in keep["render_id"]:
                    keep["render_id"] += "-SHARED"
            keep["readiness"] = _worst([m["readiness"] for m in members])
            keep["blockers"] = list(dict.fromkeys(b for m in members for b in m["blockers"]))
            keep["uncertainties"] = list(dict.fromkeys(u for m in members for u in m["uncertainties"]))
            out.append(keep)
            merged = ", ".join(m["render_id"] for m in members)
            self.log("share_identical", keep, f"merged identical views {merged} into {keep['render_id']}")
        cl["RENDER_REQUIREMENTS"] = out

    def spec_says_same(self, view: str, dwellings: list[str]) -> bool:
        return any(set(dwellings) <= g for g in self.o.share_groups.get(view, []))

    def max_count(self, cl: dict[str, Any]) -> None:
        for view in (v for v in self.views if v.get("max_count")):
            for d in self.o.dwellings:
                matching = [
                    r
                    for r in cl["RENDER_REQUIREMENTS"]
                    if self.view_id(r) == view["id"] and d in self.covers(r)
                ]
                for r in matching[view["max_count"] :]:
                    cl["RENDER_REQUIREMENTS"].remove(r)
                    reason = f"only {view['max_count']} {view['label'].lower()} per dwelling"
                    self.log("max_count", r, reason, d)
                    self.omit(cl, r, reason)

    def whole_site(self, r: dict[str, Any]) -> bool:
        """A view that shows every dwelling together (the streetscape) on a site that qualifies."""
        vid = self.view_id(r)
        view = next((v for v in self.views if v["id"] == vid), {})
        return bool(view.get("min_dwellings")) and len(self.o.dwellings) >= view["min_dwellings"]

    def insert_whole_site(self, cl: dict[str, Any]) -> None:
        for view in self.views:
            n = view.get("min_dwellings")
            if not n or len(self.o.dwellings) < n or view["id"] in self.o.declined or self.o.dwellings_only:
                continue
            if any(self.view_id(r) == view["id"] for r in cl["RENDER_REQUIREMENTS"]):
                continue
            r = _new_render(view, self.o.dwellings[0], self.o, required=False)
            r["render_id"] = r["render_id"].replace(
                dwelling_code(self.o.dwellings[0], self.o.dwellings), "SHARED", 1
            )
            r["location"] = f"{view['label']}: {', '.join(self.o.dwellings)}"
            r["priority"] = "REQUIRED EXTERIOR"
            cl["RENDER_REQUIREMENTS"].insert(0, r)
            self.log("insert_view", r, f"{view['label']} was missing; inserted as {r['readiness']}")

    def insert_required(self, cl: dict[str, Any]) -> None:
        self.insert_whole_site(cl)
        for view in self.views:
            field_name = view.get("spec_field")
            if not view.get("required") and not field_name:
                continue
            for d in self.o.dwellings:
                if self.o.dwellings_only and _norm(d) not in {_norm(x) for x in self.o.dwellings_only}:
                    continue
                if field_name and d not in self.o.documented.get(field_name, set()):
                    continue
                if any(
                    self.view_id(r) == view["id"] and d in self.covers(r) for r in cl["RENDER_REQUIREMENTS"]
                ):
                    continue
                r = _new_render(view, d, self.o, required=bool(view.get("required")))
                cl["RENDER_REQUIREMENTS"].append(r)
                if view.get("required"):
                    cl["CRITICAL_ARCHITECTURAL_BLOCKERS"].append(
                        {
                            "dwelling_id": d,
                            "affected_view": r["view_type"],
                            "blocker": r["blockers"][0],
                            "why_it_matters": "A front view is required for every dwelling.",
                        }
                    )
                self.log("insert_view", r, f"{view['label']} was missing; inserted as {r['readiness']}", d)

    def require_drawings(self, cl: dict[str, Any]) -> None:
        """A view whose drawing type is missing from the set is NOT READY with the reason."""
        present = self.o.drawings_present
        if present is None:
            return
        for r in cl["RENDER_REQUIREMENTS"]:
            view = next((v for v in self.views if v["id"] == self.view_id(r)), {})
            if view.get("interior"):
                need, what = {"floor_plan"}, "floor plan"
            else:
                need, what = {"elevation", "perspective_3d"}, "elevation"
            if present & need:
                continue
            reason = f"No {what} was found in the drawing set, so this view cannot be drawn from it."
            if r["readiness"] != "NOT READY" or reason not in r["blockers"]:
                r["readiness"] = "NOT READY"
                r["blockers"] = [*r["blockers"], reason]
                self.log("missing_drawing", r, reason)

    def trim_to_limit(self, cl: dict[str, Any]) -> None:
        for d in self.o.dwellings:
            while True:
                mine = [r for r in cl["RENDER_REQUIREMENTS"] if d in self.covers(r)]
                if len(mine) <= self.o.limit:
                    break
                droppable = [r for r in mine if not self._required(r)]
                if not droppable:
                    break
                # lowest priority first; among equals, prefer a render only this dwelling uses
                victim = max(droppable, key=lambda r: (self.rank(r), len(self.covers(r)) == 1, mine.index(r)))
                cl["RENDER_REQUIREMENTS"].remove(victim)
                reason = f"over the limit of {self.o.limit} planned renders per dwelling"
                self.log("limit", victim, reason, d)
                self.omit(cl, victim, reason, budget=True)

    def _required(self, r: dict[str, Any]) -> bool:
        if self.whole_site(r):
            return True
        vid = self.view_id(r)
        return any(v["id"] == vid and v.get("required") for v in self.views)

    def recompute(self, cl: dict[str, Any], before: dict[str, Any]) -> None:
        rs = cl["RENDER_REQUIREMENTS"]
        ids = list(self.o.dwellings) or sorted({r["dwelling_id"] for r in rs})
        old = {s["dwelling_id"]: s for s in before.get("DWELLING_SUMMARY", [])}
        summary = []
        for d in ids:
            own = [r for r in rs if r["dwelling_id"] == d]
            counts = {k: sum(1 for r in own if r["readiness"] == k) for k in READINESS_ORDER}
            covered = sum(1 for r in rs if d in self.covers(r))
            reason = old.get(d, {}).get("reason", NOT_FOUND)
            note = f" [validator: {len(own)} renders held for this dwelling, {covered} cover it]"
            summary.append(
                {
                    "dwelling_id": d,
                    "selected_render_count": len(own),
                    "ready_count": counts["READY"],
                    "review_count": counts["REVIEW"],
                    "not_ready_count": counts["NOT READY"],
                    "overall_readiness": _worst([r["readiness"] for r in rs if d in self.covers(r)]),
                    "reason": reason.split(" [validator:")[0] + note,
                }
            )
        stray = [r for r in rs if r["dwelling_id"] not in ids]
        for r in stray:
            self.log("unknown_dwelling", r, "render's dwelling_id is not a documented dwelling")
        cl["DWELLING_SUMMARY"] = summary
        totals = {
            "selected_render_count": len(rs),
            "ready_count": sum(1 for r in rs if r["readiness"] == "READY"),
            "review_count": sum(1 for r in rs if r["readiness"] == "REVIEW"),
            "not_ready_count": sum(1 for r in rs if r["readiness"] == "NOT READY"),
            "omitted_view_count": len(cl["OMITTED_VIEWS"]),
            "maximum_allowed_render_count": self.o.limit * max(len(ids), 1),
        }
        old_totals = before.get("PROJECT_TOTALS", {})
        for k, v in totals.items():
            if old_totals.get(k) != v:
                self.log("recount", None, f"PROJECT_TOTALS.{k}: {old_totals.get(k)} -> {v}")
        for s in summary:
            o = old.get(s["dwelling_id"])
            if o and o.get("selected_render_count") != s["selected_render_count"]:
                self.log(
                    "recount",
                    None,
                    f"selected_render_count {o.get('selected_render_count')} -> {s['selected_render_count']}",
                    s["dwelling_id"],
                )
        cl["PROJECT_TOTALS"] = totals
        cl["PROJECT"]["render_budget_per_dwelling"] = self.o.limit
        cl["PROJECT"]["dwelling_count"] = len(ids)
        cl["PROJECT"]["overall_readiness"] = _worst([r["readiness"] for r in rs])


def _worst(levels: list[str]) -> str:
    return max(levels, key=READINESS_ORDER.index) if levels else "NOT READY"


def _rid(base: str, dwelling: str, o: Options) -> str:
    code = dwelling_code(dwelling, o.dwellings)
    return re.sub(r"SHARED", code, base) if "SHARED" in base else f"{base}-{code}"


def _new_render(view: dict[str, Any], dwelling: str, o: Options, required: bool) -> dict[str, Any]:
    view_type = view["view_types"][0]
    code = dwelling_code(dwelling, o.dwellings)
    prefix = o.project_number if o.project_number != NOT_FOUND else "RENDER"
    blocker = f"{view['label']} was not planned; the drawings must be checked before it can be rendered."
    return {
        "render_id": f"{prefix}-{code}-{view['id'].upper().replace('_', '-')}",
        "dwelling_id": dwelling,
        "priority": "REQUIRED EXTERIOR" if required else "KEY INTERIOR",
        "view_type": view_type,
        "location": f"{view['label']}, {dwelling}",
        "identical_in_dwellings": [],
        "selection_rationale": "Inserted by the validator: the standard view set requires this view.",
        "architectural_facts": [],
        "materials_and_colours": [],
        "site_context": [],
        "permitted_visual_variation": [],
        "uncertainties": ["Inserted by the validator; details to be confirmed at the gate."],
        "readiness": "NOT READY" if required else "REVIEW",
        "blockers": [blocker] if required else [],
    }


_RANGE_RE = re.compile(
    r"\b(LOTS?|DWELLINGS?|D)\s*(\d+)\s*(?:-|–|—|to)\s*(?:LOTS?\s*|DWELLINGS?\s*|D\s*)?(\d+)\b", re.IGNORECASE
)
_PAIR_RE = re.compile(r"\b(LOTS|DWELLINGS)\s*(\d+)\s*(?:and|&|/|,)\s*(\d+)\b", re.IGNORECASE)
_ALL_RE = re.compile(r"^\s*ALL\b|\beach dwelling\b|\ball (?:three |two |four )?dwellings\b", re.IGNORECASE)


def _expand_groups(text: str) -> str:
    """'LOTS 2-3' -> 'LOT 2 LOT 3', 'DWELLINGS 2 and 3' -> 'DWELLING 2 DWELLING 3'."""

    def rng(m: re.Match[str]) -> str:
        word, lo, hi = re.sub(r"(?i)s$", "", m.group(1)), int(m.group(2)), int(m.group(3))
        nums = range(lo, hi + 1) if 0 < hi - lo < 10 else (lo, hi)
        return " ".join(f"{word} {n}" for n in nums)

    def pair(m: re.Match[str]) -> str:
        word = re.sub(r"(?i)s$", "", m.group(1))
        return f"{word} {m.group(2)} {word} {m.group(3)}"

    return _PAIR_RE.sub(pair, _RANGE_RE.sub(rng, text))


def dwelling_aliases(spec: dict[str, Any]) -> dict[str, list[str]]:
    """dwelling id -> names the drawings use for it (id, lot id parts, 'D1')."""
    out: dict[str, list[str]] = {}
    ids = [d["dwelling_id"] for d in spec.get("DWELLINGS", []) if d.get("dwelling_id")]
    for d in spec.get("DWELLINGS", []):
        did = d.get("dwelling_id")
        if not did:
            continue
        names = {did, dwelling_code(did, ids)}
        lot = str(d.get("lot_id", ""))
        names |= {p.strip() for p in lot.split("/") if p.strip() and p.strip() != NOT_FOUND}
        # labels inside free wording ("D1 on site plan; LOT 1 on ELEVATION 1")
        names |= {f"{m.group(1).upper()} {m.group(2)}" for m in _LABEL_RE.finditer(lot)}
        out[did] = sorted(names, key=len, reverse=True)
    return out


_LABEL_RE = re.compile(r"\b(LOT|DWELLING|UNIT|TOWNHOUSE|RESIDENCE)\s*(\d+)\b", re.IGNORECASE)


def _named(text: str, aliases: dict[str, list[str]]) -> set[str]:
    found = set()
    for did, names in aliases.items():
        for n in names:
            pat = re.escape(n).replace(r"\ ", r"\s*")
            if re.search(rf"(?<![\w]){pat}(?!\d)", text, re.IGNORECASE):
                found.add(did)
                break
    return found


def _has_word(text: str, words: list[str]) -> bool:
    low = f" {text.lower()} "
    return any(re.search(rf"(?<![a-z]){re.escape(w.strip().lower())}(?![a-z])", low) for w in words)


def share_groups(spec: dict[str, Any], view_set: dict[str, Any]) -> dict[str, list[set[str]]]:
    """Per standard view: groups of dwellings the spec documents as alike (mirrored / repeated)
    with no viewer-noticeable difference for that view in RELATIONSHIPS.differences."""
    aliases = dwelling_aliases(spec)
    ids = list(aliases)
    if len(ids) < 2:
        return {}
    rel = spec.get("RELATIONSHIPS", {})
    related: set[frozenset[str]] = set()
    for key in ("mirrored_relationships", "repeated_elements", "similarities"):
        for line in rel.get(key, []):
            named = set(ids) if _ALL_RE.search(line) else _named(_expand_groups(line), aliases)
            related |= {frozenset((a, b)) for a in named for b in named if a < b}
    # related is transitive: D1 mirrors D2 and D3 repeats D2 makes D1 and D3 alike too
    comp = {d: {d} for d in ids}
    for pair in related:
        a, b = sorted(pair)
        merged = comp[a] | comp[b]
        for d in merged:
            comp[d] = merged
    related = {frozenset((a, b)) for a in ids for b in ids if a < b and b in comp[a]}
    ignore = [str(w) for w in view_set.get("ignore_differences_with", [])]
    ignore_rx = [str(r) for r in view_set.get("ignore_differences_matching", [])]
    style = [str(w) for w in view_set.get("style_only_words", [])]
    contrast = [re.escape(str(w)) for w in view_set.get("difference_contrast_words", [])]
    splitter = r";|\.\s" + "".join(rf"|\b{w}\b" for w in contrast)
    diffs = [_expand_groups(x) for x in rel.get("differences", [])]
    out: dict[str, list[set[str]]] = {}
    for view in view_set["views"]:
        words = view.get("difference_words", [])
        parent = {d: d for d in ids}

        def find(x: str, parent: dict[str, str] = parent) -> str:
            while parent[x] != x:
                x = parent[x]
            return x

        for pair in related:
            a, b = sorted(pair)
            split = False
            for line in diffs:
                for clause in re.split(splitter, line, flags=re.IGNORECASE):
                    if _has_word(clause, style):
                        continue
                    if not _has_word(clause, words) or _has_word(clause, ignore):
                        continue
                    if any(re.search(rx, clause, re.IGNORECASE) for rx in ignore_rx):
                        continue
                    named = _named(clause, aliases)
                    if (a in named) != (b in named):
                        split = True
            if not split:
                parent[find(a)] = find(b)
        groups: dict[str, set[str]] = {}
        for d in ids:
            groups.setdefault(find(d), set()).add(d)
        out[view["id"]] = [g for g in groups.values() if len(g) > 1]
    return out


def documented_fields(spec: dict[str, Any], view_set: dict[str, Any]) -> dict[str, set[str]]:
    """spec_field -> dwellings whose FLOOR_PLAN documents it (anything but empty / NOT FOUND)."""
    out: dict[str, set[str]] = {}
    for view in view_set["views"]:
        f = view.get("spec_field")
        if not f:
            continue
        out[f] = {
            fp["dwelling_id"]
            for fp in spec.get("FLOOR_PLAN", [])
            if str(fp.get(f, "")).strip().upper() not in ("", NOT_FOUND)
        }
    return out


def requested_views(text: str, view_set: dict[str, Any]) -> set[str]:
    """Optional view ids the instruction file asks for (@<request_directive>=true or request_words)."""
    from render_agent.util.directives import view_requested

    return {
        v["id"]
        for v in view_set["views"]
        if v.get("optional")
        and v.get("request_directive")
        and view_requested(text, v["request_directive"], v.get("request_words", []))
    }


def declined_views(text: str, view_set: dict[str, Any]) -> set[str]:
    """View ids switched off with their opt_out_directive (e.g. @streetscape=false)."""
    from render_agent.util.directives import parse_directives

    d = parse_directives(text)
    return {
        v["id"]
        for v in view_set["views"]
        if v.get("opt_out_directive") and getattr(d, v["opt_out_directive"], None) is False
    }


def qualifying_dwellings(spec: dict[str, Any], view_set: dict[str, Any]) -> dict[str, set[str]]:
    """view id -> dwellings whose spec lines (requires_documented.fields) contain one of its words.
    A line that names no dwelling (or says ALL) counts for every dwelling."""
    aliases = dwelling_aliases(spec)
    ids = list(aliases)
    out: dict[str, set[str]] = {}
    for view in view_set["views"]:
        req = view.get("requires_documented")
        if not req:
            continue
        found: set[str] = set()
        for dotted in req["fields"]:
            section, _, key = dotted.partition(".")
            node = spec.get(section, {})
            rows = node if isinstance(node, list) else [node]
            for row in rows:
                values = row.get(key, [])
                for line in values if isinstance(values, list) else [values]:
                    text = str(line)
                    if text.strip().upper() in ("", NOT_FOUND) or not _has_word(text, req["words"]):
                        continue
                    if row.get("dwelling_id") in ids:
                        found.add(row["dwelling_id"])
                        continue
                    named = set() if _ALL_RE.search(text) else _named(_expand_groups(text), aliases)
                    found |= named or set(ids)
        out[view["id"]] = found
    return out


def validate(
    checklist: dict[str, Any], view_set: dict[str, Any], opts: Options
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(validated checklist, corrections). The input is not modified."""
    before = copy.deepcopy(checklist)
    cl = copy.deepcopy(checklist)
    if not opts.dwellings:
        opts.dwellings = [s["dwelling_id"] for s in cl.get("DWELLING_SUMMARY", [])] or ["DWELLING 1"]
    rules = ViewRules(view_set, opts)
    seen: set[str] = set()
    for r in cl["RENDER_REQUIREMENTS"]:
        if r["render_id"] in seen:
            new = f"{r['render_id']}-{len(seen) + 1}"
            rules.log("duplicate_id", r, f"render_id repeated; renamed to {new}")
            r["render_id"] = new
        seen.add(r["render_id"])
    rules.drop_outside_set(cl)
    if len(opts.dwellings) > 1 and subdivision(cl) == "separate":
        for r in cl["RENDER_REQUIREMENTS"]:
            r["identical_in_dwellings"] = []
        rules.split_shared_for_separate(cl)
    elif len(opts.dwellings) > 1:
        rules.merge_identical(cl)
    rules.max_count(cl)
    rules.insert_required(cl)
    rules.require_drawings(cl)
    rules.trim_to_limit(cl)
    rules.recompute(cl, before)
    return cl, rules.corrections


def subdivision(checklist: dict[str, Any]) -> str:
    """'separate' only when the checklist says so and never says attached; attached is the safe default."""
    text = " ".join(s.get("reason", "") for s in checklist.get("DWELLING_SUMMARY", [])).lower()
    return "separate" if "separate" in text and "attached" not in text else "attached"


def coverage(
    checklist: dict[str, Any], view_set: dict[str, Any], dwellings: list[str]
) -> dict[str, list[str]]:
    """dwelling -> render ids that stand for it (own + shared)."""
    rules = ViewRules(view_set, Options(dwellings=dwellings, limit=0))
    return {
        d: [r["render_id"] for r in checklist["RENDER_REQUIREMENTS"] if d in rules.covers(r)]
        for d in dwellings
    }
