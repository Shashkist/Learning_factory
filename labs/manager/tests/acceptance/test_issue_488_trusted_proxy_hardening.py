"""Exam for #488: hardening of the trusted proxy refresh (review of #486, #480, D132, D135).

Contract added here:
- A guard case for #486: a reset that lands before the old worker enters the resolver voids its
  answer (the old code read the generation twice).
- A `Thread.start()` that fails (threads exhausted) never reaches the caller of `client_ip`: the
  known addresses come back and exactly one warning line is logged. No 500 on a request.
- Only `Exception` is swallowed around `Thread.start()`. A `KeyboardInterrupt` from `start()`
  propagates and leaves the lookup counted as in flight (the worker already runs).
- `warm_trusted_proxy` for an empty name or an IP literal does no lookup, starts no thread and does
  not claim a lookup.
- deploy.sh prints its own WARNING for `trusted proxy: startup lookup raised unexpectedly`.
Seams are those of the #475/#480 exams: `socket.getaddrinfo`, `time.monotonic`,
`threading.Thread.start`.
"""

import asyncio
import logging
import socket
import threading
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from test_issue_13_deploy import Server, _ok, _stdin
from test_issue_93_trusted_proxy_name import NAME, REAL, A, B, Resolver, _answer, _req

from app import auth

STARTUP_BUG_LINE = "trusted proxy: startup lookup raised unexpectedly"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY", NAME)
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    reset = getattr(auth, "reset_trusted_proxy_cache", lambda: None)
    reset()
    yield
    reset()


def _proxy_warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING and r.name == "app.auth"]


def _cannot_start(self, *args, **kwargs):
    raise RuntimeError("cannot start a new thread")


# ============================================================ 1. the reset before the resolver


def test_a_reset_before_the_old_worker_reaches_the_resolver_voids_its_answer(monkeypatch):
    """Corrected #480 case: the reset lands before the worker enters getaddrinfo, so a worker that
    re-read the generation after the reset (the old double read) would cache the old answer."""
    fake = Resolver([A], [B])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    real_start = threading.Thread.start
    started: list[threading.Thread] = []

    def reset_then_start(self, *args, **kwargs):
        auth.reset_trusted_proxy_cache()  # before the worker runs, so before getaddrinfo
        started.append(self)
        return real_start(self, *args, **kwargs)

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", reset_then_start)
        auth.client_ip(_req(A, REAL))  # in a loop: schedules the old worker (it gets answer A)
        monkeypatch.setattr(threading.Thread, "start", real_start)
        assert started, "the exam expected a worker thread to be started"
        for t in started:
            await asyncio.get_running_loop().run_in_executor(None, t.join, 5)

    asyncio.run(scenario())
    assert fake.calls, "the old worker never reached the resolver"
    # A fresh sync lookup now gets the later answer B; the old worker's A must not be cached.
    assert auth.client_ip(_req(B, f"9.9.9.9, {REAL}")) == REAL
    assert auth.client_ip(_req(A, f"9.9.9.9, {REAL}")) == A, "the old address was cached"


# ============================================================ 2. no 500 when threads run out


def test_a_thread_that_cannot_start_gives_the_cached_answer_and_one_warning(monkeypatch, caplog):
    clock = [0.0]
    real_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + clock[0])
    fake = Resolver([A], [B])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert auth.client_ip(_req(A, REAL)) == REAL  # sync: inline lookup, entry cached with A
    clock[0] += 61  # expired: a refresh is due
    real_start = threading.Thread.start
    caplog.set_level(logging.INFO, logger="app.auth")
    caplog.clear()

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            return auth.client_ip(_req(A, REAL))  # must not raise
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    assert asyncio.run(scenario()) == REAL, "the known address is no longer trusted"
    assert len(_proxy_warnings(caplog)) == 1, [r.getMessage() for r in _proxy_warnings(caplog)]


def test_a_thread_that_cannot_start_with_nothing_cached_trusts_nobody_without_raising(
    monkeypatch, caplog
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    real_start = threading.Thread.start
    caplog.set_level(logging.INFO, logger="app.auth")

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            return auth.client_ip(_req(A, f"9.9.9.9, {REAL}"))  # must not raise
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    assert asyncio.run(scenario()) == A  # fail closed: the peer itself
    assert len(_proxy_warnings(caplog)) == 1, [r.getMessage() for r in _proxy_warnings(caplog)]


def test_the_trusted_proxy_addresses_call_itself_does_not_raise_when_threads_run_out(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    real_start = threading.Thread.start

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            return auth.trusted_proxy_addresses()
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    assert asyncio.run(scenario()) == frozenset()


def test_a_request_does_not_answer_500_while_threads_cannot_start(monkeypatch):
    """Request level: an async route that needs the client address (as login does)."""
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    app = FastAPI()

    @app.get("/who")
    async def who(request: Request) -> dict[str, str]:  # runs inside the event loop
        return {"ip": auth.client_ip(request)}

    real_start = threading.Thread.start
    # Enter the client first: its own portal thread must start. Only the requests run with
    # thread starts refused, so the only thread that fails is the proxy refresh worker.
    with TestClient(app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            responses = [client.get("/who") for _ in range(3)]
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)
    assert [r.status_code for r in responses] == [200, 200, 200]


# ============================================================ 3. BaseException is not swallowed


def test_a_keyboard_interrupt_from_thread_start_propagates_and_allows_no_parallel_lookup(
    monkeypatch,
):
    gate = threading.Event()
    lock = threading.Lock()
    flight = {"now": 0, "max": 0, "calls": 0}

    def gated(host, port=None, *args, **kwargs):
        with lock:
            flight["calls"] += 1
            flight["now"] += 1
            flight["max"] = max(flight["max"], flight["now"])
        try:
            gate.wait(5)
            return _answer(A)
        finally:
            with lock:
                flight["now"] -= 1

    monkeypatch.setattr(socket, "getaddrinfo", gated)
    real_start = threading.Thread.start

    def start_then_interrupt(self, *args, **kwargs):
        real_start(self, *args, **kwargs)  # the worker is already running ...
        raise KeyboardInterrupt  # ... when the interrupt arrives

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", start_then_interrupt)
        raised = False
        try:
            try:
                auth.client_ip(_req(A, REAL))
            except KeyboardInterrupt:
                raised = True
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)
        assert raised, "a KeyboardInterrupt from Thread.start() must propagate"
        # the first worker is still inside the resolver: more requests must not start another
        for _ in range(3):
            auth.client_ip(_req(A, REAL))
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        gate.set()
        await asyncio.sleep(0.3)

    try:
        asyncio.run(scenario())
    finally:
        gate.set()
    assert flight["calls"] == 1, f"{flight['calls']} lookups for one in-flight refresh (expected 1)"
    assert flight["max"] <= 1


# ============================================================ 4. a simpler warm_trusted_proxy


@pytest.mark.parametrize(
    "value",
    ["", "   ", "10.93.0.5", "2001:db8::5"],
    ids=["empty", "blank", "ipv4-literal", "ipv6-literal"],
)
def test_warm_does_no_lookup_and_starts_no_thread_for_an_empty_name_or_an_ip_literal(
    monkeypatch, value
):
    monkeypatch.setenv("LF_TRUSTED_PROXY", value)
    fake = Resolver([A])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    started = []
    real_start = threading.Thread.start

    def spy(self, *args, **kwargs):
        started.append(self)
        return real_start(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", spy)
    assert auth.warm_trusted_proxy() is None
    assert fake.calls == []
    assert started == []


@pytest.mark.parametrize("value", ["", "10.93.0.5"], ids=["empty", "ip-literal"])
def test_warm_does_not_claim_a_lookup_for_an_empty_name_or_an_ip_literal(monkeypatch, value):
    """Claiming and releasing a lookup for nothing is what the early return removes."""
    monkeypatch.setenv("LF_TRUSTED_PROXY", value)
    claims = []
    real_claim = auth._claim_lookup

    def spy():
        claims.append(1)
        return real_claim()

    monkeypatch.setattr(auth, "_claim_lookup", spy)
    auth.warm_trusted_proxy()
    assert claims == [], "warm_trusted_proxy claimed a lookup it never runs"


def test_warm_for_a_name_still_resolves_once_and_fills_the_cache(monkeypatch):
    fake = Resolver([A])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    auth.warm_trusted_proxy()
    assert fake.calls == [NAME]
    assert auth.client_ip(_req(A, REAL)) == REAL  # cached by the warm-up: no second lookup
    assert fake.calls == [NAME]


# ============================================================ 5. deploy.sh notices a startup bug


@pytest.fixture
def deploy(tmp_path) -> Server:
    s = Server(tmp_path)
    stub = s.bin / "docker"
    text = stub.read_bytes().decode()
    old = 'echo "trusted proxy nginx-proxy3 -> 172.20.0.5"'
    assert old in text, "the exam #13 docker stub changed"
    new = 'echo "${STUB_PROXY_LINE:-trusted proxy nginx-proxy3 -> 172.20.0.5}"'
    stub.write_bytes(text.replace(old, new).encode())
    return s


def _warning_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if "WARNING" in line]


@pytest.mark.parametrize(
    "line",
    [
        STARTUP_BUG_LINE,
        f"WARNING app.main: {STARTUP_BUG_LINE}",
        f"2026-11-22 10:00:00,123 WARNING app.main {STARTUP_BUG_LINE}",
    ],
    ids=["bare", "logger-prefix", "timestamp-prefix"],
)
def test_deploy_prints_its_own_warning_for_a_startup_lookup_that_raised(deploy, line):
    r = deploy.run(_stdin(), STUB_PROXY_LINE=line)
    _ok(r)
    out = r.stdout + r.stderr
    own = [x for x in out.splitlines() if "WARNING:" in x and "startup lookup" in x]
    assert own, f"no deploy WARNING of its own for the startup lookup bug:\n{out}"
    assert "deployed lf-manager" in out


def test_deploy_prints_no_startup_bug_warning_for_a_healthy_log_line(deploy):
    r = deploy.run(_stdin())
    _ok(r)
    out = r.stdout + r.stderr
    assert not [x for x in _warning_lines(out) if "startup lookup" in x]
