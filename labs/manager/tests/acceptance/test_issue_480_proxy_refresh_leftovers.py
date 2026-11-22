"""Exam for #480: leftovers of the trusted proxy refresh (#475, D135).

Contract added here:
- A background refresh whose worker thread cannot be started leaves nothing stuck: a later call
  starts a lookup again.
- A reset of the cache between a refresh being scheduled and its worker resolving makes that
  worker's answer void: it is not written into the cache.
- The lookup done during startup counts as a lookup in flight: a request meanwhile never starts a
  second, parallel lookup.
- deploy.sh prints no bogus `WARNING: trusted proxy lookup did not resolve` for main.py's own
  startup-failure line, and names the proxy correctly when the log line has a prefix.
Seams are those of the #475 exam: `socket.getaddrinfo`, `time.monotonic`, `threading.Thread.start`.
"""

import asyncio
import re
import socket
import threading
import time
from pathlib import Path

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


async def _until(cond, seconds: float) -> bool:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return cond()


def _flaky_start(real_start):
    state = {"raised": 0}

    def flaky(self, *args, **kwargs):
        if not state["raised"]:
            state["raised"] += 1
            raise RuntimeError("can't start new thread")
        return real_start(self, *args, **kwargs)

    return flaky, state


# ============================================================ 1. a thread that cannot start


def test_a_refresh_whose_thread_cannot_start_does_not_wedge_the_cache(monkeypatch):
    fake = Resolver([A])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    real_start = threading.Thread.start
    flaky, state = _flaky_start(real_start)

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", flaky)
        try:
            auth.client_ip(_req(A, REAL))  # may raise or not; the exam allows both
        except RuntimeError:
            pass
        assert state["raised"] == 1, "the exam expected the first refresh to start a thread"
        monkeypatch.setattr(threading.Thread, "start", real_start)
        # later calls, in the same running loop, must start a lookup
        return await _until(lambda: auth.client_ip(_req(A, f"9.9.9.9, {REAL}")) == REAL, 5)

    trusted = asyncio.run(scenario())
    assert fake.calls, "after a failed thread start no lookup was ever started again"
    assert trusted, "after a failed thread start the proxy was never trusted: the cache is wedged"


def test_an_expired_entry_is_refreshed_after_one_failed_thread_start(monkeypatch):
    clock = [0.0]  # an offset on the real clock: the event loop needs a clock that still runs
    real_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + clock[0])
    fake = Resolver([A], [B])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert auth.client_ip(_req(A, REAL)) == REAL  # sync: inline lookup, entry cached with A
    clock[0] += 61
    real_start = threading.Thread.start
    flaky, _state = _flaky_start(real_start)

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", flaky)
        try:
            auth.client_ip(_req(A, REAL))
        except RuntimeError:
            pass
        monkeypatch.setattr(threading.Thread, "start", real_start)
        return await _until(lambda: auth.client_ip(_req(B, f"9.9.9.9, {REAL}")) == REAL, 5)

    assert asyncio.run(scenario()), "the old address stayed trusted: the refresh never ran again"


# ============================================================ 2. reset between capture and write


def test_a_reset_while_a_refresh_is_scheduled_voids_that_workers_answer(monkeypatch):
    """The reset lands after the refresh was scheduled and before its worker starts resolving."""
    fake = Resolver([A])
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    real_start = threading.Thread.start
    started: list[threading.Thread] = []

    def reset_then_start(self, *args, **kwargs):
        auth.reset_trusted_proxy_cache()  # a reset under the worker's feet
        started.append(self)
        return real_start(self, *args, **kwargs)

    async def scenario():
        monkeypatch.setattr(threading.Thread, "start", reset_then_start)
        auth.client_ip(_req(A, REAL))  # in a loop: schedules a background refresh
        monkeypatch.setattr(threading.Thread, "start", real_start)
        assert started, "the exam expected a worker thread to be started"
        for t in started:
            await asyncio.get_running_loop().run_in_executor(None, t.join, 5)

    asyncio.run(scenario())
    before = len(fake.calls)
    # No loop here: a cache the old worker wrote into would answer without a lookup.
    assert auth.client_ip(_req(A, f"9.9.9.9, {REAL}")) == REAL
    assert len(fake.calls) == before + 1, (
        "the pre-reset worker wrote its answer into the cache after the reset"
    )


# The gated-resolver case that was here reset inside getaddrinfo, after both generation reads,
# so it could not catch the old double read. Its replacement is in
# test_issue_488_trusted_proxy_hardening.


# ============================================================ 3. startup lookup in flight


def test_a_request_during_the_startup_lookup_does_not_start_a_parallel_lookup(
    monkeypatch, no_janitor
):
    from app.main import app

    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    gate = threading.Event()
    lock = threading.Lock()
    flight = {"now": 0, "max": 0, "calls": 0}

    def slow(host, port=None, *args, **kwargs):
        with lock:
            flight["calls"] += 1
            flight["now"] += 1
            flight["max"] = max(flight["max"], flight["now"])
        try:
            gate.wait(1.0)
            return _answer(A)
        finally:
            with lock:
                flight["now"] -= 1

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    lifespan = app.router.lifespan_context(app)

    async def scenario():
        starter = asyncio.create_task(lifespan.__aenter__())
        try:
            await asyncio.sleep(0.3)  # the startup lookup is in the resolver
            for _ in range(3):
                auth.client_ip(_req(A, f"9.9.9.9, {REAL}"))  # requests meanwhile
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.2)
            gate.set()
            await asyncio.wait_for(starter, 6)
            return await _until(lambda: auth.client_ip(_req(A, REAL)) == REAL, 5)
        finally:
            gate.set()
            if not starter.done():
                await asyncio.wait_for(starter, 6)
            await lifespan.__aexit__(None, None, None)

    trusted = asyncio.run(scenario())
    assert flight["max"] <= 1, f"{flight['max']} lookups were in flight at once"
    assert flight["calls"] == 1, f"{flight['calls']} lookups for one startup (expected 1)"
    assert trusted, "once the lookup finished the proxy must be trusted"


# ============================================================ 4. deploy.sh log parsing

MAIN_PY = Path(__file__).resolve().parents[2] / "app" / "main.py"


def _startup_failure_line() -> str:
    """The text main.py logs when the startup lookup raises (read from the source)."""
    src = MAIN_PY.read_text(encoding="utf-8")
    m = re.search(r'log\.warning\(\s*"(trusted proxy[^"]*)"\s*\)', src)
    assert m, "main.py no longer logs a 'trusted proxy' line when the startup lookup fails"
    return m.group(1)


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


def test_deploy_prints_no_bogus_warning_when_the_last_line_is_the_startup_failure_line(deploy):
    line = _startup_failure_line()
    r = deploy.run(_stdin(), STUB_PROXY_LINE=line)
    _ok(r)
    out = r.stdout + r.stderr
    bogus = [x for x in _warning_lines(out) if "trusted proxy" in x and "did not resolve" in x]
    assert not bogus, f"bogus warning for main.py's own line {line!r}: {bogus}"
    assert "deployed lf-manager" in out


def test_the_startup_failure_line_after_a_good_line_is_not_reported_as_a_proxy_name(deploy):
    lines = "trusted proxy nginx-proxy3 -> 172.20.0.5\n" + _startup_failure_line()
    r = deploy.run(_stdin(), STUB_PROXY_LINE=lines)
    _ok(r)
    out = r.stdout + r.stderr
    bogus = [x for x in _warning_lines(out) if re.search(r"trusted proxy (lookup|failed)\b", x)]
    assert not bogus, bogus


@pytest.mark.parametrize(
    "line",
    [
        "WARNING app.auth: trusted proxy nginx-proxy3 -> (none)",
        "2026-11-22 10:00:00,123 INFO app.auth trusted proxy nginx-proxy3: lookup failed "
        "([Errno -2] Name or service not known); nobody trusted",
    ],
    ids=["logger-prefix-no-address", "timestamp-prefix-lookup-failed"],
)
def test_deploy_names_the_proxy_correctly_when_the_log_line_has_a_prefix(deploy, line):
    r = deploy.run(_stdin(), STUB_PROXY_LINE=line)
    _ok(r)
    out = r.stdout + r.stderr
    warn = [x for x in _warning_lines(out) if "did not resolve" in x]
    assert warn, f"no 'did not resolve' warning for a failed lookup:\n{out}"
    assert any("trusted proxy nginx-proxy3 did not resolve" in x for x in warn), warn
