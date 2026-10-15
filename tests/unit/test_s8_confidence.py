"""Drawing-check confidence floor: code, not the model, turns a low-confidence pass into "unsure"."""

from __future__ import annotations

import pytest

from render_agent.pipeline.stages.s8_check import confidence, decide


def _answer(result: str = "pass", conf: float = 0.95, match: float = 0.9) -> dict:
    return {
        "render_id": "R1",
        "checks": [
            {"check": "storeys", "result": "pass", "note": "", "confidence": 0.99},
            {"check": "roof_form", "result": result, "note": "", "confidence": conf},
        ],
        "image_match": match,
        "overall": "pass",
        "fix_instructions": [],
    }


def test_confidence_is_the_weakest_value():
    assert confidence(_answer(conf=0.7, match=0.9)) == 0.7
    assert confidence(_answer(conf=0.95, match=0.8)) == 0.8


@pytest.mark.parametrize(
    ("conf", "match", "want"),
    [(0.95, 0.9, "pass"), (0.95, 0.84, "unsure"), (0.84, 0.9, "unsure"), (0.85, 0.85, "pass")],
)
def test_floor_gates_a_pass(conf, match, want):
    assert decide(_answer(conf=conf, match=match), "R1", 0.85) == want


def test_fail_ignores_confidence():
    assert decide(_answer(result="fail", conf=0.2, match=0.1), "R1", 0.85) == "fail"


def test_missing_confidence_never_passes():
    a = _answer()
    del a["image_match"]
    assert decide(a, "R1", 0.85) == "unsure"


def test_floor_comes_from_config(install_settings):
    assert install_settings.drawing_check.min_confidence == 0.85
