"""The sample instruction files in docs/examples/ must be valid: no ignored directives, known items."""

from __future__ import annotations

import pytest
import yaml

from render_agent.pipeline.selections import _split_select
from render_agent.util.directives import parse_directives
from tests.conftest import ROOT

EXAMPLES = sorted((ROOT / "docs" / "examples").glob("instructions_*.txt"))
ITEMS = yaml.safe_load(
    (ROOT / "private" / "knowledge" / "materials_library.yaml").read_text(encoding="utf-8")
)["items"]


def test_examples_exist():
    assert len(EXAMPLES) >= 3


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_is_valid(path):
    d = parse_directives(path.read_text(encoding="utf-8"))
    assert d.ignored == []
    for key, value in d.select.items():
        assert _split_select(key, value, ITEMS), f"@select {key}={value} matches no material item"
