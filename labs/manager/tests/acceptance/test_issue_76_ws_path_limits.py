"""Acceptance tests for issue #76, round 2: the WebSocket path of the terminal proxy (D137,
amendment from review round 1 of #489).

Contract this exam fixes:

* The WebSocket path looks the lab name up through the same bounded, private pool as the HTTP
  path (`app.term_proxy.RESOLVE_TIMEOUT`, lowered here). With a hung resolver many upgrades
  fail fast, and the event loop's default executor (argon2 logins) stays usable.
* Connecting by IP keeps the lab's Host header: the lab sees `Host: <name>:<port>`.
* If `accept` fails (the client is gone), the upstream lab connection is closed and the
  per-session slot is free again.
* uvicorn in `labs/manager/Dockerfile` runs with `--ws-max-size 1048576`.

The proxy runs in a small app holding only the terminal router, with the ownership check
replaced (the #8 exam covers it). The lab is a tiny WebSocket server that counts open
connections and records the Host header. Session ids and addresses are fake.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import asyncio
import re
import socket
import threading
import time
import uuid
from pathlib import Path

import pytest
from test_issue_8_term_proxy import fresh_ip
from test_issue_76_term_proxy_limits import Pump, echoes, open_ws

SEAM = "LF_TERM_UPSTREAM"
DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


class Student:
    kind = "student"
    subject = "fake-subject"


class CountingLab:
    """Echo WebSocket server in its own thread; knows how many connections are open."""

    def __init__(self) -> None:
        self.open = 0
        self.hosts: list[str] = []
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

        async def handler(ws) -> None:
            self.open += 1
            self.hosts.append(ws.request.headers.get("Host", ""))
            try:
                async for message in ws:
                    await ws.send(message)
            finally:
                self.open -= 1

        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = self._loop.create_future()
            async with serve(handler, "127.0.0.1", 0, subprotocols=["tty"]) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop

        asyncio.run(main())


@pytest.fixture
def lab():
    lab = CountingLab()
    lab.start()
    yield lab
    lab.stop()


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


def _wait(predicate, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


# ---------------------------------------------------------------- lookup on the WebSocket path


def test_websocket_upgrades_fail_fast_and_leave_the_default_pool_free_when_lookups_hang(
    client, tp, monkeypatch
):
    monkeypatch.delenv(SEAM, raising=False)
    monkeypatch.setattr(tp, "RESOLVE_TIMEOUT", 0.3, raising=False)
    release = threading.Event()
    real = socket.getaddrinfo

    def hung(host, port, *args, **kwargs):
        if isinstance(host, str) and host.startswith("lab-"):
            release.wait(20)
            raise socket.gaierror(socket.EAI_NONAME, "hung resolver (exam)")
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", hung)
    durations: list[float] = []

    def attempt() -> None:
        started = time.time()
        try:
            with open_ws(client, str(uuid.uuid4())) as ws:
                Pump(ws).frame(8)
        except Exception:  # noqa: BLE001  (refused or closed: both are fine)
            pass
        durations.append(time.time() - started)

    async def default_pool_runs() -> str:
        try:
            return await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), 1.5)
        except TimeoutError:
            return "starved"

    try:
        threads = [threading.Thread(target=attempt, daemon=True) for _ in range(48)]
        started = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        total = time.time() - started
        outcome = client.portal.call(default_pool_runs)
    finally:
        release.set()
    assert len(durations) == 48, "some upgrades never finished"
    assert total < 4, f"48 upgrades took {total:.1f} s with a hung lookup: the path is not bounded"
    assert outcome == "ok", "hung WebSocket lookups held every thread of the default executor"


# ---------------------------------------------------------------- Host header


def test_the_lab_sees_the_name_and_port_as_host_not_the_ip(client, tp, lab, monkeypatch):
    monkeypatch.delenv(SEAM, raising=False)
    monkeypatch.setattr(tp, "TTYD_PORT", lab.port)
    real = socket.getaddrinfo
    sid = str(uuid.uuid4())

    def to_lab(host, port, *args, **kwargs):
        if isinstance(host, str) and host.startswith("lab-"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", to_lab)
    with open_ws(client, sid) as ws:
        assert echoes(ws), "the terminal did not connect through the name lookup"
    assert lab.hosts, "the lab was never contacted"
    assert lab.hosts[-1] == f"lab-{sid}:{lab.port}", lab.hosts[-1]


# ---------------------------------------------------------------- failed accept


def test_a_failed_accept_closes_the_upstream_and_frees_the_slot(client, lab, monkeypatch):
    from starlette.websockets import WebSocket

    monkeypatch.setenv(SEAM, f"127.0.0.1:{lab.port}")
    sid = str(uuid.uuid4())
    real_accept = WebSocket.accept
    broken = {"on": True}

    async def accept(self, *args, **kwargs):
        if broken["on"]:
            raise RuntimeError("the client is gone (exam)")
        return await real_accept(self, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "accept", accept)
    for _ in range(6):  # more than the four slots of a session
        try:
            with open_ws(client, sid) as ws:
                Pump(ws).frame(3)
        except Exception:  # noqa: BLE001  (the failure surfaces in the client: expected)
            pass
    assert _wait(lambda: lab.open == 0, 5), (
        f"{lab.open} lab connection(s) still open after 6 failed accepts: the upstream is leaked"
    )
    broken["on"] = False
    opened = []
    try:
        for _ in range(4):
            ws = open_ws(client, sid)
            opened.append(ws)
            assert echoes(ws.__enter__()), "a slot of the session was still taken by a failed accept"
    finally:
        for ws in reversed(opened):
            ws.__exit__(None, None, None)


# ---------------------------------------------------------------- client-side frame limit


def test_uvicorn_in_the_image_limits_client_frames_to_one_mib():
    text = DOCKERFILE.read_text(encoding="utf-8")
    cmd = re.search(r"^CMD\s*(\[.*\])\s*$", text, re.M)
    assert cmd, "labs/manager/Dockerfile has no CMD line"
    line = cmd.group(1)
    assert "uvicorn" in line
    pattern = r"--ws-max-size=1048576|--ws-max-size\W+1048576\b"
    assert re.search(pattern, line), f"the uvicorn command lacks --ws-max-size 1048576: {line}"
