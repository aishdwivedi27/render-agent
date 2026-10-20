import shutil
from pathlib import Path

import pytest
import yaml

from render_agent.config import load_config
from render_agent.errors import PermanentError

ROOT = Path(__file__).resolve().parents[2]
KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MUAPI_API_KEY")


@pytest.fixture
def install(tmp_path, monkeypatch):
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    shutil.copy(ROOT / "config.example.yaml", tmp_path / "config.yaml")
    return tmp_path


def _edit(root: Path, fn) -> None:
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    fn(cfg)
    (root / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _invalid(root: Path) -> PermanentError:
    with pytest.raises(PermanentError) as exc:
        load_config(root)
    assert exc.value.code == "CONFIG_INVALID"
    return exc.value


def test_example_config_loads_with_paths_under_root(install):
    s = load_config(install)
    assert s.llm.model == "gpt-5.6-sol"
    assert s.path("jobs") == install.resolve() / "jobs"
    assert s.planning.max_planned_renders_per_dwelling == 16


def test_unknown_key_names_the_setting(install):
    _edit(install, lambda c: c["llm"].update(temprature=0.2))
    err = _invalid(install)
    assert "llm.temprature" in err.what_happened and "unknown setting" in err.what_happened


def test_unknown_top_level_section(install):
    _edit(install, lambda c: c.update(rendering={}))
    assert "'rendering'" in _invalid(install).what_happened


@pytest.mark.parametrize(
    ("section", "key", "bad"),
    [("llm", "model", ""), ("llm", "model", "GPT 5"), ("images.openai", "model", "gpt-image-2!")],
)
def test_bad_model_id_names_the_setting(install, section, key, bad):
    def edit(c):
        node = c
        for part in section.split("."):
            node = node[part]
        node[key] = bad

    _edit(install, edit)
    assert f"{section}.{key}" in _invalid(install).what_happened


def test_model_must_match_provider(install):
    _edit(install, lambda c: c["llm"].update(model="claude-sonnet-5-5"))
    assert "'llm.model'" in _invalid(install).what_happened
    _edit(install, lambda c: c["llm"].update(provider="anthropic"))
    assert load_config(install).llm.model == "claude-sonnet-5-5"


def test_limit_cannot_exceed_16(install):
    _edit(install, lambda c: c["planning"].update(max_planned_renders_per_dwelling=20))
    assert "planning.max_planned_renders_per_dwelling" in _invalid(install).what_happened


def test_review_host_must_be_localhost(install):
    _edit(install, lambda c: c["review"].update(host="0.0.0.0"))
    assert "review.host" in _invalid(install).what_happened


def test_missing_config(tmp_path):
    with pytest.raises(PermanentError) as exc:
        load_config(tmp_path)
    assert exc.value.code == "CONFIG_MISSING"


def test_keys_load_from_dotenv_and_env_wins(install, monkeypatch):
    (install / ".env").write_text("OPENAI_API_KEY=sk-from-dotenv-123456\nMUAPI_API_KEY=\n", encoding="utf-8")
    s = load_config(install)
    assert s.secret("OPENAI_API_KEY") == "sk-from-dotenv-123456"
    assert s.secret("MUAPI_API_KEY") is None
    assert s.required_key_vars() == ["OPENAI_API_KEY"]
    assert "sk-from-dotenv" not in repr(s)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-abcdef")
    assert load_config(install).secret("OPENAI_API_KEY") == "sk-from-env-abcdef"


def test_draft_may_be_cheaper(install):
    _edit(install, lambda c: c["images"]["openai"]["draft"].update(size="1024x576", quality="low"))
    s = load_config(install)
    assert (s.images.openai.draft.size, s.images.openai.draft.quality) == ("1024x576", "low")


def test_render_size_must_be_wxh_and_quality_known(install):
    _edit(install, lambda c: c["images"]["openai"]["draft"].update(size="auto"))
    assert "images.openai.draft.size" in _invalid(install).what_happened
    _edit(install, lambda c: c["images"]["openai"]["draft"].update(size="1536x864", quality="ultra"))
    assert "images.openai.draft.quality" in _invalid(install).what_happened


def test_legacy_settings_still_load(install):
    def legacy(c):
        o = c["images"]["openai"]
        o.update(o.pop("draft"))
        o["final"] = {"size": "2560x1440", "quality": "high"}  # ignored since 20 Oct 2026

    _edit(install, legacy)
    s = load_config(install)
    assert (s.images.openai.draft.size, s.images.openai.draft.quality) == ("1536x864", "medium")


def test_delivery_size_must_be_wxh(install):
    _edit(install, lambda c: c["images"].update(delivery_size="big"))
    assert "images.delivery_size" in _invalid(install).what_happened


def test_final_long_edge_below_hd_refused(install):
    _edit(install, lambda c: c["images"].update(final_long_edge=800))
    assert "images.final_long_edge" in _invalid(install).what_happened
