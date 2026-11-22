"""Exam for #510: one warning per exhausted window, one read of the setting (review of #509).

Contract added here:
- While `Thread.start()` keeps failing (threads exhausted), N requests in one window log exactly
  one WARNING line from the trusted proxy code. When starts work again the refresh works again,
  and a new exhausted window may warn again. The backoff length is not pinned: the exam only
  assumes it is far below 60 s of the (fake) monotonic clock.
- Each call of `trusted_proxy_addresses`, `client_ip` (the verify path) and
  `warm_trusted_proxy` reads LF_TRUSTED_PROXY from the environment exactly once, for an unset
  value, a blank one, an IP literal and a host name alike.
The D135 notes (AC3) are documentation and have no case here.
Seams are those of the #475/#488 exams: `socket.getaddrinfo`, `time.monotonic`,
`threading.Thread.start`, and a counting `os.environ`.
"""

import asyncio
import logging
import os
import socket
import threading
import time

import pytest
from test_issue_93_trusted_proxy_name import NAME, REAL, A, Resolver, _req

from app import auth

N = 20
KEY = "LF_TRUSTED_PROXY"


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


# ============================================================ 1. one warning per exhausted window


def test_n_requests_in_one_exhausted_window_log_one_warning_with_nothing_cached(
    monkeypatch, caplog
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    caplog.set_level(logging.INFO, logger="app.auth")
    real_start = threading.Thread.start

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            return [auth.client_ip(_req(A, f"9.9.9.9, {REAL}")) for _ in range(N)]
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    assert asyncio.run(scenario()) == [A] * N  # fail closed, never raises
    lines = [r.getMessage() for r in _proxy_warnings(caplog)]
    assert len(lines) == 1, f"{len(lines)} warnings for one exhausted window: {lines}"


def test_n_requests_in_one_exhausted_window_log_one_warning_with_an_expired_entry(
    monkeypatch, caplog, clock
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    assert auth.client_ip(_req(A, REAL)) == REAL  # sync: inline lookup, entry cached with A
    clock[0] += 61  # expired: a refresh is due
    caplog.set_level(logging.INFO, logger="app.auth")
    caplog.clear()
    real_start = threading.Thread.start

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            return [auth.client_ip(_req(A, REAL)) for _ in range(N)]
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    assert asyncio.run(scenario()) == [REAL] * N  # the known set is still trusted
    lines = [r.getMessage() for r in _proxy_warnings(caplog)]
    assert len(lines) == 1, f"{len(lines)} warnings for one exhausted window: {lines}"


def test_the_refresh_works_again_after_the_window_and_a_new_window_can_warn_again(
    monkeypatch, caplog, clock
):
    fake = Resolver([A])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    caplog.set_level(logging.INFO, logger="app.auth")
    real_start = threading.Thread.start
    started: list[threading.Thread] = []

    def spy(self, *args, **kwargs):
        started.append(self)
        return real_start(self, *args, **kwargs)

    async def window_exhausted():
        monkeypatch.setattr(threading.Thread, "start", _cannot_start)
        try:
            for _ in range(N):
                auth.client_ip(_req(A, f"9.9.9.9, {REAL}"))
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    async def window_over():
        monkeypatch.setattr(threading.Thread, "start", spy)
        try:
            auth.client_ip(_req(A, f"9.9.9.9, {REAL}"))  # schedules a worker
            for t in started:
                await asyncio.get_running_loop().run_in_executor(None, t.join, 5)
        finally:
            monkeypatch.setattr(threading.Thread, "start", real_start)

    async def scenario():
        await window_exhausted()
        assert len(_proxy_warnings(caplog)) == 1
        clock[0] += 60  # far past any short backoff: the window is over, starts work again
        await window_over()
        assert started, "no refresh worker was started after the window ended"
        assert fake.calls == [NAME], "the refresh did not resolve the name"
        assert auth.client_ip(_req(A, f"9.9.9.9, {REAL}")) == REAL, "the refreshed set is unused"
        clock[0] += 120  # the entry expired: a refresh is due, and threads are exhausted again
        caplog.clear()
        await window_exhausted()

    asyncio.run(scenario())
    lines = [r.getMessage() for r in _proxy_warnings(caplog)]
    assert len(lines) == 1, f"a new exhausted window must warn once, got {len(lines)}: {lines}"


# ============================================================ 2. one read of the setting


class CountingEnviron(dict):
    """A stand-in for os.environ that counts every lookup of LF_TRUSTED_PROXY."""

    reads = 0

    def get(self, key, default=None):
        if key == KEY:
            self.reads += 1
        return super().get(key, default)

    def __getitem__(self, key):
        if key == KEY:
            self.reads += 1
        return super().__getitem__(key)

    def __contains__(self, key):
        if key == KEY:
            self.reads += 1
        return super().__contains__(key)


def _counting(monkeypatch, value: str | None) -> CountingEnviron:
    env = CountingEnviron(os.environ)
    env.pop(KEY, None)
    if value is not None:
        dict.__setitem__(env, KEY, value)
    env.reads = 0
    monkeypatch.setattr(os, "environ", env)
    return env


VALUES = [None, "", "   ", "10.93.0.5", "2001:db8::5", f"  {NAME} "]
IDS = ["unset", "empty", "blank", "ipv4-literal", "ipv6-literal", "name"]


@pytest.mark.parametrize("value", VALUES, ids=IDS)
def test_trusted_proxy_addresses_reads_the_setting_once(monkeypatch, value):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    env = _counting(monkeypatch, value)
    auth.trusted_proxy_addresses()
    assert env.reads == 1, f"{env.reads} reads of {KEY} in one call"


@pytest.mark.parametrize("value", VALUES, ids=IDS)
def test_the_verify_path_client_ip_reads_the_setting_once(monkeypatch, value):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    env = _counting(monkeypatch, value)
    auth.client_ip(_req(A, REAL))
    assert env.reads == 1, f"{env.reads} reads of {KEY} in one call"


@pytest.mark.parametrize("value", VALUES, ids=IDS)
def test_warm_trusted_proxy_reads_the_setting_once(monkeypatch, value):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    env = _counting(monkeypatch, value)
    auth.warm_trusted_proxy()
    assert env.reads == 1, f"{env.reads} reads of {KEY} in one call"


@pytest.mark.parametrize("value", ["10.93.0.5", f"  {NAME} "], ids=["literal", "name"])
def test_the_stripped_value_is_still_what_is_trusted(monkeypatch, value):
    """Guard for the refactor: a padded literal or name still stands for its address."""
    monkeypatch.setattr(socket, "getaddrinfo", Resolver([A]))
    monkeypatch.setenv(KEY, value)
    expected = frozenset({"10.93.0.5"}) if value == "10.93.0.5" else frozenset({A})
    assert auth.trusted_proxy_addresses() == expected
