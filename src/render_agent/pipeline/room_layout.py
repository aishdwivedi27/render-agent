"""Interior room layouts in the viewer's frame (docs/SPEC.md section 6, prompt assembly).

The house specification's ROOMS section records, per room, the openings and fixtures on each wall
as drawn on the sheet (top, bottom, left, right; positions read left to right on top and bottom
walls, top to bottom on left and right walls). This module picks the wall the camera faces from
the view's `camera` rule in knowledge/view_set.yaml and turns the layout into the ROOM LAYOUT block:
"Facing you", "Left wall", "Right wall", "Behind you (not in view)". It also strips size and
opening codes from text that goes to the image model (patterns from view_set.yaml).
"""

from __future__ import annotations

import re
from typing import Any

SIDES = ("top", "right", "bottom", "left")  # clockwise on the page
OPPOSITE = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
# (viewer's left wall, viewer's right wall) when facing each page wall from inside the room
LEFT_RIGHT = {
    "top": ("left", "right"),
    "bottom": ("right", "left"),
    "left": ("bottom", "top"),
    "right": ("top", "bottom"),
}
ORDER = ["left_end", "left_of_centre", "centre", "right_of_centre", "right_end"]
ACROSS = ["at the left end", "left of centre", "at the centre", "right of centre", "at the right end"]
BEHIND = [
    "behind you to your left",
    "behind you, left of centre",
    "directly behind you",
    "behind you, right of centre",
    "behind you to your right",
]
ALONG = [
    "at the far end",
    "towards the far end",
    "halfway along",
    "towards you",
    "at the near end, beside you",
]
KIND_WORDS = {
    "garage_door": "garage door",
    "washing_machine": "washing machine space",
    "wc": "WC",
    "fridge": "fridge space",
    "robe": "built-in robe",
}


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def find_room(
    spec: dict[str, Any], render_req: dict[str, Any], names: list[str], room_words: list[str]
) -> dict[str, Any] | None:
    """The ROOMS entry this render shows, for its dwelling (any alias in `names`). A location often
    names neighbouring rooms too ("ENS off BED 1"), so the view's own room_words decide first:
    1. a room named in the location that matches room_words (longest name first);
    2. else the first room matching room_words, in room_words order;
    3. else the room named in the location with the longest name."""
    wanted = {_key(n) for n in names}
    rooms = [r for r in spec.get("ROOMS", []) if _key(r["dwelling_id"]) in wanted | {"all"}]
    location = f" {_key(render_req.get('location', ''))} "
    named = [r for r in rooms if _key(r["name"]) and f" {_key(r['name'])} " in location]

    def matches(r: dict[str, Any], word: str) -> bool:
        return f" {_key(word)} " in f" {_key(r['name'])} "

    own = [r for r in named if any(matches(r, w) for w in room_words)]
    if own:
        return max(own, key=lambda r: len(_key(r["name"])))
    for word in room_words:
        for r in rooms:
            if matches(r, word):
                return r
    return max(named, key=lambda r: len(_key(r["name"]))) if named else None


def _walls(room: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in SIDES}
    for w in room.get("walls", []):
        if w["side"] in out:
            out[w["side"]].extend(w["items"])
    return out


def usable(room: dict[str, Any] | None) -> bool:
    return bool(room) and any(_walls(room).values())  # type: ignore[arg-type]


def camera_wall(room: dict[str, Any], camera: dict[str, Any]) -> tuple[str | None, str]:
    """(page wall the camera faces, why). Faces the first wall holding a `face` kind (most such items
    first); else the wall opposite the entry (first door or opening). (None, reason) if neither."""
    walls = _walls(room)
    for kind in camera.get("face", []):
        counts = {s: sum(1 for i in items if i["kind"] == kind) for s, items in walls.items()}
        best = max(SIDES, key=lambda s: counts[s])
        if counts[best]:
            return best, f"faces the wall with the {KIND_WORDS.get(kind, kind.replace('_', ' '))}"
    for s in SIDES:
        if any(i["kind"] in ("door", "opening") for i in walls[s]):
            return OPPOSITE[s], "faces the wall opposite the entry"
    return None, "no wall of this room has a usable opening or fixture"


def opening_shape(label: str, rules: dict[str, Any] | None) -> str:
    """Plain words for a size code such as "06-12 obsc" (height-width in hundreds of mm), from
    view_set.yaml `opening_shapes`: "small, high-set, wide and short, obscure glass". The image
    model cannot use numbers, but it needs the proportions. "" when there is no code or no rules."""
    if not rules or not label:
        return ""
    words = []
    m = re.search(rules["code_pattern"], label)
    if m:
        h, w = int(m.group(1)) * 100, int(m.group(2)) * 100
        if h <= rules["high_set_max_height_mm"]:
            words.append("small, high-set")
        elif h >= rules["full_height_min_height_mm"]:
            words.append("full-height")
        if w >= h * rules["wide_ratio"]:
            words.append("wide and short")
        elif w <= h * rules["tall_ratio"]:
            words.append("tall and narrow")
    low = f" {label.lower()} "
    words += [v for k, v in rules.get("type_words", {}).items() if f" {k} " in low]
    return ", ".join(words)


def _item_text(item: dict[str, Any], shapes: dict[str, Any] | None = None) -> str:
    kind = item["kind"]
    words = KIND_WORDS.get(kind, kind.replace("_", " "))
    shape = opening_shape(item.get("label", ""), shapes) if kind in ("window", "door", "opening") else ""
    if shape:
        words += f" ({shape})"
    if kind == "other":  # the label is all we know about it ("DW PROV")
        words = item.get("label", "").strip() or "fixture"
    elif kind in ("door", "opening", "garage_door") and item.get("leads_to", "").strip():
        words += f" to {item['leads_to'].strip()}"
    beside = item.get("beside", "").strip()
    if beside:
        words += (
            f" {beside}"
            if beside.lower().startswith(("beside", "next to", "in ", "on "))
            else f" beside {beside}"
        )
    return words


def _place(item: dict[str, Any], words: list[str], reverse: bool, wall: str) -> str:
    pos = item["position"]
    if pos == "island":
        return f"freestanding, in front of the {wall}"
    if pos == "corner":
        return "in a corner"
    i = ORDER.index(pos) if pos in ORDER else 2
    return words[len(ORDER) - 1 - i if reverse else i]


def _line(
    title: str,
    items: list[dict[str, Any]],
    words: list[str],
    reverse: bool,
    wall: str,
    shapes: dict[str, Any] | None = None,
) -> str:
    if not items:
        return f"{title}: no window, door or fixture."
    ordered = sorted(items, key=lambda it: ORDER.index(it["position"]) if it["position"] in ORDER else 2)
    if reverse:
        ordered.reverse()
    return (
        f"{title}: "
        + "; ".join(f"{_item_text(it, shapes)} {_place(it, words, reverse, wall)}" for it in ordered)
        + "."
    )


def layout_block(
    room: dict[str, Any],
    face: str,
    camera: dict[str, Any],
    reason: str,
    shapes: dict[str, Any] | None = None,
) -> str:
    """The ROOM LAYOUT block in viewer terms for a camera looking at page wall `face`."""
    walls = {s: [i for i in items if i["position"] != "island"] for s, items in _walls(room).items()}
    islands = [i for items in _walls(room).values() for i in items if i["position"] == "island"]
    left, right = LEFT_RIGHT[face]
    behind = OPPOSITE[face]
    reverse = face in ("bottom", "left")  # facing these walls, the viewer reads them against page order
    far_first = face in ("top", "left")  # side walls: the page-order start is the end nearest `face`
    stand = camera.get("stand", "inside the room, with your back to the wall behind you")
    lines = [
        f"ROOM LAYOUT: {room['name']} (from the floor plan; the camera {reason})",
        f"Camera: {stand}, looking straight at the wall facing you.",
        _line("Facing you", walls[face], ACROSS, reverse, "facing wall", shapes),
        _line("Left wall", walls[left], ALONG, not far_first, "left wall", shapes),
        _line("Right wall", walls[right], ALONG, not far_first, "right wall", shapes),
        _line("Behind you (not in view)", walls[behind], BEHIND, reverse, "wall behind you", shapes),
    ]
    if islands:
        lines.append("Freestanding in the room: " + "; ".join(_item_text(i, shapes) for i in islands) + ".")
    others = [w for w in room.get("walls", []) if w["side"] == "other" and w["items"]]
    for w in others:
        lines.append(
            f"Other wall ({w['description'] or 'angled'}): "
            + "; ".join(_item_text(i, shapes) for i in w["items"])
            + "."
        )
    lines.append("Exactly these windows, doors and fixtures on these walls; no others. Sizes may vary.")
    return "\n".join(lines)


def strip_codes(text: str, patterns: list[str]) -> str:
    """Removes size and opening codes (patterns from view_set.yaml) and tidies what is left."""
    for p in patterns:
        text = re.sub(p, "", text)
    text = re.sub(r"\b(?:coded|marked|measuring|sized|of|as)\s+(?=[,.;:)]|and\b|$)", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"([,;])\1+", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def strip_lines(items: list[str], patterns: list[str], drop_words: list[str]) -> list[str]:
    """Code-free lines for the image prompt; lines about sizes (drop_words) are left out entirely."""
    out = []
    for line in items:
        if any(w.lower() in line.lower() for w in drop_words):
            continue
        clean = strip_codes(line, patterns)
        if clean and clean not in out:
            out.append(clean)
    return out
