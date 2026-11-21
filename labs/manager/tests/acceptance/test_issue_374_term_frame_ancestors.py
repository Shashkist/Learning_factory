"""Acceptance tests for issue #374 (review of #69): another site cannot frame a live terminal.

Contract this exam fixes:

* Every HTTP response of `/s/<sid>/term` and `/s/<sid>/term/<anything>` carries
  `Content-Security-Policy` with the directive `frame-ancestors 'self'` and
  `X-Frame-Options: SAMEORIGIN`. The manager sets them (the exam talks to the app directly, no
  nginx in between).
* Which responses count. The proxied page (200, an upstream 418, any method) is the one that
  matters: it is the live shell. The refusals count too (401 without a login, 403 for another
  student, 404 unknown session, 410 ended, 502 unreachable lab): they hold no shell, but a framer
  can reach all of them, and a rule that holds for "every response under the prefix" cannot be
  broken by a new error path. WebSocket upgrades are not framed, so they are not covered.
* The lab must not weaken the policy: when ttyd itself answers with `X-Frame-Options: ALLOWALL`
  and `CSP: frame-ancestors *`, the response still has exactly one `X-Frame-Options` value,
  SAMEORIGIN, and no `frame-ancestors` source other than 'self'.
* Responses outside the terminal prefix (for example `/api/...`) are not part of this contract.

No-database tests run anywhere; the rest need LF_TEST_DATABASE_URL (skip locally, CI has it).
It reuses the fixtures and stub ttyd of the #8 exam. Student ids are fake.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import re
import socket
import threading
import uuid

import pytest
from test_issue_8_term_proxy import (  # noqa: F401, F811  (fixtures are used by name)
    FakeRuntime,
    _app_dsn,
    _db,
    _stub,
    cookie_of,
    env,
    nodb_client,
    upstream,
)

PATHS = ["", "/", "/token", "/deep/path/file.js", "/ws"]


def csp_values(resp) -> list[str]:
    return resp.headers.get_list("content-security-policy")


def frame_ancestors_sources(resp) -> list[list[str]]:
    """The source list of every `frame-ancestors` directive in every CSP header."""
    found = []
    for value in csp_values(resp):
        for directive in value.split(";"):
            parts = directive.split()
            if parts and parts[0].lower() == "frame-ancestors":
                found.append(parts[1:])
    return found


def assert_framing_headers(resp, what: str) -> None:
    sources = frame_ancestors_sources(resp)
    assert sources, f"{what}: no CSP frame-ancestors (status {resp.status_code})"
    assert all(s == ["'self'"] for s in sources), f"{what}: frame-ancestors is {sources!r}"
    xfo = resp.headers.get_list("x-frame-options")
    assert [v.strip().upper() for v in xfo] == ["SAMEORIGIN"], (
        f"{what}: X-Frame-Options is {xfo!r} (status {resp.status_code})"
    )


# ---------------------------------------------------------------- a lab that tries to allow framing


class WeakUpstream:
    """A raw-socket HTTP server standing in for a ttyd that allows framing by anyone."""

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
                body = b"weak-ttyd"
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    b"X-Frame-Options: ALLOWALL\r\n"
                    b"Content-Security-Policy: frame-ancestors *\r\n"
                    b"X-Stub: weak\r\nConnection: close\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
            except OSError:
                pass
            finally:
                conn.close()

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def weak_upstream(monkeypatch):
    srv = WeakUpstream()
    monkeypatch.setenv("LF_TERM_UPSTREAM", srv.address)
    yield srv
    srv.close()


def closed_port_address() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    addr = f"127.0.0.1:{s.getsockname()[1]}"
    s.close()
    return addr


# ---------------------------------------------------------------- no database


def test_an_unauthenticated_request_gets_401_with_the_framing_headers(nodb_client, upstream):
    sid = str(uuid.uuid4())
    for path in PATHS:
        resp = nodb_client.get(f"/s/{sid}/term{path}")
        assert resp.status_code == 401, (path, resp.status_code)
        assert_framing_headers(resp, f"401 on /term{path}")
    assert upstream.untouched()


# ---------------------------------------------------------------- the proxied page (database)


@pytest.mark.parametrize("path", PATHS)
def test_the_owners_terminal_page_carries_the_framing_headers(env, upstream, path):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term{path}")
    assert resp.status_code == 200, (path, resp.status_code)
    assert resp.headers.get("x-stub") == "1", "the response came from the lab"
    assert_framing_headers(resp, f"200 on /term{path}")


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_other_methods_carry_the_framing_headers(env, upstream, method):
    client, sid, _ = env.owner_with_session()
    resp = client.request(method, f"/s/{sid}/term/token", content=b"x")
    assert_framing_headers(resp, f"{method} on /term/token")


def test_head_carries_the_framing_headers(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.head(f"/s/{sid}/term/")
    assert resp.status_code == 200
    assert_framing_headers(resp, "HEAD on /term/")


def test_an_upstream_error_status_keeps_the_framing_headers(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/teapot")
    assert resp.status_code == 418
    assert resp.headers.get("x-stub") == "1"
    assert_framing_headers(resp, "418 passed through from the lab")


def test_the_page_and_its_body_still_pass_through_unchanged(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/")
    assert resp.text == "stub-ttyd GET"
    assert resp.headers["content-type"].startswith("text/html")


def test_a_lab_that_allows_framing_cannot_weaken_the_policy(env, weak_upstream):
    client, sid, _ = env.owner_with_session()
    for path in ("/", "/token"):
        resp = client.get(f"/s/{sid}/term{path}")
        assert resp.status_code == 200
        assert resp.headers.get("x-stub") == "weak", "the response came from the lab"
        assert_framing_headers(resp, f"weak lab on /term{path}")
        assert not re.search(r"frame-ancestors\s+\*", " ".join(csp_values(resp)))


def test_an_unreachable_lab_is_502_with_the_framing_headers(env, monkeypatch):
    client, sid, _ = env.owner_with_session()
    monkeypatch.setenv("LF_TERM_UPSTREAM", closed_port_address())
    resp = client.get(f"/s/{sid}/term/")
    assert resp.status_code in (502, 503, 504)
    assert_framing_headers(resp, f"{resp.status_code} for an unreachable lab")



def test_an_invalid_cookie_gets_401_with_the_framing_headers(env, upstream):
    resp = env.new_client().get(
        f"/s/{uuid.uuid4()}/term/", headers={"cookie": "lf_session=not-a-real-token"}
    )
    assert resp.status_code == 401
    assert_framing_headers(resp, "401 with a bad cookie")


# ---------------------------------------------------------------- refusals (database)


def test_another_students_request_gets_403_with_the_framing_headers(env, upstream):
    _, sid, _ = env.owner_with_session()
    intruder, _ = env.student()
    for path in PATHS:
        resp = intruder.get(f"/s/{sid}/term{path}")
        assert resp.status_code == 403, (path, resp.status_code)
        assert_framing_headers(resp, f"403 on /term{path}")
    assert upstream.untouched()


def test_an_unknown_session_gets_404_with_the_framing_headers(env, upstream):
    client, _, _ = env.owner_with_session()
    for sid in (str(uuid.uuid4()), "not-a-uuid"):
        resp = client.get(f"/s/{sid}/term/")
        assert resp.status_code == 404, (sid, resp.status_code)
        assert_framing_headers(resp, f"404 for {sid}")
    assert upstream.untouched()


def test_an_ended_session_gets_410_with_the_framing_headers(env, upstream):
    client, sid, _ = env.owner_with_session()
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    resp = client.get(f"/s/{sid}/term/")
    assert resp.status_code == 410
    assert_framing_headers(resp, "410 for an ended session")
