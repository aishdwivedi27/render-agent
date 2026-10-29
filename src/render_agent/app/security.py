"""Localhost-only safety: Host/Origin check, per-session token, files only from the job's folders."""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

SERVED_DIRS = ("output", "references", "plan")
TOKEN_HEADER = "X-Session-Token"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _host(value: str) -> str:
    return (urlsplit(f"//{value}").hostname or "").lower()


class LocalOnly(BaseHTTPMiddleware):
    """Refuses requests whose Host or Origin is not this machine (stops DNS-rebinding and other
    web pages driving the app)."""

    def __init__(self, app: object, allowed: set[str]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.allowed = allowed

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = _host(request.headers.get("host", ""))
        origin = request.headers.get("origin")
        origin_host = (urlsplit(origin).hostname or "").lower() if origin and origin != "null" else None
        if host not in self.allowed or (origin_host is not None and origin_host not in self.allowed):
            return JSONResponse(
                {"what_happened": "Request refused.", "what_to_do": "Use the Render Agent window."},
                status_code=403,
            )
        return await call_next(request)


def require_token(request: Request) -> None:
    sent = request.headers.get(TOKEN_HEADER, "")
    if not secrets.compare_digest(sent, request.app.state.ctx.token):
        raise HTTPException(
            status_code=403,
            detail={
                "what_happened": "This page has expired.",
                "what_to_do": "Close the Render Agent window and open it again.",
            },
        )


def job_file(job_dir: Path, rel: str) -> Path | None:
    """A file under the job's output/, references/ or plan/ folder, or None (no other path is served)."""
    if not rel or rel.replace("\\", "/").split("/")[0] not in SERVED_DIRS:
        return None
    path = (job_dir / rel).resolve()
    allowed = [(job_dir / d).resolve() for d in SERVED_DIRS]
    if not any(path.is_relative_to(a) for a in allowed) or not path.is_file():
        return None
    return path
