"""Parity of the code's floor plan reading with the render-geometry key for fixture_03 (PV3 fix round, J).

NOTE: that key (from gen_data_reference.py) describes the photorealistic render, not the 2D plan. The 2D plan
reference for fixture_03 is the owner's first-pass table, tested in test_reference_tables.py.

The code reads the drawing with no model and no cost, is aligned to the key by the best wall-to-wall offset,
and is matched within the key's tolerance (100 mm). The key is
`private/fixtures/fixture_03/plan_answer_key.yaml`, checked by the owner. The draft
(`plan_answer_key.draft.yaml`) is never used by this test unless the developer points
RENDER_AGENT_PARITY_KEY at it, to try the test before the key is confirmed.

Two layers, as in test_reference_tables.py:
- the floors: today's agreement, so a regression fails;
- the targets from the spec (every wall, room, opening, window code and robe matched), marked xfail while the
  gaps listed in docs/parity/fixture_03.md remain, so an improvement shows up as XPASS and is not hidden.
`scripts/parity_report.py` writes the report with the same numbers.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from render_agent.plan.trace_rules import load_trace_rules

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "private" / "fixtures" / "fixture_03"


def _key_path() -> Path | None:
    chosen = os.environ.get("RENDER_AGENT_PARITY_KEY")
    if chosen:
        return Path(chosen)
    key = FIXTURE / "plan_answer_key.yaml"
    return key if key.is_file() else None


pytestmark = pytest.mark.skipif(
    _key_path() is None or not any((FIXTURE / "input").glob("*.pdf")),
    reason="the checked fixture_03 answer key (plan_answer_key.yaml) is not available",
)


def _script():
    spec = importlib.util.spec_from_file_location("parity_report", ROOT / "scripts" / "parity_report.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def parity():
    script = _script()
    rules = load_trace_rules(ROOT / "private" / "knowledge")
    key, _draft = script.load_key(_key_path())
    vp, code = script.read_code(rules)
    return script.compare(key, vp, code), key, code, script


# --- floors: what the code reads today ---------------------------------------------------------------------
def test_the_trace_aligns_to_the_key(parity):
    result, key, _code, _script = parity
    assert result.walls["matched"] >= 0.9 * result.walls["total"]  # a wrong offset would match almost nothing


def test_every_window_code_is_exact_and_every_door_is_found(parity):
    result, _key, _code, _script = parity
    o = result.openings
    assert o["bad_code"] == []
    assert not o["wrong_kind"]
    assert len(o["matched"]) >= o["total"] - 2  # two open walkways are not found (see the report)


def test_every_robe_is_found_as_a_robe(parity):
    result, _key, _code, _script = parity
    assert result.recesses["missing"] == []
    assert all(m[3] for m in result.recesses["matched"])


def test_matched_rooms_agree_within_the_tolerance(parity):
    result, key, _code, _script = parity
    assert len(result.rooms["matched"]) >= 10
    assert all(d <= key["tolerance_mm"] for _n, _c, d in result.rooms["matched"])


def test_extras_are_only_a_few(parity):
    result, _key, _code, _script = parity
    assert len(result.openings["extra"]) <= 6  # allowed, as Orange rows, but they should stay few


# --- targets from the spec ---------------------------------------------------------------------------------
@pytest.mark.xfail(reason="4 key faces between open-plan spaces have no wall (see the report)", strict=False)
def test_every_key_wall_is_within_the_tolerance(parity):
    result, _key, _code, _script = parity
    assert result.walls["missing"] == []


@pytest.mark.xfail(reason="hall, WC, entry hall, the open-plan room and outdoor spaces differ", strict=False)
def test_every_key_room_is_matched_with_corners_within_the_tolerance(parity):
    result, _key, _code, _script = parity
    r = result.rooms
    assert not r["missing"] and not r["merged"] and not r["off"]


@pytest.mark.xfail(reason="two open walkways of the key are not found", strict=False)
def test_every_key_opening_is_matched_by_kind_wall_and_position(parity):
    result, _key, _code, _script = parity
    assert result.openings["missing"] == []
