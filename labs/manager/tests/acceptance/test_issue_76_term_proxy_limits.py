"""Acceptance tests for issue #76 (terminal proxy hardening), decided in D137.

Contract this exam fixes:

* The lab name lookup of `/s/<sid>/term` has a 5 s budget (module constant
  `app.term_proxy.RESOLVE_TIMEOUT`, seconds; the exam lowers it so it stays fast). When it
  runs out the HTTP request answers 504 with detail `terminal_timeout`. A hung lookup never holds
  a thread of the event loop's default executor (argon2 login checks use it through
  `asyncio.to_thread`): after many hung lookups timed out, `asyncio.to_thread` still runs.
* Upstream WebSocket limits: `max_size` 1 MiB (`MAX_WS_MESSAGE`) and `max_queue` 16
  (`MAX_WS_QUEUE`), passed to `websockets.asyncio.client.connect` (the name `connect` in
  `app.term_proxy`). Behaviour: a lab frame above 1 MiB closes the terminal instead of passing.
* At most 4 open terminal WebSockets per session (`MAX_TERMINALS_PER_SESSION`). A fifth is
  refused before accept with HTTP 429 and detail `too_many_terminals` (close 1008 is accepted
  where no denial response is possible) and the lab is not contacted. The slot is given back
  when a socket closes and when the upstream connect fails. Other sessions have their own 4.
* `LF_TERM_UPSTREAM` set at startup logs exactly one ERROR line that names the seam and never
  its value. Unset: no such line. (`deploy.sh` refusing it in `.env` is the #13 exam's job.)

The proxy cases run against a small app holding only the terminal router, with the ownership
check replaced (it is covered by the #8 exam), so no database is needed. The lab is the stub
ttyd of the #8 exam. Session ids and addresses are fake.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import asyncio
import json
import logging
import socket
import threading
import time
import uuid

import pytest
from test_issue_8_term_proxy import (  # noqa: F401, F811  (fixtures are used by name)
    Pump,
    _set_env,
    _stub,
    fresh_ip,
    upstream,
    write_content,
)

MIB = 1024 * 1024
SEAM = "LF_TERM_UPSTREAM"


class Student:
    kind = "student"
    subject = "fake-subject"


@pytest.fixture
def tp():
    from app import term_proxy

    return term_proxy


@pytest.fixture
def client(tp, monkeypatch):
    """The terminal router alone; every caller owns every session."""
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


def open_ws(client, sid: str):
    return client.websocket_connect(f"/s/{sid}/term/ws", subprotocols=["tty"])


def echoes(ws, payload: bytes = b"0ping", timeout: float = 10.0) -> bool:
    """True when the terminal is really connected end to end."""
    pump = Pump(ws)
    ws.send_bytes(payload)
    return pump.frame(timeout) == ("bytes", payload)


def assert_too_many(client, sid: str) -> None:
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    try:
        ws = open_ws(client, sid)
        ws.__enter__()
    except WebSocketDenialResponse as denial:
        assert denial.status_code == 429, f"denied with {denial.status_code}, want 429"
        assert "too_many_terminals" in denial.text, denial.text
        return
    except WebSocketDisconnect as exc:
        assert exc.code == 1008, f"closed with {exc.code}, want 1008"
        return
    ws.__exit__(None, None, None)
    raise AssertionError("the fifth terminal socket was accepted")


def open_when_free(client, sid: str, wait: float = 10.0):
    """Open a socket, retrying while the proxy still counts a socket that just closed."""
    deadline = time.time() + wait
    while True:
        ws = open_ws(client, sid)
        try:
            entered = ws.__enter__()
        except Exception:  # noqa: BLE001  (refused: the slot is not back yet)
            if time.time() > deadline:
                raise
            time.sleep(0.1)
            continue
        return ws, entered


# ---------------------------------------------------------------- lookup timeout


def test_the_name_lookup_budget_is_five_seconds(tp):
    assert getattr(tp, "RESOLVE_TIMEOUT", None) == 5, "app.term_proxy.RESOLVE_TIMEOUT must be 5 (s)"


def test_a_hung_name_lookup_answers_504_terminal_timeout_in_time(client, tp, monkeypatch):
    monkeypatch.delenv(SEAM, raising=False)
    monkeypatch.setattr(tp, "RESOLVE_TIMEOUT", 0.3, raising=False)
    release = threading.Event()
    real = socket.getaddrinfo

    def slow(host, port, *args, **kwargs):
        if isinstance(host, str) and host.startswith("lab-"):
            release.wait(5)
            raise socket.gaierror(socket.EAI_NONAME, "slow resolver (exam)")
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    started = time.time()
    try:
        resp = client.get(f"/s/{uuid.uuid4()}/term/")
        took = time.time() - started
    finally:
        release.set()
    assert resp.status_code == 504, (resp.status_code, resp.text[:200])
    assert "terminal_timeout" in resp.text, resp.text[:200]
    assert took < 3, f"the 504 took {took:.1f} s: the lookup is not bounded"


def test_hung_name_lookups_do_not_starve_the_default_thread_pool(tp, monkeypatch):
    """Logins (argon2 in asyncio.to_thread) must not queue behind hung name lookups."""
    monkeypatch.setattr(tp, "RESOLVE_TIMEOUT", 0.2, raising=False)
    release = threading.Event()
    real = socket.getaddrinfo

    def hung(host, port, *args, **kwargs):
        if isinstance(host, str) and host.endswith(".hung.test"):
            release.wait(15)
            raise socket.gaierror(socket.EAI_NONAME, "hung resolver (exam)")
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", hung)

    async def scenario() -> str:
        lookups = [asyncio.ensure_future(tp.resolve(f"lab-{i}.hung.test:7681")) for i in range(64)]
        try:
            await asyncio.wait_for(asyncio.gather(*lookups, return_exceptions=True), 3)
        except TimeoutError:
            pass
        try:
            return await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), 1.5)
        except TimeoutError:
            return "starved"
        finally:
            release.set()
            for t in lookups:
                t.cancel()

    try:
        outcome = asyncio.run(scenario())
    finally:
        release.set()
    assert outcome == "ok", "hung name lookups held every thread of the default executor"


# ---------------------------------------------------------------- websocket limits


def test_upstream_websocket_limits_are_sized_for_ttyd(tp):
    assert tp.MAX_WS_MESSAGE == 1 * MIB, f"MAX_WS_MESSAGE is {tp.MAX_WS_MESSAGE}, want 1 MiB"
    assert getattr(tp, "MAX_WS_QUEUE", None) == 16, "app.term_proxy.MAX_WS_QUEUE must be 16"


def test_the_upstream_connection_is_opened_with_the_limits(client, tp, upstream, monkeypatch):
    seen: list[dict] = []
    real = tp.connect

    def spy(*args, **kwargs):
        seen.append(dict(kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(tp, "connect", spy)
    with open_ws(client, str(uuid.uuid4())) as ws:
        assert echoes(ws)
    assert seen, "the proxy never opened an upstream connection"
    assert seen[0].get("max_size") == 1 * MIB, seen[0].get("max_size")
    assert seen[0].get("max_queue") == 16, seen[0].get("max_queue")


def test_a_lab_frame_above_one_mib_closes_the_terminal(client, upstream):
    blob = b"0" + b"x" * (2 * MIB)  # the stub echoes it back: a 2 MiB frame from the lab
    with open_ws(client, str(uuid.uuid4())) as ws:
        pump = Pump(ws)
        ws.send_bytes(blob)
        kind, _ = pump.frame(30)
        assert kind == "close", f"a {len(blob)} byte lab frame passed the proxy ({kind})"


def test_a_large_paste_below_the_limit_still_passes(client, upstream):
    blob = b"0" + bytes(range(256)) * 3500  # about 875 KiB
    with open_ws(client, str(uuid.uuid4())) as ws:
        pump = Pump(ws)
        ws.send_bytes(blob)
        assert pump.frame(30) == ("bytes", blob)


# ---------------------------------------------------------------- sockets per session


def test_the_limit_is_four_terminal_sockets_per_session(tp):
    assert getattr(tp, "MAX_TERMINALS_PER_SESSION", None) == 4, (
        "app.term_proxy.MAX_TERMINALS_PER_SESSION must be 4"
    )


def test_a_fifth_terminal_socket_of_a_session_is_refused_before_accept(client, upstream):
    sid = str(uuid.uuid4())
    opened = []
    try:
        for _ in range(4):
            ws = open_ws(client, sid)
            opened.append((ws, ws.__enter__()))
            assert echoes(opened[-1][1]), "one of the first four sockets did not work"
        assert len(upstream.ws) == 4
        assert_too_many(client, sid)
        assert len(upstream.ws) == 4, "the refused fifth socket still contacted the lab"
    finally:
        for ws, _ in reversed(opened):
            ws.__exit__(None, None, None)


def test_the_slot_is_given_back_when_a_socket_closes(client, upstream):
    sid = str(uuid.uuid4())
    opened = []
    try:
        for _ in range(4):
            ws = open_ws(client, sid)
            opened.append((ws, ws.__enter__()))
            assert echoes(opened[-1][1])
        assert_too_many(client, sid)
        ws, _ = opened.pop(0)
        ws.__exit__(None, None, None)
        fifth, entered = open_when_free(client, sid)
        opened.append((fifth, entered))
        assert echoes(entered), "after one socket closed a new one must connect"
    finally:
        for ws, _ in reversed(opened):
            ws.__exit__(None, None, None)


def test_another_session_has_its_own_four_slots(client, upstream):
    full, other = str(uuid.uuid4()), str(uuid.uuid4())
    opened = []
    try:
        for _ in range(4):
            ws = open_ws(client, full)
            opened.append((ws, ws.__enter__()))
            assert echoes(opened[-1][1])
        assert_too_many(client, full)
        ws = open_ws(client, other)
        opened.append((ws, ws.__enter__()))
        assert echoes(opened[-1][1]), "a full session must not block another session"
    finally:
        for ws, _ in reversed(opened):
            ws.__exit__(None, None, None)


def test_failed_upstream_connects_do_not_use_up_slots(client, monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()  # nothing listens there now
    monkeypatch.setenv(SEAM, f"127.0.0.1:{dead_port}")
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    sid = str(uuid.uuid4())
    for attempt in range(6):
        try:
            with open_ws(client, sid) as ws:
                Pump(ws).frame(5)
        except WebSocketDenialResponse as denial:
            assert denial.status_code == 502, (
                f"attempt {attempt + 1}: {denial.status_code} (a failed connect kept its slot?)"
            )
        except WebSocketDisconnect as exc:
            assert exc.code != 1008 or attempt < 4, "a failed connect kept its slot"


# ---------------------------------------------------------------- startup line for the seam


def _start_app(monkeypatch, tmp_path, seam: str | None):
    from starlette.testclient import TestClient

    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    _set_env(monkeypatch, write_content(tmp_path / "content"), tmp_path / "meminfo", tmp_path)
    if seam is not None:
        monkeypatch.setenv(SEAM, seam)
    from app.main import app

    return TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000))


def _error_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def test_startup_with_the_test_seam_set_logs_one_error_line_without_the_value(
    monkeypatch, tmp_path, caplog
):
    value = "127.0.0.1:34567"
    caplog.set_level(logging.INFO)
    with _start_app(monkeypatch, tmp_path, value):
        pass
    named = [m for m in _error_lines(caplog) if SEAM in m]
    assert len(named) == 1, f"want one ERROR line naming {SEAM}, got {_error_lines(caplog)!r}"
    everything = "\n".join(
        r.getMessage() + json.dumps(r.__dict__, default=str) for r in caplog.records
    )
    assert value not in everything, "the seam's value was logged"
    assert "34567" not in everything


def test_startup_without_the_test_seam_logs_no_error_about_it(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with _start_app(monkeypatch, tmp_path, None):
        pass
    assert not [m for m in _error_lines(caplog) if SEAM in m]
