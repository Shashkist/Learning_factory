"""Acceptance tests for issue #399 (review leftovers of #373 and #392; D117, D119, D120).

Contract this exam fixes, on top of the #373 and #392 exams:

* The terminal smoke passes only when the foreign-Origin upgrade is answered with exactly 403.
  Any other refusal status (500, 401) fails it, with one reason line, and the session is ended.
* When the server refuses the smoke's own Origin (403 on the first upgrade), the failure line
  names `LF_PUBLIC_ORIGIN`, the setting the owner has to fix.
* The startup check for `LF_PUBLIC_ORIGIN` warns when the value is not exactly
  `scheme://hostname[:port]` apart from a trailing-slash run and case: `https://host:`,
  `https://host/?` and `https://host#` warn, `https://host//` and the plain value do not.
* `web()` decides the framing headers on the media type of the response (text/html or
  application/xhtml+xml), not on a suffix list.

The fake manager is the #373 exam's; ids and passwords are fake. No Docker.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import importlib
import logging
import mimetypes
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from test_issue_69_ws_origin_check import (  # noqa: F401, F811
    PUBLIC,
    _origin_warnings,
    listener,
    make_client,
)
from test_issue_86_terminal_client import (  # noqa: F401, F811
    Fake,
    _build_app,
    assert_clean,
    reason_line,
    run_client,
)
from test_issue_374_term_frame_ancestors import assert_framing_headers, frame_ancestors_sources

# ---------------------------------------------------------------- smoke: fake manager


class StatusPolicy:
    """The own Origin is accepted; any other Origin is answered with `foreign_status`."""

    def __init__(self) -> None:
        self.base = ""
        self.own_status: int | None = None  # None = accept the own origin
        self.foreign_status = 403
        self.origins: list[str | None] = []

    def reset(self, foreign_status: int, own_status: int | None = None) -> None:
        self.foreign_status = foreign_status
        self.own_status = own_status
        self.origins = []


def _with_status_policy(app, policy: StatusPolicy):
    async def wrapped(scope, receive, send):
        if scope["type"] == "websocket":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            origin = headers.get("origin")
            policy.origins.append(origin)
            own = origin is not None and origin.lower().rstrip("/") == policy.base.lower()
            status = policy.own_status if own else policy.foreign_status
            if status is not None:
                await receive()  # websocket.connect
                if status == 403:
                    await send({"type": "websocket.close", "code": 1008})  # uvicorn: HTTP 403
                else:
                    await send(
                        {
                            "type": "websocket.http.response.start",
                            "status": status,
                            "headers": [(b"content-length", b"0")],
                        }
                    )
                    await send({"type": "websocket.http.response.body", "body": b""})
                return
        await app(scope, receive, send)

    return wrapped


@pytest.fixture(scope="module")
def status_server():
    import uvicorn

    fake = Fake()
    policy = StatusPolicy()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    policy.base = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(
        _with_status_policy(_build_app(fake), policy),
        host="127.0.0.1",
        port=port,
        log_level="error",
        ws="auto",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline, "the fake manager did not start"
        time.sleep(0.05)
    yield fake, policy, policy.base
    server.should_exit = True
    thread.join(timeout=10)


# ---------------------------------------------------------------- AC1: exactly 403


def test_the_smoke_passes_when_the_foreign_origin_is_answered_with_403(status_server):
    fake, policy, base = status_server
    fake.reset("good")
    policy.reset(403)
    res = run_client(base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert len(policy.origins) == 2, policy.origins
    assert_clean(res, fake)


@pytest.mark.parametrize("status", [500, 401])
def test_the_smoke_fails_when_the_foreign_origin_is_refused_with_a_status_other_than_403(
    status_server, status
):
    fake, policy, base = status_server
    fake.reset("good")
    policy.reset(status)
    res = run_client(base, timeout="4")
    assert len(policy.origins) == 2, f"the foreign-origin upgrade was not tried: {policy.origins}"
    assert res.returncode != 0, f"HTTP {status} on a foreign Origin passed the smoke"
    assert ("end", fake.sid, True) in fake.events, "the session must still be ended"
    assert_clean(res, fake)
    assert str(status) in reason_line(res), "the reason line should name the status it got"


# ---------------------------------------------------------------- AC4: same probe semantics


def test_the_foreign_origin_probe_still_sends_a_foreign_origin_once(status_server):
    fake, policy, base = status_server
    fake.reset("good")
    policy.reset(403)
    res = run_client(base)
    assert res.returncode == 0, res.stdout + res.stderr
    foreign = [o for o in policy.origins if (o or "").lower().rstrip("/") != base.lower()]
    assert len(foreign) == 1 and foreign[0].startswith(("http://", "https://")), policy.origins
    assert ("end", fake.sid, True) in fake.events


# ---------------------------------------------------------------- AC5: name LF_PUBLIC_ORIGIN


def test_a_refused_own_origin_names_lf_public_origin_in_the_failure(status_server):
    fake, policy, base = status_server
    fake.reset("good")
    policy.reset(403, own_status=403)  # the server refuses the smoke's own Origin
    res = run_client(base, timeout="4")
    assert res.returncode != 0, res.stdout + res.stderr
    assert ("end", fake.sid, True) in fake.events, "the session must still be ended"
    assert_clean(res, fake)
    assert "LF_PUBLIC_ORIGIN" in reason_line(res), res.stderr


# ---------------------------------------------------------------- AC3: rebuilt origin


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("https://labs.shashkist.com:", id="empty-port"),
        pytest.param("https://labs.shashkist.com/?", id="empty-query"),
        pytest.param("https://labs.shashkist.com#", id="empty-fragment"),
    ],
)
def test_an_origin_with_an_empty_port_query_or_fragment_logs_one_startup_warning(
    make_client, caplog, value
):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        warnings = _origin_warnings(caplog)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "not set" not in warnings[0]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("https://labs.shashkist.com//", id="double-trailing-slash"),
        pytest.param(PUBLIC, id="plain"),
        pytest.param("HTTPS://Labs.Shashkist.com", id="upper-case"),
        pytest.param("https://labs.shashkist.com:8443/", id="port-and-slash"),
    ],
)
def test_a_trailing_slash_run_or_case_difference_logs_no_startup_warning(
    make_client, caplog, value
):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        assert _origin_warnings(caplog) == []


@pytest.mark.parametrize(
    "value",
    [
        "https://labs.shashkist.com/app",
        "https://labs.shashkist.com:443",
        "https://user:pw@labs.shashkist.com",
        "labs.shashkist.com",
    ],
)
def test_the_existing_malformed_cases_still_warn_once(make_client, caplog, value):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        assert len(_origin_warnings(caplog)) == 1


# ---------------------------------------------------------------- AC6: framing by media type

PAGE = "<!doctype html><html><body><p>lf-fake-page-399</p></body></html>"


@pytest.fixture
def web_app(monkeypatch, tmp_path: Path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(PAGE, encoding="utf-8")
    for name in ("page.xhtml", "page.shtml", "UPPER.XHTML"):
        (web / name).write_text(PAGE, encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1);", encoding="utf-8")
    (web / "assets" / "style.css").write_text("p{color:red}", encoding="utf-8")
    # a mime map that serves `.shtml` as HTML, as some systems' maps do
    monkeypatch.setitem(mimetypes.types_map, ".shtml", "text/html")
    monkeypatch.setenv("LF_WEB_DIR", str(web))
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    sys.modules.pop("app.main", None)
    app = importlib.import_module("app.main").app
    yield app
    sys.modules.pop("app.main", None)


@pytest.mark.parametrize("path", ["/page.xhtml", "/page.shtml", "/UPPER.XHTML"])
def test_a_file_served_as_html_by_media_type_carries_the_framing_headers(web_app, path):
    from fastapi.testclient import TestClient

    with TestClient(web_app) as c:
        resp = c.get(path)
    assert resp.status_code == 200, (path, resp.status_code)
    ctype = resp.headers["content-type"]
    assert ctype.startswith(("text/html", "application/xhtml+xml")), ctype
    assert_framing_headers(resp, f"{path} ({ctype})")


@pytest.mark.parametrize("path", ["/assets/app.js", "/assets/style.css"])
def test_a_non_html_media_type_is_served_without_a_frame_ancestors_policy(web_app, path):
    from fastapi.testclient import TestClient

    with TestClient(web_app) as c:
        resp = c.get(path)
    assert resp.status_code == 200
    assert not frame_ancestors_sources(resp)
