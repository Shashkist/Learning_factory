"""Exam for #475: leftovers of #93 (LF_TRUSTED_PROXY by name, D132).

Contract added here:
- A lookup that raises anything but an answer (UnicodeError from a malformed name such as
  `nginx..proxy3`, ValueError, OSError) is a failed lookup: nobody is trusted, `client_ip` never
  raises, the failure is cached for the TTL and logged as one WARNING `trusted proxy <name>`.
- Inside a running event loop `client_ip` never waits for DNS: the lookup runs off the request
  path (lifespan task or a worker thread). While the first lookup is pending nobody is trusted.
  With no running loop (plain sync callers, the #93 exam) the lookup may still be done inline.
  Seam used: `socket.getaddrinfo` and `time.monotonic` (the TTL clock), as in the #93 exam.
- LF_TRUSTED_PROXY_TTL_S is clamped to 1..3600 s; inf, nan, 0, negatives and garbage give 30.
- deploy.sh prints a loud `WARNING` line when the manager's `trusted proxy` line shows no
  address or a failed lookup, and still exits 0 (the deploy stub is the one of exam #13).
"""

import asyncio
import logging
import socket
import threading
import time

import pytest
from test_issue_13_deploy import Server, _ok, _stdin
from test_issue_93_trusted_proxy_name import NAME, REAL, A, B, Resolver, _answer, _req

from app import auth


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY", NAME)
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    reset = getattr(auth, "reset_trusted_proxy_cache", lambda: None)
    reset()
    yield
    reset()


def _use(monkeypatch, *steps) -> Resolver:
    fake = Resolver(*steps)
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return fake


def _warnings(caplog, name: str = NAME):
    return [
        r for r in caplog.records
        if r.levelno == logging.WARNING and f"trusted proxy {name}" in r.getMessage()
    ]


# ============================================================ malformed names and odd errors

BAD_ERRORS = [
    UnicodeError("label empty or too long"),
    UnicodeEncodeError("idna", "x", 0, 1, "label empty or too long"),
    ValueError("odd resolver"),
]


@pytest.mark.parametrize("exc", BAD_ERRORS, ids=lambda e: type(e).__name__)
def test_a_lookup_that_raises_unicode_or_value_error_trusts_nobody_and_does_not_raise(
    monkeypatch, exc
):
    _use(monkeypatch, exc)
    assert auth.client_ip(_req(A, f"9.9.9.9, {REAL}")) == A


@pytest.mark.parametrize("exc", BAD_ERRORS, ids=lambda e: type(e).__name__)
def test_that_failure_is_cached_for_the_ttl_and_warns_once(monkeypatch, caplog, exc):
    fake = _use(monkeypatch, exc, [A])
    with caplog.at_level(logging.INFO, logger="app.auth"):
        for _ in range(6):
            assert auth.client_ip(_req(A, REAL)) == A
    assert len(fake.calls) == 1, "a failed lookup must be cached like an answer"
    assert len(_warnings(caplog)) == 1


@pytest.mark.parametrize("name", ["nginx..proxy3", "a" * 64 + ".invalid", ".nginx", "x" * 70])
def test_a_really_malformed_name_never_reaches_the_caller_as_an_error(monkeypatch, caplog, name):
    """No fake: the real getaddrinfo raises UnicodeError for these (no network is touched)."""
    monkeypatch.setenv("LF_TRUSTED_PROXY", name)
    with caplog.at_level(logging.INFO, logger="app.auth"):
        for _ in range(3):
            assert auth.client_ip(_req(A, REAL)) == A
    assert len(_warnings(caplog, name)) == 1


# ============================================================ TTL clamp, observed on a clock


@pytest.mark.parametrize(
    "raw, effective",
    [
        (None, 30), ("", 30), ("abc", 30), ("inf", 30), ("-inf", 30), ("nan", 30), ("0", 30),
        ("-5", 30), ("-0.0", 30),
        ("1", 1), ("5", 5), ("300", 300), ("3600", 3600), ("99999", 3600), ("1e9", 3600),
    ],
)
def test_ttl_is_clamped_to_1_to_3600_seconds_and_bad_values_mean_30(monkeypatch, raw, effective):
    if raw is None:
        monkeypatch.delenv("LF_TRUSTED_PROXY_TTL_S", raising=False)
    else:
        monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", raw)
    clock = [10_000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    fake = _use(monkeypatch, [A])
    auth.client_ip(_req(A, REAL))
    clock[0] += effective - 0.5
    auth.client_ip(_req(A, REAL))
    assert len(fake.calls) == 1, f"TTL {raw!r}: looked up again before {effective} s"
    clock[0] += 1.0
    auth.client_ip(_req(A, REAL))
    assert len(fake.calls) == 2, f"TTL {raw!r}: no new lookup after {effective} s"


# ============================================================ lookup off the request path


def _manager_lifespan(monkeypatch):
    from app.main import app

    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    return app.router.lifespan_context(app)


async def _until(cond, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return cond()


def test_first_lookup_pending_means_fail_closed_and_client_ip_does_not_wait(
    monkeypatch, no_janitor
):
    gate = threading.Event()
    calls: list[str] = []

    def slow(host, port=None, *args, **kwargs):
        calls.append(host)
        gate.wait(1.0)  # a slow resolver (bounded, so a blocking implementation ends too)
        return _answer(A)

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    lifespan = _manager_lifespan(monkeypatch)

    async def scenario():
        starter = asyncio.create_task(lifespan.__aenter__())
        try:
            await asyncio.sleep(0.3)
            t0 = time.perf_counter()
            pending = auth.client_ip(_req(A, f"9.9.9.9, {REAL}"))
            took = time.perf_counter() - t0
            gate.set()
            await asyncio.wait_for(starter, 6)
            trusted_later = await _until(lambda: auth.client_ip(_req(A, REAL)) == REAL, 5)
            return pending, took, trusted_later
        finally:
            gate.set()
            if not starter.done():
                await asyncio.wait_for(starter, 6)
            await lifespan.__aexit__(None, None, None)

    pending, took, trusted_later = asyncio.run(scenario())
    assert pending == A, "while the first lookup is pending nobody may be trusted (fail closed)"
    assert took < 0.25, f"client_ip waited {took:.2f}s for DNS"
    assert calls and calls[0] == NAME
    assert trusted_later, "once the lookup finished the proxy must be trusted"


def test_a_slow_refresh_after_the_ttl_does_not_stall_the_event_loop(monkeypatch, no_janitor):
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "1")
    calls: list[str] = []

    def resolver(host, port=None, *args, **kwargs):
        calls.append(host)
        if len(calls) == 1:
            return _answer(A)
        time.sleep(1.0)  # slow upstream resolver on every refresh
        return _answer(B)

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    lifespan = _manager_lifespan(monkeypatch)

    async def scenario():
        async with lifespan:
            assert await _until(lambda: auth.client_ip(_req(A, REAL)) == REAL, 5)
            await asyncio.sleep(1.4)  # the 1 s TTL has run out
            gaps: list[float] = []
            stop = asyncio.Event()

            async def ticker():
                last = time.perf_counter()
                while not stop.is_set():
                    await asyncio.sleep(0.02)
                    now = time.perf_counter()
                    gaps.append(now - last)
                    last = now

            tick = asyncio.create_task(ticker())
            t0 = time.perf_counter()
            auth.client_ip(_req(A, REAL))
            took = time.perf_counter() - t0
            await asyncio.sleep(2.5)  # the refresh runs somewhere meanwhile
            stop.set()
            await tick
            moved = await _until(lambda: auth.client_ip(_req(B, REAL)) == REAL, 6)
            return took, max(gaps), moved

    took, worst_gap, moved = asyncio.run(scenario())
    assert took < 0.3, f"client_ip waited {took:.2f}s on an expired entry"
    assert worst_gap < 0.5, f"the event loop stalled for {worst_gap:.2f}s during a refresh"
    assert moved, "the refreshed address was never picked up"


# ============================================================ deploy.sh warning


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


BAD_LINES = [
    "trusted proxy nginx-proxy3 -> (none)",
    "trusted proxy nginx-proxy3: lookup failed ([Errno -2] Name or service not known); "
    "nobody trusted",
]


def _warning_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if "WARNING" in line]


@pytest.mark.parametrize("line", BAD_LINES, ids=["no-address", "lookup-failed"])
def test_deploy_warns_loudly_when_the_manager_resolved_no_proxy_address(deploy, line):
    r = deploy.run(_stdin(), STUB_PROXY_LINE=line)
    _ok(r)  # a warning, never a failed deploy
    out = r.stdout + r.stderr
    lines = _warning_lines(out)
    assert lines, f"no WARNING line in the deploy log:\n{out}"
    assert any("trusted proxy" in x and "nginx-proxy3" in x for x in lines), lines
    assert "deployed lf-manager" in out


def test_deploy_prints_no_warning_when_the_proxy_resolved(deploy):
    r = deploy.run(_stdin())
    _ok(r)
    out = r.stdout + r.stderr
    assert "trusted proxy nginx-proxy3 -> 172.20.0.5" in out
    assert not _warning_lines(out), out
