"""Home: what waits in Input, warnings, Start (copy, verify, move to History) and drag-and-drop."""

from __future__ import annotations

import shutil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from render_agent.app import designer, keys
from render_agent.app.main import AppContext
from render_agent.app.models import HomeOut, InputItemOut, InterpretIn, StartIn, StartOut, Warning
from render_agent.app.routes import Ctx
from render_agent.app.routes.jobs import all_jobs
from render_agent.app.security import require_token
from render_agent.pipeline.job import load_job
from render_agent.validate import view_words
from render_agent.validate.checklist_validator import load_view_set

router = APIRouter()
ONEDRIVE_SEEN = "onedrive_warning_seen"
MAX_UPLOAD_MB = 500


def _items(ctx: AppContext) -> list[InputItemOut]:
    return [
        InputItemOut(name=i.name, kind=i.kind, size_mb=i.size_mb, startable=i.startable, reason=i.reason)
        for i in designer.list_inputs(ctx.settings)
    ]


def _disk_low(ctx: AppContext) -> bool:
    free_gb = shutil.disk_usage(ctx.settings.path("jobs").parent).free / 1e9
    return free_gb < ctx.settings.watcher.min_free_disk_gb


def warnings(ctx: AppContext) -> list[Warning]:
    s = ctx.settings
    out = []
    for p in keys.missing_keys(s):
        out.append(
            Warning(
                kind="key",
                text=f"No working {p} key yet. Add it in Settings; until then no job can start.",
            )
        )
    if _disk_low(ctx):
        out.append(
            Warning(
                kind="disk",
                text=(
                    f"Less than {s.watcher.min_free_disk_gb:g} GB of disk space is free. New jobs cannot "
                    "start and running jobs pause. Free up space, then press Retry on paused jobs."
                ),
            )
        )
    f = designer.folders(s)
    if f.on_onedrive and not (s.work_root / ONEDRIVE_SEEN).exists():
        out.append(
            Warning(
                kind="onedrive",
                text=(
                    "Your Desktop is synced by OneDrive. To keep big render files off the cloud, "
                    "exclude the RenderAgent folder on the Desktop from OneDrive sync."
                ),
            )
        )
    return out


@router.get("/home")
def home(ctx: Ctx) -> HomeOut:
    s = ctx.settings
    warns = warnings(ctx)
    return HomeOut(
        inputs=_items(ctx),
        jobs=all_jobs(ctx),
        warnings=warns,
        can_start=not any(w.kind in ("key", "disk") for w in warns),
        providers=keys.available_image_providers(s),
        llm_choices=sorted(s.llm.choices),
        input_folder=str(designer.folders(s).input),
        views=view_words.describe(load_view_set(s.path("knowledge"))),
    )


@router.post("/views/interpret")
def interpret_views(body: InterpretIn, ctx: Ctx) -> dict[str, Any]:
    """Plain words -> the views they name, for the designer to confirm. Deterministic, no cost."""
    view_set = load_view_set(ctx.settings.path("knowledge"))
    ids, unplaced = view_words.interpret(body.text, view_set)
    labels = {v["id"]: v.get("label", v["id"]) for v in view_set["views"]}
    return {"views": [{"id": i, "label": labels[i]} for i in ids], "unplaced": unplaced}


@router.get("/inputs")
def refresh(ctx: Ctx) -> list[InputItemOut]:
    return _items(ctx)


@router.post("/home/dismiss-onedrive", dependencies=[Depends(require_token)])
def dismiss_onedrive(ctx: Ctx) -> dict[str, bool]:
    (ctx.settings.work_root / ONEDRIVE_SEEN).write_text("seen\n", encoding="utf-8")
    return {"ok": True}


@router.post("/inputs/upload", dependencies=[Depends(require_token)])
async def upload(file: UploadFile, ctx: Ctx) -> InputItemOut:
    data = await file.read(MAX_UPLOAD_MB * 1_000_000 + 1)
    if len(data) > MAX_UPLOAD_MB * 1_000_000:
        raise HTTPException(
            413,
            {
                "what_happened": f"That file is larger than {MAX_UPLOAD_MB} MB.",
                "what_to_do": "Copy it into the Input folder on the Desktop instead.",
            },
        )
    path = designer.save_upload(ctx.settings, file.filename or "drawing.pdf", data)
    item = next(i for i in _items(ctx) if i.name == path.stem)
    return item


@router.post("/inputs/start", dependencies=[Depends(require_token)])
def start(body: StartIn, ctx: Ctx) -> StartOut:
    s = ctx.reload_settings()  # keys or config changed since the window opened
    practice = bool(body.options.dry_run)
    if _disk_low(ctx):
        raise HTTPException(
            400,
            {
                "what_happened": "There is not enough free disk space to start a job.",
                "what_to_do": f"Free at least {s.watcher.min_free_disk_gb:g} GB, then press Start again.",
            },
        )
    if not practice and (missing := keys.missing_keys(s)):
        raise HTTPException(
            400,
            {
                "what_happened": f"No working key for {', '.join(missing)}.",
                "what_to_do": "Add the key in Settings and press Test, then press Start again.",
            },
        )
    opts = body.options.model_dump()
    opts["dry_run"] = None  # a practice run is a job setting, not a directive
    if opts["provider"] and opts["provider"] not in keys.available_image_providers(s):
        raise HTTPException(
            400,
            {
                "what_happened": f"The image service '{opts['provider']}' is not available here.",
                "what_to_do": "Choose one of the image services shown on the Start options.",
            },
        )
    item = designer.find_item(s, body.name)
    started = designer.start_item(s, item, opts, start_anyway=body.start_anyway)
    if practice:
        job = load_job(started.job.dir)
        job.state["dry_run"] = True
        job.save()
    ctx.worker.enqueue(started.job.id)
    return StartOut(
        job_id=started.job.id,
        history_folder=str(started.history) if started.history else None,
        output_folder=str(started.output),
        warnings=started.warnings,
    )
