"""Localhost-only review page (127.0.0.1): thumbnails by dwelling beside their reference crop and
drawing-check result; approve / flag with note; "Continue" regenerates flagged renders and delivers
approved ones (the same as `resume`). Decisions go through stage 9 (review.json).

Safety: binds to the configured host, which config.py only allows to be 127.0.0.1; serves files
only from the job's output/ and references/ folders; every POST carries a per-server token so
another web page cannot submit decisions.
"""

from __future__ import annotations

import html
import json
import logging
import secrets
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from render_agent.pipeline.stages import s9_review

log = logging.getLogger("render_agent.review")
SERVED_DIRS = ("output", "references")
CHUNK = 64 * 1024
# fixed map: mimetypes reads the Windows registry, which can stall a request
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
CSS = """
:root { --bg:#fff; --fg:#1d1d1f; --muted:#666; --line:#ddd; --card:#f6f6f7; --ok:#1e7d3c; --flag:#b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16171a; --fg:#eee; --muted:#aaa; --line:#333; --card:#202226; --ok:#6fcf8f; --flag:#ff8a80; }
}
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif; margin:0 auto;
       max-width:1200px; padding:16px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; }
.imgs { display:flex; gap:8px; } .imgs img { width:100%; height:auto; border-radius:4px; }
.imgs a:first-child { flex:3 } .imgs a:last-child { flex:2 }
.status-approved { color:var(--ok); font-weight:600 }
.status-flagged, .status-failed { color:var(--flag); font-weight:600 }
.muted { color:var(--muted); font-size:13px } textarea { width:100%; box-sizing:border-box; min-height:3em; }
button { padding:6px 12px; margin-top:6px; cursor:pointer; } form { margin:0 }
.bar { position:sticky; top:0; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); }
"""


class ReviewApp:
    """State shared by the request handlers of one review server."""

    def __init__(self, job_dir: Path, continue_job: Callable[[], str]) -> None:
        self.job_dir = job_dir.resolve()
        self.continue_job = continue_job
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.busy = False
        self.message = ""

    # --- data --------------------------------------------------------------------------------
    def cards(self) -> dict[str, list[dict[str, Any]]]:
        review = s9_review.load_review(self.job_dir)
        prompts = {}
        idx = self.job_dir / "prompts" / "index.json"
        if idx.is_file():
            prompts = {r["render_id"]: r for r in json.loads(idx.read_text(encoding="utf-8"))["renders"]}
        groups: dict[str, list[dict[str, Any]]] = {}
        for rid, e in review["renders"].items():
            p = prompts.get(rid, {})
            groups.setdefault(p.get("dwelling_id", "Other"), []).append(
                {
                    "render_id": rid,
                    **e,
                    "view_type": p.get("view_type", ""),
                    "reference": (p.get("references") or [None])[0],
                }
            )
        return dict(sorted(groups.items()))

    # --- pages -------------------------------------------------------------------------------
    def page(self) -> str:
        e = html.escape
        review = s9_review.load_review(self.job_dir)
        counts = s9_review.summary(review)
        parts = []
        for dwelling, cards in self.cards().items():
            parts.append(f"<h2>{e(dwelling)}</h2><div class='grid'>")
            for c in cards:
                parts.append(self.card(c))
            parts.append("</div>")
        busy = (
            "<p><b>Working: regenerating and delivering. This page refreshes itself.</b></p>"
            if self.busy
            else ""
        )
        refresh = '<meta http-equiv="refresh" content="5">' if self.busy else ""
        return "\n".join(
            [
                "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
                "<meta name='viewport' content='width=device-width, initial-scale=1'>",
                f"<title>Render review</title>{refresh}<style>{CSS}</style></head><body>",
                f"<div class='bar'><h1>Render review: {e(self.job_dir.name)}</h1>",
                f"<p>{e(', '.join(f'{k}: {v}' for k, v in sorted(counts.items())))}</p>",
                busy + (f"<p>{e(self.message)}</p>" if self.message else ""),
                "<form method='post' action='/continue'>"
                f"<input type='hidden' name='token' value='{self.token}'>"
                "<button>Continue: regenerate flagged, deliver approved</button></form></div>",
                *parts,
                "</body></html>",
            ]
        )

    def card(self, c: dict[str, Any]) -> str:
        e = html.escape
        rid, status, v = c["render_id"], c["status"], c.get("version")
        img = f"/file/output/{rid}_v{v}.png" if v else ""
        ref = f"/file/{c['reference']}" if c.get("reference") else ""
        imgs = "".join(
            f"<a href='{e(u)}' target='_blank'><img src='{e(u)}' alt='{e(alt)}'></a>"
            for u, alt in ((img, f"{rid} render"), (ref, "reference drawing"))
            if u
        )
        forms = ""
        hidden = (
            f"<input type='hidden' name='token' value='{self.token}'>"
            f"<input type='hidden' name='render_id' value='{e(rid)}'>"
        )
        if status == "pending":
            forms = (
                f"<form method='post' action='/decide'>{hidden}"
                "<input type='hidden' name='action' value='approve'>"
                "<button>Approve</button></form>"
            )
        if status in ("pending", "failed"):
            forms += (
                f"<form method='post' action='/decide'>{hidden}"
                "<input type='hidden' name='action' value='flag'>"
                "<textarea name='note' placeholder='What should change?' required></textarea>"
                "<button>Flag for regeneration</button></form>"
            )
        detail = c.get("note") or c.get("reason") or ""
        return (
            f"<div class='card'><div class='imgs'>{imgs or '<span class=muted>No image</span>'}</div>"
            f"<p><b>{e(rid)}</b> <span class='muted'>{e(c.get('view_type', ''))} "
            f"{('v' + str(v)) if v else ''}</span><br>"
            f"<span class='status-{e(status)}'>{e(status)}</span> &middot; "
            f"<span class='muted'>{e(c.get('check') or 'not checked')}</span></p>"
            f"{'<p class=muted>' + e(detail) + '</p>' if detail else ''}{forms}</div>"
        )

    # --- actions -----------------------------------------------------------------------------
    def decide(self, form: dict[str, str]) -> str | None:
        with self.lock:
            review = s9_review.load_review(self.job_dir)
            problem = s9_review.decide(
                review,
                form.get("render_id", ""),
                form.get("action", ""),
                form.get("note", ""),
                by="review page",
            )
            if problem is None:
                s9_review.save_review(self.job_dir, review)
            return problem

    def start_continue(self) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True

        def work() -> None:
            try:
                self.message = self.continue_job()
            except Exception as exc:  # shown on the page; the job's own error.json has the detail
                log.exception("review page: continue failed")
                self.message = f"Continue failed: {exc}"
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    def file(self, rel: str) -> Path | None:
        """A file under output/ or references/ of this job, or None (no other path is served)."""
        rel = unquote(rel)
        if not rel or rel.split("/")[0] not in SERVED_DIRS:
            return None
        path = (self.job_dir / rel).resolve()
        allowed = [(self.job_dir / d).resolve() for d in SERVED_DIRS]
        if not any(path.is_relative_to(a) for a in allowed) or not path.is_file():
            return None  # also stops "output/../state.json" from escaping the served folders
        return path


def make_handler(app: ReviewApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # with explicit Content-Length and Connection: close

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet console; log file instead
            log.info("review page: " + fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            view = memoryview(body)  # in chunks: one huge send stalled on Windows (seen with 1 MB+)
            for i in range(0, len(view), CHUNK):
                self.wfile.write(view[i : i + CHUNK])
            self.wfile.flush()
            self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            try:
                self._get()
            except Exception:  # answer instead of dropping the connection; detail goes to the log
                log.exception("review page: GET %s failed", self.path)
                self._send(
                    500, b"Something went wrong; see logs/agent.log. Reload to try again.", "text/plain"
                )

        def _get(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, app.page().encode("utf-8"))
            elif path.startswith("/file/"):
                f = app.file(path[len("/file/") :])
                if f is None:
                    self._send(404, b"Not found", "text/plain")
                else:
                    self._send(
                        200, f.read_bytes(), CONTENT_TYPES.get(f.suffix.lower(), "application/octet-stream")
                    )
            else:
                self._send(404, b"Not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._post()
            except Exception:
                log.exception("review page: POST %s failed", self.path)
                self._send(
                    500, b"Something went wrong; see logs/agent.log. Go back and try again.", "text/plain"
                )

        def _post(self) -> None:
            length = min(int(self.headers.get("Content-Length") or 0), 100_000)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            if not secrets.compare_digest(form.get("token", ""), app.token):
                self._send(403, b"Forbidden", "text/plain")
                return
            path = urlparse(self.path).path
            if path == "/decide":
                problem = app.decide(form)
                app.message = problem or ""
            elif path == "/continue":
                app.start_continue()
            else:
                self._send(404, b"Not found", "text/plain")
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

    return Handler


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets another program bind the same port; keep the port to ourselves.
    allow_reuse_address = False
    daemon_threads = True


def make_server(app: ReviewApp, host: str, port: int) -> ThreadingHTTPServer:
    if host != "127.0.0.1":  # config.py already enforces this; never serve beyond this machine
        raise ValueError("the review page only binds to 127.0.0.1")
    return _Server((host, port), make_handler(app))
