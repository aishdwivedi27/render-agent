"""The app: FastAPI on 127.0.0.1, calling the pipeline directly (never the CLI).

create_app() wires the routes, the localhost-only middleware, plain-English error responses and
the static front end. The launcher (launcher.py) starts uvicorn and the native window.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from render_agent.app.security import LOCAL_HOSTS, LocalOnly
from render_agent.app.worker import JobWorker
from render_agent.config import Settings, load_config
from render_agent.errors import AgentError

STATIC = Path(__file__).parent / "static"


class FreshStatic(StaticFiles):
    """No caching: after an update the window always shows the new screens."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


log = logging.getLogger("render_agent.app")


def open_in_explorer(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606 (a folder we created)
    else:  # developers on other systems
        import subprocess

        subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", str(path)])  # noqa: S603


@dataclass
class AppContext:
    settings: Settings
    token: str
    worker: JobWorker
    allowed_hosts: set[str] = field(default_factory=lambda: set(LOCAL_HOSTS))
    open_folder: Callable[[Path], None] = open_in_explorer
    reload: Callable[[], Settings] | None = None
    seen_onedrive_warning: bool = False

    def reload_settings(self) -> Settings:
        self.settings = (self.reload or (lambda: load_config(self.settings.root, self.settings.work_root)))()
        return self.settings


def make_context(
    settings: Settings,
    token: str,
    extras: Callable[[], dict[str, Any]] | None = None,
    **kw: Any,
) -> AppContext:
    holder: dict[str, AppContext] = {}
    worker = JobWorker(lambda: holder["ctx"].settings, extras)
    ctx = AppContext(settings=settings, token=token, worker=worker, **kw)
    holder["ctx"] = ctx
    return ctx


def create_app(ctx: AppContext) -> FastAPI:
    from render_agent.app.routes import approval, help, inputs, jobs, review, settings, views

    app = FastAPI(title="Render Agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.add_middleware(LocalOnly, allowed=ctx.allowed_hosts)
    for module in (inputs, jobs, views, approval, review, settings, help):
        app.include_router(module.router, prefix="/api")

    @app.exception_handler(AgentError)
    async def agent_error(_: Request, err: AgentError) -> JSONResponse:
        body: dict[str, Any] = {"what_happened": err.what_happened, "what_to_do": err.what_to_do}
        if (dup := getattr(err, "duplicate_of", None)) is not None:
            body["duplicate_of"] = dup
            return JSONResponse(body, status_code=409)
        return JSONResponse(body, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, err: HTTPException) -> JSONResponse:
        detail = err.detail if isinstance(err.detail, dict) else {"what_happened": str(err.detail)}
        detail.setdefault("what_to_do", "Go back to Home and try again.")
        return JSONResponse(detail, status_code=err.status_code)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, err: Exception) -> JSONResponse:
        log.exception("app: unexpected error")
        return JSONResponse(
            {
                "what_happened": "Something unexpected went wrong in the app.",
                "what_to_do": "Try again. If it keeps happening, use Help → Copy diagnostics.",
            },
            status_code=500,
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        return page.replace("{{SESSION_TOKEN}}", ctx.token)

    app.mount("/static", FreshStatic(directory=STATIC), name="static")
    return app
