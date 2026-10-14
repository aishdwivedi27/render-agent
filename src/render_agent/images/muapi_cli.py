"""MuAPI via muapi-cli subprocess: upload references, image edit, read the result.

Flags are never assumed: `supported_flags` reads `muapi image edit --help` and the provider uses
only flags listed there (doctor reports missing ones). The key is passed in the environment only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from render_agent.errors import PauseJobError, PermanentError, TransientError
from render_agent.images.base import RenderRequest
from render_agent.llm.base import is_out_of_credit, out_of_credit

EXE = "muapi"
FLAG_RE = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
URL_RE = re.compile(r"https?://\S+")
# role -> accepted spellings, first one found in --help is used
FLAG_ROLES: dict[str, tuple[str, ...]] = {
    "model": ("--model",),
    "prompt": ("--prompt",),
    "image": ("--image", "--images", "--image-url"),
    "output": ("--output", "--out", "--output-file"),
}
REQUIRED_ROLES = ("model", "prompt", "image")
Runner = Callable[..., subprocess.CompletedProcess[str]]


def supported_flags(run: Runner = subprocess.run, exe: str = EXE) -> set[str]:
    proc = run([exe, "image", "edit", "--help"], capture_output=True, text=True, timeout=60)
    return set(FLAG_RE.findall((proc.stdout or "") + (proc.stderr or "")))


def resolve_flags(flags: set[str]) -> tuple[dict[str, str], list[str]]:
    """(role -> flag, missing required roles)."""
    chosen = {role: next((f for f in names if f in flags), "") for role, names in FLAG_ROLES.items()}
    found = {k: v for k, v in chosen.items() if v}
    return found, [r for r in REQUIRED_ROLES if r not in found]


def doctor_check(run: Runner = subprocess.run) -> tuple[str, str]:
    if not shutil.which(EXE):
        return "WARN", "muapi not on PATH (needed only for images.provider: muapi)"
    try:
        _, missing = resolve_flags(supported_flags(run))
    except (OSError, subprocess.SubprocessError) as exc:
        return "WARN", f"muapi image edit --help failed: {exc}"
    if missing:
        return "WARN", f"muapi image edit --help lists no flag for: {', '.join(missing)}"
    return "OK", "muapi image edit flags found"


class MuapiProvider:
    name = "muapi"
    paid = True

    def __init__(self, api_key: str, model: str, timeout_seconds: int, run: Runner = subprocess.run):
        self.model = model
        self.timeout = timeout_seconds
        self.run = run
        self.env = {**os.environ, "MUAPI_API_KEY": api_key}
        self._flags: dict[str, str] | None = None

    def flags(self) -> dict[str, str]:
        if self._flags is None:
            found, missing = resolve_flags(supported_flags(self.run))
            if missing:
                raise PermanentError(
                    code="MUAPI_FLAGS",
                    what_happened=f"muapi image edit has no flag for: {', '.join(missing)}.",
                    likely_cause="The installed muapi-cli version differs from the one this agent expects.",
                    what_to_do="Run `python -m render_agent doctor`, update muapi-cli, or switch provider.",
                )
            self._flags = found
        return self._flags

    def _call(self, args: list[str]) -> str:
        try:
            proc = self.run([EXE, *args], capture_output=True, text=True, timeout=self.timeout, env=self.env)
        except subprocess.TimeoutExpired as exc:
            raise TransientError(code="IMAGE_TRANSIENT", what_happened=f"muapi timed out: {exc}") from exc
        except OSError as exc:
            raise PermanentError(code="MUAPI_MISSING", what_happened=f"muapi could not start: {exc}") from exc
        if proc.returncode != 0:
            raise map_cli_error((proc.stderr or proc.stdout or "").strip())
        return proc.stdout or ""

    def render(self, req: RenderRequest) -> bytes:
        f = self.flags()
        urls = []
        for ref in req.references:
            m = URL_RE.search(self._call(["upload", "file", str(ref)]))
            if not m:
                raise PermanentError(
                    code="MUAPI_UPLOAD", what_happened=f"muapi upload gave no URL for {ref.name}"
                )
            urls.append(m.group(0))
        args = ["image", "edit", f["model"], self.model, f["prompt"], req.prompt]
        for u in urls:
            args += [f["image"], u]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.png"
            if "output" in f:
                args += [f["output"], str(out)]
            stdout = self._call(args)
            if out.is_file():
                return out.read_bytes()
        m = URL_RE.search(stdout)
        if not m:
            return b""
        return _download(m.group(0).rstrip("\"',)"), self.timeout)


def _download(url: str, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (provider result URL)
            data: Any = resp.read()
            return bytes(data)
    except OSError as exc:
        raise TransientError(
            code="IMAGE_TRANSIENT", what_happened=f"muapi result download failed: {exc}"
        ) from exc


def map_cli_error(message: str) -> Exception:
    low = message.lower()
    short = message[:300]
    if any(w in low for w in ("unauthorized", "invalid api key", "api key", "401", "forbidden", "403")):
        return PauseJobError(
            code="KEY_INVALID",
            what_happened=f"MuAPI rejected the key: {short}",
            likely_cause="MUAPI_API_KEY in .env is wrong, expired or revoked.",
            what_to_do="Update MUAPI_API_KEY in .env, then resume the job.",
        )
    if is_out_of_credit(low) or any(w in low for w in ("credit", "quota", "402")):
        return out_of_credit("MuAPI", message)
    if any(w in low for w in ("rate limit", "429", "timeout", "timed out", "502", "503", "504", "network")):
        return TransientError(code="IMAGE_TRANSIENT", what_happened=f"MuAPI: {short}")
    return PermanentError(
        code="IMAGE_REJECTED",
        what_happened=f"MuAPI rejected the request: {short}",
        likely_cause="A content-policy block or an unsupported option.",
        what_to_do="Read the prompt and reason at review; edit and regenerate, or switch provider.",
    )
