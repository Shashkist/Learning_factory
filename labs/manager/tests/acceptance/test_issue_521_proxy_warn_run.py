"""Exam for #521: a suppressed-warning count never outlives its run of failures (review of #518).

Contract added here (the #515 exam stays as it is and still holds):
- A run of failed lookup-thread starts logs one warning and suppresses the rest. When the run
  ends (a lookup succeeds, or the next failure comes more than a window after the run's last
  failure) its pending count is either logged as its own line or dropped. Which one is the
  implementer's choice; this exam accepts both. It pins only that a later, unrelated warning
  carries no count from the earlier run, and that no line ever reports more suppressed warnings
  than the run it belongs to actually suppressed.
Seams are those of the #515 exam: `socket.getaddrinfo`, `time.monotonic`,
`threading.Thread.start`, log capture. The message wording is not pinned except the count, which
is recognised by the word "suppressed" and a number.
"""

import asyncio
import logging
import re
import socket
import threading
import time

import pytest
from test_issue_93_trusted_proxy_name import NAME, A, Resolver, _req

from app import auth

KEY = "LF_TRUSTED_PROXY"
XFF = f"9.9.9.9, {A}"
HOURS = 3 * 3600


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


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name == "app.auth"
    ]


def _counts(line: str) -> list[int]:
    """Numbers attached to the word 'suppressed' in a line (the exception text has none)."""
    if "suppressed" not in line.lower():
        return []
    return [int(n) for n in re.findall(r"\d+", line.replace(NAME, ""))]


def _cannot_start(self, *args, **kwargs):
    raise RuntimeError("cannot start a new thread")


def _failing_run(monkeypatch, clock, seconds: float) -> int:
    """Failed starts every 0.5 s for `seconds`; returns how many attempts the run made."""
    real_start = threading.Thread.start
    attempts = [0]

    def failing(self, *args, **kwargs):
        attempts[0] += 1
        raise RuntimeError("cannot start a new thread")

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", failing)
        start = clock[0]
        try:
            while clock[0] - start < seconds:
                auth.client_ip(_req(A, XFF))
                clock[0] += 0.5
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    asyncio.run(scenario())
    return attempts[0]


def _one_failed_start(monkeypatch) -> None:
    real_start = threading.Thread.start

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            auth.client_ip(_req(A, XFF))
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    asyncio.run(scenario())


def _successful_lookup(monkeypatch, clock) -> None:
    """Past the backoff, let a real worker thread run the lookup to the end."""
    clock[0] += 5
    real_start = threading.Thread.start
    started: list[threading.Thread] = []

    def spy(self, *args, **kwargs):
        if self.name == "lf-proxy-lookup":
            started.append(self)
        return real_start(self, *args, **kwargs)

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", spy)
        try:
            auth.client_ip(_req(A, XFF))
            for t in started:
                await asyncio.get_running_loop().run_in_executor(None, t.join, 5)
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    asyncio.run(scenario())
    assert started, "the lookup did not start: the scenario is not a successful lookup"


def test_a_failure_hours_after_a_success_carries_no_count_from_the_earlier_run(
    monkeypatch, caplog, clock
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    caplog.set_level(logging.INFO, logger="app.auth")
    attempts = _failing_run(monkeypatch, clock, 30)
    assert attempts >= 25, f"the run of failures did not happen: {attempts}"
    first_run = _warnings(caplog)
    assert len(first_run) >= 1, "the run logged no warning at all"

    _successful_lookup(monkeypatch, clock)
    clock[0] += HOURS  # the cached answer is long expired; a refresh is due again
    caplog.clear()
    _one_failed_start(monkeypatch)

    later = _warnings(caplog)
    assert len(later) == 1, f"one failed start, one warning expected: {later}"
    assert _counts(later[0]) == [], f"count from the earlier run leaked: {later[0]}"


def test_no_line_reports_more_suppressed_warnings_than_its_run_suppressed(
    monkeypatch, caplog, clock
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    caplog.set_level(logging.INFO, logger="app.auth")
    attempts = _failing_run(monkeypatch, clock, 30)
    _successful_lookup(monkeypatch, clock)
    clock[0] += HOURS
    _one_failed_start(monkeypatch)

    lines = _warnings(caplog)
    reported = [n for line in lines for n in _counts(line)]
    # the old run suppressed attempts - 1 warnings (one was logged); the later run suppressed 0
    assert sum(reported) <= attempts - 1, f"more suppressed than there were: {lines}"
    assert all(n <= attempts - 1 for n in reported), f"impossible count: {lines}"
    assert _counts(lines[-1]) == [], f"the last line carries an old count: {lines[-1]}"


def test_a_failure_long_after_a_run_that_never_succeeded_carries_no_count_from_it(
    monkeypatch, caplog, clock
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    caplog.set_level(logging.INFO, logger="app.auth")
    attempts = _failing_run(monkeypatch, clock, 30)
    assert attempts >= 25, f"the run of failures did not happen: {attempts}"

    clock[0] += HOURS  # the run is over: no failure for hours, and no success either
    caplog.clear()
    _one_failed_start(monkeypatch)

    later = _warnings(caplog)
    assert later, "the failure after the quiet period logged no warning"
    assert _counts(later[-1]) == [], f"count from the earlier run leaked: {later[-1]}"
    reported = [n for line in later for n in _counts(line)]
    assert all(n <= attempts - 1 for n in reported), f"impossible count: {later}"
