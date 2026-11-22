"""Acceptance tests for issue #490 (review of #489, D137): defence in depth on the terminal path.

Contract this exam fixes:

* A terminal upstream never redirects. When the lab answers the WebSocket upgrade with a 3xx the
  proxy refuses (502 `terminal_unreachable` on the denial response, or close 1008 where the
  server cannot send a denial, the same way an unreachable lab is reported) and never opens a
  connection to the redirect target.
* The deploy seam check in `labs/deploy/deploy.sh` catches `LF_TERM_UPSTREAM=` in `.env` with
  optional leading blanks and an optional `export ` prefix. A commented line or another variable
  that merely contains the name is not flagged (that is also what the check does today).

The proxy runs in a small app with the ownership check replaced (the #8 exam covers it); the
stubs are local sockets. Session ids and values are fake.
"""
# ruff: noqa: F401, F811  (imported fixtures are used by name)
import asyncio
import socket
import threading
import time
import uuid

import pytest
from test_issue_8_term_proxy import fresh_ip
from test_issue_13_deploy import _ok, _stdin, server
from test_issue_76_term_proxy_limits import open_ws

SEAM = "LF_TERM_UPSTREAM"
SENTINEL = "http://sentinel-term-upstream.invalid:7681"


class Student:
    kind = "student"
    subject = "fake-subject"


class CountingListener:
    """The redirect target: a plain TCP listener that only counts connections."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections += 1
            conn.close()

    def stop(self) -> None:
        self._stop = True
        self._thread.join(timeout=5)
        self.sock.close()


class RedirectingLab:
    """A lab whose WebSocket upgrade is answered with a redirect to `location`.

    `location` may use `{port}` (this lab). The path `/elsewhere` is the redirect target on this
    same lab: an upgrade there is served normally and counted in `followed`."""

    def __init__(self, location: str, status: int = 302) -> None:
        self.location = location
        self.status = status
        self.upgrades = 0
        self.followed = 0
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Future | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(15), "the stub lab did not start"

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set_result, None)
        self._thread.join(timeout=10)

    def _run(self) -> None:
        from websockets.asyncio.server import serve
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        def redirect(_connection, request):
            if request.path.endswith("/elsewhere"):
                self.followed += 1
                return None
            self.upgrades += 1
            where = self.location.format(port=self.port)
            headers = Headers([("Location", where), ("Content-Length", "0")])
            return Response(self.status, "Redirect", headers, b"")

        async def handler(ws) -> None:
            async for message in ws:
                await ws.send(message)

        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = self._loop.create_future()
            async with serve(
                handler, "127.0.0.1", 0, process_request=redirect, subprotocols=["tty"]
            ) as srv:
                self.port = srv.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop

        asyncio.run(main())


@pytest.fixture
def tp():
    from app import term_proxy

    return term_proxy


@pytest.fixture
def client(tp, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import auth

    async def principal(_conn):
        return Student()

    async def authorize(_conn, raw_session, _principal):
        uuid.UUID(raw_session)
        return {}

    monkeypatch.setattr(auth, "current_principal", principal)
    monkeypatch.setattr(tp, "authorize", authorize)
    app = FastAPI()
    app.include_router(tp.router)
    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        yield c


def _outcome(client) -> str:
    """'accepted', 'denied:<status>:<body>' or 'closed:<code>' for one terminal upgrade."""
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    try:
        with open_ws(client, str(uuid.uuid4())):
            return "accepted"
    except WebSocketDenialResponse as denial:
        return f"denied:{denial.status_code}:{denial.text}"
    except WebSocketDisconnect as exc:
        return f"closed:{exc.code}"


SAME_LAB = [
    pytest.param("/elsewhere", id="relative-location"),
    pytest.param("ws://127.0.0.1:{port}/elsewhere", id="absolute-same-origin-location"),
]


@pytest.mark.parametrize("status", [301, 302, 307])
@pytest.mark.parametrize("location", SAME_LAB)
def test_a_redirect_from_the_lab_on_the_upgrade_is_refused_as_terminal_unreachable(
    client, monkeypatch, status, location
):
    lab = RedirectingLab(location, status)
    lab.start()
    try:
        monkeypatch.setenv(SEAM, f"127.0.0.1:{lab.port}")
        outcome = _outcome(client)
    finally:
        lab.stop()
    assert lab.upgrades >= 1, "the proxy never contacted the stub lab"
    assert outcome != "accepted", "a redirected upgrade was accepted"
    if outcome.startswith("denied"):
        assert outcome.startswith("denied:502:"), outcome
        assert "terminal_unreachable" in outcome, outcome
    else:
        assert outcome == "closed:1008", outcome


@pytest.mark.parametrize("location", SAME_LAB)
def test_the_redirect_target_on_the_lab_never_receives_an_upgrade(client, monkeypatch, location):
    lab = RedirectingLab(location)
    lab.start()
    try:
        monkeypatch.setenv(SEAM, f"127.0.0.1:{lab.port}")
        _outcome(client)
        time.sleep(0.3)
    finally:
        lab.stop()
    assert lab.followed == 0, f"the proxy followed the lab redirect ({lab.followed} upgrades)"


def test_a_redirect_to_another_host_is_refused_and_that_host_never_receives_a_connection(
    client, monkeypatch
):
    target = CountingListener()
    lab = RedirectingLab(f"ws://127.0.0.1:{target.port}/s/x/term/ws")
    lab.start()
    try:
        monkeypatch.setenv(SEAM, f"127.0.0.1:{lab.port}")
        outcome = _outcome(client)
        time.sleep(0.3)
        connections = target.connections
    finally:
        lab.stop()
        target.stop()
    assert outcome != "accepted", "a redirected upgrade was accepted"
    assert connections == 0, f"the proxy followed the lab redirect ({connections} connection)"


# ---------------------------------------------------------------- deploy seam spellings

FLAGGED = [
    pytest.param(f"export {SEAM}=x", id="export"),
    pytest.param(f" {SEAM}=x", id="leading-space"),
    pytest.param(f"  {SEAM}=x", id="leading-spaces"),
    pytest.param(f"\t{SEAM}=x", id="leading-tab"),
    pytest.param(f"\texport {SEAM}=x", id="tab-export"),
    pytest.param(f"  export {SEAM}=x", id="spaces-export"),
    pytest.param(f"{SEAM}=x", id="plain"),
]

NOT_FLAGGED = [
    pytest.param(f"# {SEAM}=x", id="comment"),
    pytest.param(f"#{SEAM}=x", id="comment-tight"),
    pytest.param(f"X{SEAM}=x", id="longer-name-prefix"),
    pytest.param(f"{SEAM}_EXTRA=x", id="longer-name-suffix"),
]


@pytest.mark.parametrize("line", FLAGGED)
def test_deploy_seam_check_catches_every_spelling(server, line):
    server.env_file.write_text(line.replace("=x", f"={SENTINEL}") + "\n")
    r = server.run(_stdin())
    assert r.returncode != 0, f"{line!r} got past the seam check"
    assert SEAM in r.stdout + r.stderr
    assert "sentinel-term-upstream" not in r.stdout + r.stderr
    assert not server.compose_up(), "the deploy went on to start containers"


@pytest.mark.parametrize("line", NOT_FLAGGED)
def test_deploy_seam_check_ignores_comments_and_other_names(server, line):
    server.env_file.write_text(line + "\n")
    _ok(server.run(_stdin()))
