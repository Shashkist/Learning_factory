"""Acceptance tests for issue #392 (review of #390): every `.html` file the UI server serves
carries the framing headers, not only the index.

Contract: `web()` in `app.main` answers a request for any `.html` file under the web dir (for
example a copy or a hard link of the shell under another name, or a second page) with
`frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN`. Static assets that are not HTML (js,
css) are not touched. No database needed. The fake web dir only.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest
from test_issue_374_term_frame_ancestors import assert_framing_headers, frame_ancestors_sources

INDEX = "<!doctype html><html><body><div id=root>lf-fake-spa-392</div></body></html>"
OTHER = "<!doctype html><html><body><p>lf-fake-other-page-392</p></body></html>"


@pytest.fixture
def web_app(monkeypatch, tmp_path: Path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "docs").mkdir()
    (web / "index.html").write_text(INDEX, encoding="utf-8")
    (web / "other.html").write_text(OTHER, encoding="utf-8")
    (web / "docs" / "guide.html").write_text(OTHER, encoding="utf-8")
    (web / "assets" / "page.html").write_text(OTHER, encoding="utf-8")
    (web / "upper.HTML").write_text(OTHER, encoding="utf-8")
    (web / "legacy.htm").write_text(OTHER, encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1);", encoding="utf-8")
    try:  # a hard link of the shell under another name, as the issue describes
        os.link(web / "index.html", web / "copy-of-shell.html")
    except OSError:
        (web / "copy-of-shell.html").write_text(INDEX, encoding="utf-8")
    monkeypatch.setenv("LF_WEB_DIR", str(web))
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    sys.modules.pop("app.main", None)
    app = importlib.import_module("app.main").app
    yield app
    sys.modules.pop("app.main", None)


@pytest.mark.parametrize(
    "path,marker",
    [
        ("/other.html", "lf-fake-other-page-392"),
        ("/docs/guide.html", "lf-fake-other-page-392"),
        ("/assets/page.html", "lf-fake-other-page-392"),
        ("/copy-of-shell.html", "lf-fake-spa-392"),
        ("/upper.HTML", "lf-fake-other-page-392"),
        ("/legacy.htm", "lf-fake-other-page-392"),
    ],
)
def test_a_second_html_file_is_served_with_the_framing_headers(web_app, path, marker):
    from fastapi.testclient import TestClient

    with TestClient(web_app) as c:
        resp = c.get(path)
    assert resp.status_code == 200, (path, resp.status_code)
    assert marker in resp.text, "the file's own content is served"
    assert "text/html" in resp.headers["content-type"]
    assert_framing_headers(resp, f"HTML file {path}")


def test_head_on_a_second_html_file_carries_the_framing_headers(web_app):
    from fastapi.testclient import TestClient

    with TestClient(web_app) as c:
        resp = c.head("/other.html")
    assert resp.status_code == 200
    assert_framing_headers(resp, "HEAD /other.html")


def test_the_index_still_carries_the_framing_headers(web_app):
    from fastapi.testclient import TestClient

    with TestClient(web_app) as c:
        resp = c.get("/index.html")
    assert resp.status_code == 200
    assert "lf-fake-spa-392" in resp.text
    assert_framing_headers(resp, "index.html")


def test_a_non_html_static_file_is_served_without_a_frame_ancestors_policy(web_app):
    from fastapi.testclient import TestClient

    with TestClient(web_app) as c:
        resp = c.get("/assets/app.js")
    assert resp.status_code == 200
    assert "console.log(1);" in resp.text
    assert not frame_ancestors_sources(resp)
