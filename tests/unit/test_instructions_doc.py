"""docs/INSTRUCTIONS.md must list every directive, material item and view the code knows."""

from __future__ import annotations

import yaml

from render_agent.util.directives import _KNOWN
from tests.conftest import ROOT

DOC = (ROOT / "docs" / "INSTRUCTIONS.md").read_text(encoding="utf-8")


def test_every_directive_documented():
    missing = [d for d in sorted(_KNOWN) if f"`@{d}`" not in DOC]
    assert not missing, f"add to docs/INSTRUCTIONS.md: {missing}"


def test_every_material_item_documented():
    items = yaml.safe_load(
        (ROOT / "private" / "knowledge" / "materials_library.yaml").read_text(encoding="utf-8")
    )["items"]
    missing = [k for k in items if f"`{k}`" not in DOC]
    assert not missing, f"add to docs/INSTRUCTIONS.md: {missing}"


def test_every_view_documented_in_priority_order():
    views = [
        v["id"]
        for v in yaml.safe_load(
            (ROOT / "private" / "knowledge" / "view_set.yaml").read_text(encoding="utf-8")
        )["views"]
    ]
    positions = [DOC.find(f"`{v}`") for v in views]
    assert -1 not in positions, (
        f"add to docs/INSTRUCTIONS.md: {[v for v, p in zip(views, positions, strict=True) if p < 0]}"
    )
    assert positions == sorted(positions), "views must be listed in view_set.yaml priority order"
