"""Exam for #515: the backoff keeps a fresher answer, the warning is throttled (review of #514).

Contract added here:
- A successful lookup that lands between a request's cache read and its failed `Thread.start()`
  is not overwritten by the backoff: the fresh set is trusted afterwards, its "set changed" info
  line is logged, and the in-flight flag is clear so the next due refresh starts a worker.
- While starts keep failing the retry stays at about one per second, but the warning is logged at
  most once per 60 s for a name, and the next line carries the count of suppressed warnings.
- The backoff expiry is `now + _PROXY_BACKOFF` (not capped by the TTL).
Seams are those of the #475/#510 exams: `socket.getaddrinfo`, `time.monotonic`,
`threading.Thread.start`, log capture. The message wording is not pinned.
"""

import asyncio
import logging
import re
import socket
import threading
import time

import pytest
from test_issue_93_trusted_proxy_name import NAME, REAL, A, B, Resolver, _req

from app import auth

KEY = "LF_TRUSTED_PROXY"
XFF = f"9.9.9.9, {REAL}"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv(KEY, NAME)
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    reset = getattr(auth, "reset_trusted_proxy_cache", lambda: None)
    reset()
    yield
    reset()


@pytest.fixture
def clock(monkeypatch):
    offset = [0.0]
    real_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + offset[0])
    return offset


def _proxy_warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING and r.name == "app.auth"]


def _cannot_start(self, *args, **kwargs):
    raise RuntimeError("cannot start a new thread")


# ============================================================ 1. a fresher answer survives


@pytest.mark.parametrize("prior", [True, False], ids=["expired-entry", "nothing-cached"])
def test_a_lookup_landing_before_a_failed_start_is_still_trusted_afterwards(
    monkeypatch, caplog, clock, prior
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,), (B,)) if prior else Resolver((B,)))
    if prior:
        assert auth.client_ip(_req(A, REAL)) == REAL  # sync: inline lookup caches {A}
        clock[0] += 61  # expired: a refresh is due
    caplog.set_level(logging.INFO, logger="app.auth")
    caplog.clear()
    real_start = threading.Thread.start

    def land_then_fail(self, *args, **kwargs):
        # the worker's answer lands (and the worker finishes) before the start is reported failed
        self.run()
        raise RuntimeError("cannot start a new thread")

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", land_then_fail)
        try:
            auth.client_ip(_req(B, XFF))  # reads the stale cache, schedules, start fails
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)
        return auth.client_ip(_req(B, XFF)), auth.client_ip(_req(A, XFF))

    new_peer, old_peer = asyncio.run(scenario())
    assert new_peer == REAL, "the fresh lookup result was overwritten by the old set"
    assert old_peer == A, "the old set is still trusted after the fresh answer landed"
    changed = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO and r.name == "app.auth" and B in r.getMessage()
    ]
    assert len(changed) == 1, f"expected one 'set changed' line for {B}, got {changed}"


def test_a_failed_start_clears_the_in_flight_flag_so_the_next_due_refresh_starts(
    monkeypatch, clock
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,), (B,)))
    assert auth.client_ip(_req(A, REAL)) == REAL
    clock[0] += 61
    real_start = threading.Thread.start
    started: list[threading.Thread] = []

    def spy(self, *args, **kwargs):
        if self.name == "lf-proxy-lookup":
            started.append(self)
        return real_start(self, *args, **kwargs)

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            auth.client_ip(_req(A, XFF))
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)
        clock[0] += 5  # far past the backoff
        monkeypatch.setattr(threading.Thread, "start", spy)
        try:
            auth.client_ip(_req(A, XFF))
            for t in started:
                await asyncio.get_running_loop().run_in_executor(None, t.join, 5)
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)
        return auth.client_ip(_req(B, XFF))

    assert asyncio.run(scenario()) == REAL, "no refresh ran after the failed start"
    assert started, "the in-flight flag was not cleared by the failed start"


# ============================================================ 2. throttled warning, 1 s retry


@pytest.mark.parametrize("prior", [True, False], ids=["expired-entry", "nothing-cached"])
def test_failed_starts_over_120_s_warn_at_most_twice_and_report_the_suppressed_count(
    monkeypatch, caplog, clock, prior
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    if prior:
        assert auth.client_ip(_req(A, REAL)) == REAL
        clock[0] += 61
    caplog.set_level(logging.INFO, logger="app.auth")
    caplog.clear()
    real_start = threading.Thread.start
    attempts: list[float] = []
    at_warning: list[int] = []  # attempt count when each warning was logged

    class Marker(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                at_warning.append(len(attempts))

    handler = Marker()
    logging.getLogger("app.auth").addHandler(handler)

    def failing(self, *args, **kwargs):
        attempts.append(clock[0])
        raise RuntimeError("cannot start a new thread")

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", failing)
        start = clock[0]
        try:
            while clock[0] - start < 120:
                auth.client_ip(_req(A, XFF))
                clock[0] += 0.5
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    try:
        asyncio.run(scenario())
    finally:
        logging.getLogger("app.auth").removeHandler(handler)

    lines = [r.getMessage() for r in _proxy_warnings(caplog)]
    assert 100 <= len(attempts) <= 125, f"retry is not about 1 per second: {len(attempts)}"
    assert len(lines) == 2, f"expected 2 warnings over 120 s, got {len(lines)}: {lines}"
    suppressed = at_warning[1] - at_warning[0] - 1
    assert suppressed >= 50
    numbers = [int(n) for n in re.findall(r"\d+", lines[1])]
    assert suppressed in numbers, f"second line must report {suppressed} suppressed: {lines[1]}"


def test_the_first_failed_start_after_a_quiet_minute_warns_again_at_once(
    monkeypatch, caplog, clock
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    caplog.set_level(logging.INFO, logger="app.auth")
    real_start = threading.Thread.start

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            auth.client_ip(_req(A, XFF))
            clock[0] += 61
            auth.client_ip(_req(A, XFF))
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    asyncio.run(scenario())
    lines = [r.getMessage() for r in _proxy_warnings(caplog)]
    assert len(lines) == 2, f"one warning per 60 s window expected, got {lines}"


# ============================================================ 3. backoff expiry is now + backoff


def test_the_backoff_expires_exactly_one_backoff_after_the_failed_start(monkeypatch, clock):
    monkeypatch.setattr(auth, "_PROXY_BACKOFF", 5.0)
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    real_start = threading.Thread.start
    attempts: list[float] = []

    def failing(self, *args, **kwargs):
        attempts.append(clock[0])
        raise RuntimeError("cannot start a new thread")

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", failing)
        try:
            auth.client_ip(_req(A, XFF))
            clock[0] += 4.5
            auth.client_ip(_req(A, XFF))  # inside the backoff: no new attempt
            assert len(attempts) == 1
            clock[0] += 1.0  # 5.5 s after the first failure
            auth.client_ip(_req(A, XFF))
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    asyncio.run(scenario())
    assert len(attempts) == 2, f"expected a retry after the backoff, got {attempts}"
