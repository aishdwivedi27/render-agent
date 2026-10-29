"""Help: health checks in plain words, Copy diagnostics (no keys), Export feedback."""

from __future__ import annotations

import json
import platform
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent import __version__
from render_agent.app import designer, feedback, keys
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import all_jobs
from render_agent.app.security import require_token
from render_agent.app.worker import find_job_dir
from render_agent.config import KEY_ENV_VARS
from render_agent.doctor import plain_checks
from render_agent.errors import AgentError
from render_agent.pipeline.job import load_job
from render_agent.plan.health import KEY_LABELS

router = APIRouter()


@router.get("/help/doctor")
def doctor(ctx: Ctx) -> list[dict[str, str]]:
    return plain_checks(ctx.settings)


@router.get("/help/diagnostics")
def diagnostics(ctx: Ctx) -> dict[str, Any]:
    """Text a designer can paste to the developer: versions, folders, checks, recent job states."""
    s = ctx.settings
    lines = [
        f"Render Agent {__version__} on {platform.system()} {platform.release()}",
        f"Program folder: {s.root}",
        f"Working folder: {s.work_root}",
        f"Designer folder: {designer.folders(s, create=False).root}",
        f"Image services enabled: {', '.join(s.images.enabled_providers)}",
        "",
        "Checks:",
        *(
            f"  {c['level']}: {c['text']}" + (f" (fix: {c['fix']})" if c["fix"] else "")
            for c in plain_checks(s)
        ),
        "",
        "Recent jobs:",
    ]
    for j in all_jobs(ctx)[:10]:
        err_file = None
        for root in (s.path("jobs"), s.path("failed")):
            if (root / j.id / "error.json").is_file():
                err_file = root / j.id / "error.json"
        code = ""
        if err_file and j.status in ("FAILED", "PAUSED"):
            try:
                code = " " + str(json.loads(err_file.read_text(encoding="utf-8")).get("code", ""))
            except ValueError:
                code = " (error note unreadable)"
        lines.append(f"  {j.id}: {j.status}{code}")
        lines += _skipped_lines(ctx, j.id)
    return {"text": "\n".join(lines)}


def _skipped_lines(ctx: Ctx, job_id: str) -> list[str]:
    """Skipped stages grouped by reason, e.g. 'skipped: not enabled yet: gate_plan_start, ...'."""
    d = find_job_dir(ctx.settings, job_id)
    try:
        stages = load_job(d).state["stages"] if d else {}  # older jobs gain the new stages here
    except (AgentError, KeyError):
        return []
    by_reason: dict[str, list[str]] = {}
    for name, st in stages.items():
        if st.get("status") == "skipped":
            by_reason.setdefault(str(st.get("reason") or "no reason given"), []).append(name)
    return [f"    skipped: {reason}: {', '.join(names)}" for reason, names in by_reason.items()]


@router.post("/help/check-keys", dependencies=[Depends(require_token)])
def check_keys(ctx: Ctx) -> list[dict[str, Any]]:
    """The no-cost key checks for every saved key (never a paid call; never echoes a key)."""
    s = ctx.settings
    out: list[dict[str, Any]] = []
    for provider in keys.settings_providers(s):
        label = KEY_LABELS.get(provider, provider)
        key = s.secret(KEY_ENV_VARS[provider])
        if not key:
            out.append({"label": label, "ok": False, "message": f"No {label} key saved. Add it in Settings."})
        elif provider == "muapi":  # muapi-cli has no free authentication call
            out.append({"label": label, "ok": True, "message": "MuAPI key saved. It is tested on first use."})
        else:
            ok, message = keys.test_key(provider, key)
            out.append({"label": label, "ok": ok, "message": message})
    return out


@router.post("/help/export-feedback", dependencies=[Depends(require_token)])
def export_feedback(ctx: Ctx) -> dict[str, str]:
    dest = designer.folders(ctx.settings).root
    try:
        out = feedback.export(ctx.settings, dest)
    except OSError as exc:
        raise HTTPException(
            500,
            {
                "what_happened": f"The feedback file could not be saved ({exc.strerror or exc}).",
                "what_to_do": (
                    "Free some disk space or close programs using the RenderAgent folder, then try again."
                ),
            },
        ) from exc
    return {"path": str(out)}
