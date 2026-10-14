"""The 127.0.0.1 review page, served for real on a free port with the mock provider."""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from render_agent.images import MockProvider
from render_agent.pipeline.job import load_job
from render_agent.pipeline.runner import RunContext, run_job
from render_agent.pipeline.stages import s9_review
from render_agent.review.server import ReviewApp, make_server
from tests.integration.test_m3_rendering import CheckLLM, _approved, _go


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


@pytest.fixture
def page(install_settings):
    job = _approved(install_settings)
    _go(install_settings, job)

    def continue_job():
        extras = {"llm": CheckLLM(), "image_provider": MockProvider()}
        return run_job(RunContext(load_job(job.dir), install_settings, extras=extras)).status

    app = ReviewApp(job.dir, continue_job)
    server = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield job, app, base
    server.shutdown()
    server.server_close()


def _get(url):
    try:
        with OPENER.open(url) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(url, **form):
    data = urllib.parse.urlencode(form).encode()
    try:
        with OPENER.open(urllib.request.Request(url, data=data, method="POST")) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_page_lists_renders_and_records_decisions(page):
    job, app, base = page
    status, body = _get(base + "/")
    text = body.decode()
    assert status == 200 and "P1-SHARED-KIT" in text and "DWELLING 2" in text
    img = re.search(r"src='(/file/output/P1-SHARED-KIT_v1\.png)'", text).group(1)
    assert _get(base + img)[1].startswith(b"\x89PNG")
    assert "/file/references/" in text  # reference crop beside the render

    assert _post(base + "/decide", token=app.token, render_id="P1-SHARED-STREET", action="approve") == 303
    assert (
        _post(
            base + "/decide",
            token=app.token,
            render_id="P1-SHARED-KIT",
            action="flag",
            note="<b>darker</b> bench",
        )
        == 303
    )
    review = s9_review.load_review(job.dir)
    assert review["renders"]["P1-SHARED-STREET"]["status"] == "approved"
    assert review["renders"]["P1-SHARED-KIT"]["note"] == "<b>darker</b> bench"
    assert "&lt;b&gt;darker" in _get(base + "/")[1].decode()  # notes are escaped on the page

    # a locked render cannot be flagged; the page says why
    _post(base + "/decide", token=app.token, render_id="P1-SHARED-STREET", action="flag", note="x")
    assert "already approved and locked" in _get(base + "/")[1].decode()


def test_page_refuses_bad_token_and_other_files(page):
    job, app, base = page
    assert _post(base + "/decide", token="wrong", render_id="P1-SHARED-KIT", action="approve") == 403
    assert s9_review.load_review(job.dir)["renders"]["P1-SHARED-KIT"]["status"] == "pending"
    for bad in ("/file/state.json", "/file/output/../state.json", "/file/output/..%2F..%2Fconfig.yaml", "/x"):
        assert _get(base + bad)[0] == 404, bad
    with pytest.raises(ValueError):
        make_server(app, "0.0.0.0", 0)


def test_continue_regenerates_and_delivers(page):
    job, app, base = page
    review = s9_review.load_review(job.dir)
    for rid in review["renders"]:
        s9_review.decide(review, rid, "approve")
    s9_review.save_review(job.dir, review)
    assert _post(base + "/continue", token=app.token) == 303
    for _ in range(100):
        if not app.busy:
            break
        time.sleep(0.05)
    assert app.message == "SUCCEEDED"
    assert (job.dir / "manifest.json").is_file() and (job.dir / "output" / "Common").is_dir()


def test_large_images_are_served_completely(tmp_path):
    """A 3 MB render must arrive whole (one big send used to stall on Windows)."""
    import os

    (tmp_path / "output").mkdir()
    data = b"\x89PNG" + os.urandom(3_000_000)
    (tmp_path / "output" / "big_v1.png").write_bytes(data)
    server = make_server(ReviewApp(tmp_path, lambda: ""), "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/file/output/big_v1.png"
        for _ in range(3):
            with urllib.request.urlopen(url, timeout=20) as r:
                assert r.read() == data
    finally:
        server.shutdown()
        server.server_close()
