"""Image providers, retry/error mapping, budget guard and DRAFT overlay (no network)."""

from __future__ import annotations

import base64
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest
from PIL import Image
from tenacity import wait_none

from render_agent.errors import PauseJobError, PermanentError, TransientError
from render_agent.images import MockProvider, RenderRequest, get_provider, render_with_retry, size_for
from render_agent.images.budget import Budget, allowances
from render_agent.images.muapi_cli import MuapiProvider, map_cli_error, resolve_flags
from render_agent.images.openai_images import OpenAIImagesProvider
from render_agent.images.overlay import draft_overlay


def _req(tmp_path: Path | None = None, refs: list[Path] | None = None) -> RenderRequest:
    return RenderRequest("R1", "a house\nwith a roof", refs or [], "1536x1024")


def _png(color=(10, 20, 30), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_size_for_turns_portrait():
    assert size_for("1536x1024", "portrait") == "1024x1536"
    assert size_for("1536x1024", "landscape") == "1536x1024"
    assert size_for("auto", "portrait") == "auto"


def test_mock_is_deterministic_and_draws_references(tmp_path):
    ref = tmp_path / "r.png"
    ref.write_bytes(_png((200, 0, 0), (300, 200)))
    a = MockProvider().render(_req(refs=[ref]))
    b = MockProvider().render(_req(refs=[ref]))
    assert a == b
    im = Image.open(io.BytesIO(a))
    assert im.size == (768, 512) and (200, 0, 0) in {im.getpixel((x, 480)) for x in range(16, 200)}


def test_retry_transient_then_success():
    p = MockProvider({"R1": [TransientError(code="T", what_happened="429")] * 2})
    png, attempts = render_with_retry(p, _req(), retries=5, wait=wait_none())
    assert attempts == 3 and png.startswith(b"\x89PNG")


def test_retry_gives_up_after_retries():
    p = MockProvider({"R1": [TransientError(code="T", what_happened="503")] * 5})
    with pytest.raises(TransientError):
        render_with_retry(p, _req(), retries=5, wait=wait_none())
    assert len(p.calls) == 5


def test_content_policy_not_retried():
    p = MockProvider({"R1": [PermanentError(code="IMAGE_REJECTED", what_happened="policy")]})
    with pytest.raises(PermanentError, match="policy"):
        render_with_retry(p, _req(), retries=5, wait=wait_none())
    assert len(p.calls) == 1


def test_unreadable_image_retried_once():
    p = MockProvider({"R1": [b"not a png", b""]})
    with pytest.raises(PermanentError) as e:
        render_with_retry(p, _req(), retries=5, wait=wait_none())
    assert e.value.code == "IMAGE_UNREADABLE" and len(p.calls) == 2
    p = MockProvider({"R1": [b"garbage"]})
    assert render_with_retry(p, _req(), retries=5, wait=wait_none())[1] == 2


def test_budget_refuses_paid_call_over_allowance(tmp_path):
    assert allowances({"D1": 20, "D2": 3}, 16, 1) == {"D1": 32, "D2": 6}
    b = Budget(tmp_path, {"D1": 2})
    b.reserve("R1", "D1", "first", "openai", True)
    b.reserve("R1", "D1", "auto_retry", "openai", True)
    with pytest.raises(PauseJobError) as e:
        b.reserve("R2", "D1", "first", "openai", True)
    assert e.value.code == "BUDGET_EXHAUSTED"
    b.reserve("R2", "D1", "first", "mock", False)  # free calls recorded, never refused
    b.reserve("R1", "D1", "review", "openai", True)  # designer regenerations counted separately
    again = Budget(tmp_path, {"D1": 2})  # persisted across resume
    assert len(again.data["calls"]) == 4 and again.used("D1") == 2
    with pytest.raises(PauseJobError):
        again.reserve("R3", "D1", "first", "openai", True)
    with pytest.raises(PauseJobError):
        Budget(tmp_path, {}).reserve("RX", "D9", "first", "openai", True)  # unplanned dwelling


def test_overlay_changes_pixels_and_keeps_size():
    clean = _png((90, 90, 90), (600, 400))
    draft = draft_overlay(clean)
    a, b = Image.open(io.BytesIO(clean)), Image.open(io.BytesIO(draft))
    assert a.size == b.size and a.tobytes() != b.tobytes()


def test_get_provider(install_settings):
    assert get_provider(install_settings, dry_run=True).name == "mock"
    assert get_provider(install_settings, "mock").name == "mock"
    with pytest.raises(PauseJobError, match="OPENAI_API_KEY"):
        get_provider(install_settings)  # config default is openai, no key in the test install
    with pytest.raises(PauseJobError, match="MUAPI_API_KEY"):
        get_provider(install_settings, "muapi")


# --- OpenAI (fake client, no network) -------------------------------------------------------------
class _Images:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def _go(self, kind, **kw):
        self.calls.append((kind, kw))
        if self.exc:
            raise self.exc
        return self.result

    def edit(self, **kw):
        return self._go("edit", **kw)

    def generate(self, **kw):
        return self._go("generate", **kw)


def _openai(images):
    return OpenAIImagesProvider("k", "gpt-image-2", "high", 60, client=SimpleNamespace(images=images))


def _status_error(cls, status, msg):
    req = httpx.Request("POST", "https://api.openai.com/v1/images/edits")
    return cls(msg, response=httpx.Response(status, request=req), body=None)


def test_openai_edit_with_references_and_generate_without(tmp_path):
    ref = tmp_path / "r.png"
    ref.write_bytes(_png())
    ok = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(_png()).decode())])
    imgs = _Images(ok)
    assert _openai(imgs).render(_req(refs=[ref])) == _png()
    assert imgs.calls[0][0] == "edit" and imgs.calls[0][1]["model"] == "gpt-image-2"
    _openai(imgs).render(_req())
    assert imgs.calls[1][0] == "generate"
    assert _openai(_Images(SimpleNamespace(data=[]))).render(_req()) == b""


@pytest.mark.parametrize(
    ("cls", "status", "msg", "expected", "code"),
    [
        (openai.RateLimitError, 429, "slow down", TransientError, "IMAGE_TRANSIENT"),
        (openai.InternalServerError, 500, "oops", TransientError, "IMAGE_TRANSIENT"),
        (openai.BadRequestError, 400, "content_policy_violation", PermanentError, "IMAGE_REJECTED"),
        (openai.AuthenticationError, 401, "bad key", PauseJobError, "KEY_INVALID"),
        (openai.RateLimitError, 429, "insufficient_quota", PauseJobError, "OUT_OF_CREDIT"),
        (openai.APIStatusError, 402, "payment required", PauseJobError, "OUT_OF_CREDIT"),
        (openai.BadRequestError, 400, "Billing hard limit has been reached", PauseJobError, "OUT_OF_CREDIT"),
    ],
)
def test_openai_error_mapping(cls, status, msg, expected, code):
    with pytest.raises(expected) as e:
        _openai(_Images(exc=_status_error(cls, status, msg))).render(_req())
    assert e.value.code == code


# --- MuAPI (stubbed subprocess) -------------------------------------------------------------------
HELP = "Usage: muapi image edit [OPTIONS]\n  --model TEXT\n  --prompt TEXT\n  --image TEXT\n  --output PATH\n"


def _muapi_runner(log, fail=None):
    def run(args, **kw):
        log.append(args)
        if args[1:] == ["image", "edit", "--help"]:
            return subprocess.CompletedProcess(args, 0, HELP, "")
        if fail:
            return subprocess.CompletedProcess(args, 1, "", fail)
        if args[1] == "upload":
            return subprocess.CompletedProcess(args, 0, "uploaded: https://cdn.example/r.png\n", "")
        out = args[args.index("--output") + 1]
        Path(out).write_bytes(_png())
        return subprocess.CompletedProcess(args, 0, "done", "")

    return run


def test_muapi_uses_help_flags(tmp_path):
    ref = tmp_path / "r.png"
    ref.write_bytes(_png())
    log: list[list[str]] = []
    p = MuapiProvider("secret", "nano-banana-2-edit", 60, run=_muapi_runner(log))
    assert p.render(_req(refs=[ref])) == _png()
    edit = log[-1]
    assert (
        edit[:3] == ["muapi", "image", "edit"] and "--image" in edit and "https://cdn.example/r.png" in edit
    )
    assert "secret" not in " ".join(edit)  # key passed in env only


def test_muapi_missing_flags_and_errors():
    assert resolve_flags({"--model", "--prompt"})[1] == ["image"]
    assert isinstance(map_cli_error("Error 401 Unauthorized"), PauseJobError)
    assert isinstance(map_cli_error("insufficient credits"), PauseJobError)
    assert isinstance(map_cli_error("429 rate limit"), TransientError)
    assert isinstance(map_cli_error("prompt blocked"), PermanentError)
    log: list[list[str]] = []
    p = MuapiProvider("k", "m", 60, run=_muapi_runner(log, fail="503 Service Unavailable"))
    with pytest.raises(TransientError):
        p.render(_req())
