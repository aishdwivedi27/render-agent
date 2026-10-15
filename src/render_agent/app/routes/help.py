"""Help: health checks in plain words, Copy diagnostics (no keys), Export feedback."""

from __future__ import annotations

import json
import platform
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from render_agent import __version__
from render_agent.app import designer, feedback
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import all_jobs
from render_agent.app.security import require_token
from render_agent.doctor import plain_checks

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
        *(f"  {c['level']}: {c['text']}" for c in plain_checks(s)),
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
    return {"text": "\n".join(lines)}


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
