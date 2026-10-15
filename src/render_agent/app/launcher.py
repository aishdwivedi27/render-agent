"""Opens the app: single-instance lock, a free 127.0.0.1 port, uvicorn in a thread, then a native
window (pywebview on Edge WebView2). A second launch brings the open window to the front and exits.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

from render_agent.app.main import create_app, make_context
from render_agent.app.security import new_token
from render_agent.config import Settings
from render_agent.pipeline.runner import JobBusy, job_lock

log = logging.getLogger("render_agent.launcher")
PORT_FILE = "app_port.json"
TITLE = "Render Agent"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def message_box(text: str) -> None:
    """A plain message before any window exists (no terminal for designers)."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x40)  # type: ignore[attr-defined]
    else:
        print(text)


def _focus_existing(work_root: Path) -> bool:
    try:
        port = json.loads((work_root / PORT_FILE).read_text(encoding="utf-8"))["port"]
        urllib.request.urlopen(f"http://127.0.0.1:{port}/focus", data=b"", timeout=3)  # noqa: S310
        return True
    except Exception:
        return False


def run(settings: Settings, browser: bool = False) -> int:
    lock_dir = settings.work_root / "app_lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    try:
        with job_lock(lock_dir):
            return _run(settings, browser)
    except JobBusy:
        if not _focus_existing(settings.work_root):
            message_box("Render Agent is already open. Look for its window on the taskbar.")
        return 0


def _run(settings: Settings, browser: bool) -> int:
    import uvicorn

    try:
        port = free_port()
    except OSError:
        message_box(
            "Render Agent could not open a local connection on this computer. Restart Windows and try again."
        )
        return 1
    ctx = make_context(settings, new_token())
    app = create_app(ctx)
    windows: list[object] = []

    @app.post("/focus")
    def focus() -> dict[str, bool]:  # no token: it only brings the window forward
        for w in windows:
            try:
                w.restore()  # type: ignore[attr-defined]
                w.show()  # type: ignore[attr-defined]
            except Exception:
                pass
        return {"ok": True}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="app-server", daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    (settings.work_root / PORT_FILE).write_text(json.dumps({"port": port}), encoding="utf-8")
    ctx.worker.start()  # resumes queued and running jobs from state.json
    url = f"http://127.0.0.1:{port}/"
    log.info("app on %s", url)
    try:
        if browser:
            import webbrowser

            webbrowser.open(url)
            print(f"Render Agent on {url} (Ctrl+C to stop)")
            while thread.is_alive():
                time.sleep(0.5)
        else:
            try:
                import webview
            except ImportError:
                message_box("The window component is missing. Reinstall Render Agent.")
                return 1
            windows.append(webview.create_window(TITLE, url, width=1280, height=860, min_size=(900, 640)))
            try:
                webview.start(gui="edgechromium" if sys.platform == "win32" else None)
            except Exception:
                log.exception("window failed to open")
                message_box(
                    "Render Agent needs the Microsoft Edge WebView2 Runtime. Install it from "
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703 and open Render Agent again."
                )
                return 1
    except KeyboardInterrupt:
        pass
    finally:
        ctx.worker.stop()
        server.should_exit = True
        (settings.work_root / PORT_FILE).unlink(missing_ok=True)
    return 0
