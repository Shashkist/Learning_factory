"""Acceptance tests for issue #385 (review of #374): framing headers on the 500 path, on a
comma-separated upstream CSP, and on the UI pages.

Contract this exam fixes:

* AC1: a 500 caused by an unhandled exception under `/s/<sid>/term*` carries the framing headers
  (`frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`). The exception is still raised to the
  server (so it is logged), the headers are only added. Probe: a route inserted at the front of the
  real app's router under the prefix that raises before any response starts. The real app is used
  (not a hand-built one) so the exam also covers how `app.main` stacks its middleware around
  Starlette's 500 handler, and the probe does not depend on how `FrameGuard` is written. One
  database case tries the real proxy with a 70 000-character path.
* AC2: through the proxy, an upstream CSP of `default-src 'self', frame-ancestors *` leaves no
  `frame-ancestors *` in any comma-separated policy of the response. The direct `merge_headers`
  unit test belongs to the implementer.
* AC3: the UI's HTML (the shell at `/`, `/index.html`, deep links such as `/lab/<id>`) carries the
  same two headers; the page still serves its content. Static assets and `/api` are not covered.

No-database tests run anywhere; the proxy cases need LF_TEST_DATABASE_URL (skip locally, CI has
it). It reuses the fixtures of the #8 exam. Student ids are fake.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import asyncio
import importlib
import re
import socket
import sys
import threading
import uuid
from pathlib import Path

import pytest
from test_issue_8_term_proxy import (  # noqa: F401, F811  (fixtures are used by name)
    _app_dsn,
    _db,
    _stub,
    env,
    upstream,
)
from test_issue_374_term_frame_ancestors import (
    assert_framing_headers,
    csp_values,
    frame_ancestors_sources,
)

INDEX = "<!doctype html><html><body><div id=root>lf-fake-spa-385</div></body></html>"


def asgi_get(app, path: str, raw_path: bytes | None = None, headers=None):
    """Drive the app with a raw ASGI scope, as uvicorn does: `path` is decoded, `raw_path` is not.
    No client-side limit applies. Returns (status, headers, body); an exception the app re-raises
    after sending its 500 is swallowed here (the response is what is checked)."""
    sent: list[dict] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")] + list(headers or []),
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443),
    }

    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # a client that stays connected (no busy disconnect loop)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def run():
        try:
            await app(scope, receive, send)
        except Exception:
            pass

    asyncio.run(run())
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    assert start is not None, "the app sent no response at all"
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], start["headers"], body


class _RawResp:
    """Enough of a response for assert_framing_headers: .headers.get_list / .get."""

    def __init__(self, headers, status_code=0):
        import httpx

        self.headers = httpx.Headers(headers)
        self.status_code = status_code


def comma_policy_sources(resp) -> list[list[str]]:
    """Source lists of every `frame-ancestors` directive, splitting policies on `,` first."""
    found = []
    for value in csp_values(resp):
        for policy in value.split(","):
            for directive in policy.split(";"):
                parts = directive.split()
                if parts and parts[0].lower() == "frame-ancestors":
                    found.append(parts[1:])
    return found


@pytest.fixture
def fresh_app(monkeypatch, tmp_path: Path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(INDEX, encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1);", encoding="utf-8")
    monkeypatch.setenv("LF_WEB_DIR", str(web))
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    sys.modules.pop("app.main", None)
    app = importlib.import_module("app.main").app
    yield app
    sys.modules.pop("app.main", None)


# ---------------------------------------------------------------- AC1: the 500 path (no database)


def _with_probe(app):
    from fastapi import APIRouter

    router = APIRouter()

    @router.api_route("/s/{sid}/term/boom", methods=["GET", "POST"])
    async def boom(sid: str):
        raise RuntimeError("probe failure")

    @router.api_route("/s/{sid}/term", methods=["GET"])
    async def boom_root(sid: str):
        raise RuntimeError("probe failure")

    app.router.routes[0:0] = router.routes
    return app


@pytest.mark.parametrize(
    "method,path", [("GET", "/term/boom"), ("POST", "/term/boom"), ("GET", "/term")]
)
def test_an_unhandled_exception_under_the_terminal_prefix_is_500_with_the_framing_headers(
    fresh_app, method, path
):
    from fastapi.testclient import TestClient

    app = _with_probe(fresh_app)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.request(method, f"/s/{uuid.uuid4()}{path}")
    assert resp.status_code == 500, (resp.status_code, resp.text[:200])
    assert_framing_headers(resp, f"500 on {method} {path}")


def test_the_exception_still_reaches_the_server_after_the_500_is_framed(fresh_app):
    from fastapi.testclient import TestClient

    app = _with_probe(fresh_app)
    with TestClient(app, raise_server_exceptions=True) as c:
        with pytest.raises(RuntimeError, match="probe failure"):
            c.get(f"/s/{uuid.uuid4()}/term/boom")


def test_a_500_outside_the_terminal_prefix_is_not_given_the_framing_headers(fresh_app):
    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    router = APIRouter()

    @router.get("/api/_probe385")
    async def boom():
        raise RuntimeError("probe failure")

    fresh_app.router.routes[0:0] = router.routes
    with TestClient(fresh_app, raise_server_exceptions=False) as c:
        resp = c.get("/api/_probe385")
    assert resp.status_code == 500
    assert not frame_ancestors_sources(resp)
    assert not resp.headers.get_list("x-frame-options")


# ---------------------------------------------------------------- AC3: the UI pages (no database)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/lab/some-lab",
        "/lab/a/b/c",
        "/labs/some-lab",
        "/admin",
        "/teacher/dashboard",
        "/login",
    ],
)
def test_the_ui_pages_carry_the_framing_headers(fresh_app, path):
    from fastapi.testclient import TestClient

    with TestClient(fresh_app) as c:
        resp = c.get(path)
    assert resp.status_code == 200, (path, resp.status_code)
    assert "lf-fake-spa-385" in resp.text, "the page still serves the UI shell"
    assert "text/html" in resp.headers["content-type"]
    assert_framing_headers(resp, f"UI page {path}")


@pytest.mark.parametrize(
    "decoded,raw",
    [
        ("/labs/../index.html", b"/labs/..%2Findex.html"),
        ("/assets/../index.html", b"/assets/..%2Findex.html"),
        ("/labs/x/../../index.html", b"/labs/x/..%2F..%2Findex.html"),
    ],
)
def test_the_shell_reached_by_an_encoded_dot_dot_path_carries_the_framing_headers(
    fresh_app, decoded, raw
):
    """uvicorn decodes %2F, so the app sees `/labs/../index.html`, which resolves to the shell
    file. Either that is a 404, or the shell comes with the framing headers."""
    status, headers, body = asgi_get(fresh_app, decoded, raw)
    if status == 404:
        return
    resp = _RawResp(headers, status)
    if b"lf-fake-spa-385" in body:
        assert_framing_headers(resp, f"shell served for {raw!r} (status {status})")
    else:
        assert status in (400, 403, 404), (status, body[:100])


def test_head_on_the_ui_shell_carries_the_framing_headers(fresh_app):
    from fastapi.testclient import TestClient

    with TestClient(fresh_app) as c:
        resp = c.head("/")
    assert resp.status_code == 200
    assert_framing_headers(resp, "HEAD /")


def test_the_ui_shell_keeps_its_no_cache_header(fresh_app):
    from fastapi.testclient import TestClient

    with TestClient(fresh_app) as c:
        resp = c.get("/lab/x")
    assert resp.headers.get("cache-control") == "no-cache"


# ------------------------------------------------------- AC1 and AC2 through the proxy (database)


class CommaCspUpstream:
    """A raw-socket HTTP server standing in for a ttyd whose CSP lists a second policy."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.address = f"127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                body = b"comma-ttyd"
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    b"Content-Security-Policy: default-src 'self', frame-ancestors *\r\n"
                    b"X-Stub: comma\r\nConnection: close\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
            except OSError:
                pass
            finally:
                conn.close()

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def comma_upstream(monkeypatch):
    srv = CommaCspUpstream()
    monkeypatch.setenv("LF_TERM_UPSTREAM", srv.address)
    yield srv
    srv.close()


def test_a_frame_ancestors_after_a_comma_in_the_upstream_csp_is_not_passed_on(env, comma_upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/")
    assert resp.status_code == 200
    assert resp.headers.get("x-stub") == "comma", "the response came from the lab"
    assert_framing_headers(resp, "comma CSP lab")
    sources = comma_policy_sources(resp)
    assert sources and all(s == ["'self'"] for s in sources), sources
    assert not re.search(r"frame-ancestors\s+\*", " ".join(csp_values(resp)))


def test_an_unhandled_error_in_the_proxy_is_500_with_the_framing_headers(env, upstream):
    """A ~70 000-character path makes the proxy's HTTP client raise `InvalidURL`. The app is driven
    with a raw ASGI scope (httpx would refuse such a URL on the client side). If the proxy ever
    handles it itself, the answer must still carry the headers."""
    from fastapi.testclient import TestClient

    from app.main import app

    client, sid, _ = env.owner_with_session()
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items()).encode()
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False):
        status, headers, _ = asgi_get(
            app, f"/s/{sid}/term/{'a' * 70000}", headers=[(b"cookie", cookie)]
        )
    assert status >= 400, status
    assert_framing_headers(_RawResp(headers, status), f"{status} for a very long path")
