import asyncio
import time

import httpx
import pytest

from app import sessions
from app.runtime import DockerRuntime, ExecResult


def _runtime(results):
    rt = DockerRuntime.__new__(DockerRuntime)
    calls = []

    async def exec_script(name, script, timeout):
        calls.append((name, script))
        r = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    async def running(name):
        return True

    rt.exec_script = exec_script  # type: ignore[method-assign]
    rt._is_running = running  # type: ignore[method-assign]
    return rt, calls


def test_ready_once_ttyd_runs():
    rt, calls = _runtime([ExecResult(1, ""), RuntimeError("down"), ExecResult(0, "")])
    asyncio.run(rt.wait_ready("lab-x", 5))
    assert len(calls) == 3
    assert "ttyd" in calls[0][1]


def test_never_ready_times_out():
    rt, _ = _runtime([ExecResult(1, "")])
    with pytest.raises(TimeoutError):
        asyncio.run(rt.wait_ready("lab-x", 0.5))


def test_runtime_without_wait_ready_is_ready():
    asyncio.run(sessions.wait_ready(object(), "lab-x", time.monotonic() + 5))


def test_wait_gets_only_what_is_left_of_the_deadline():
    seen = []

    class R:
        async def wait_ready(self, name, timeout):
            seen.append(timeout)

    asyncio.run(sessions.wait_ready(R(), "lab-x", time.monotonic() + 2))
    assert 0 < seen[0] <= 2
    with pytest.raises(TimeoutError):
        asyncio.run(sessions.wait_ready(R(), "lab-x", time.monotonic() - 1))
    assert len(seen) == 1
    assert sessions.READY_TIMEOUT_MAX_S < 60


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (lambda r: httpx.Response(404), False),
        (lambda r: httpx.Response(200, json={"State": {"Running": False}}), False),
        (lambda r: httpx.Response(200, json={"State": {"Running": True}}), True),
        (lambda r: httpx.Response(500), True),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")), True),
    ],
)
def test_is_running_keeps_waiting_unless_gone_or_stopped(handler, expected):
    rt = DockerRuntime.__new__(DockerRuntime)
    rt._client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), base_url="http://docker"
    )
    assert asyncio.run(rt._is_running("lab-x")) is expected


def test_gone_container_stops_the_wait_at_once():
    rt, calls = _runtime([ExecResult(1, "")])

    async def gone(name):
        return False

    rt._is_running = gone  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        asyncio.run(rt.wait_ready("lab-x", 30))
    assert len(calls) == 1


class _Pool:
    def __init__(self, deleted):
        self.deleted, self.sql = deleted, []

    async def fetchrow(self, sql, *a):
        return {
            "id": "11111111-1111-1111-1111-111111111111",
            "lab_id": "l",
            "status": "starting",
            "expires_at": None,
        }

    async def execute(self, sql, *args):
        self.sql.append(sql)
        return self.deleted


def _failing_start(monkeypatch, deleted):
    from types import SimpleNamespace

    from fastapi import HTTPException, Response

    pool = _Pool(deleted)
    removed = []

    class RT:
        async def start(self, spec):
            return "lab-x"

        async def wait_ready(self, name, timeout):
            raise RuntimeError("gone")

        async def remove(self, name):
            removed.append(name)

    lab = SimpleNamespace(id="l", status="public", time_limit_min=5)
    monkeypatch.setattr(sessions, "container_spec", lambda lab, sid: {})
    monkeypatch.setattr(sessions, "memory_limit_mb", lambda lab: 1)
    monkeypatch.setattr(sessions.admission, "resources_ok", lambda mb: True)

    async def ok(*a):
        return True

    async def none(*a):
        return None

    monkeypatch.setattr(sessions.admission, "cap_ok", ok)
    monkeypatch.setattr(sessions, "_live", none)
    state = SimpleNamespace(labs={"l": lab}, pool=pool, runtime=RT())
    req = SimpleNamespace(app=SimpleNamespace(state=state))
    p = SimpleNamespace(subject="s")

    async def go():
        return await sessions.start(sessions.StartBody(lab_id="l"), req, Response(), p)

    return go, pool, removed, HTTPException


def test_failed_start_removes_container_deletes_starting_row_502(monkeypatch):
    go, pool, removed, HTTPException = _failing_start(monkeypatch, "DELETE 1")
    with pytest.raises(HTTPException) as e:
        asyncio.run(go())
    assert e.value.status_code == 502 and e.value.detail == "start_failed"
    assert removed and "status = 'starting'" in pool.sql[0]


def test_failed_start_of_an_ended_session_is_409(monkeypatch):
    go, _, removed, HTTPException = _failing_start(monkeypatch, "DELETE 0")
    with pytest.raises(HTTPException) as e:
        asyncio.run(go())
    assert e.value.status_code == 409 and removed


def test_slow_start_over_budget_answers_502_within_budget_and_removes(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "1")
    go, pool, removed, HTTPException = _failing_start(monkeypatch, "DELETE 1")

    class Hang:
        async def start(self, spec):
            await asyncio.sleep(30)
            return "x"

        async def remove(self, name):
            removed.append(name)

    # swap the runtime of the prepared state
    closure = go.__closure__
    req = next(c.cell_contents for c in closure if hasattr(c.cell_contents, "app"))
    req.app.state.runtime = Hang()
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as e:
        asyncio.run(go())
    assert e.value.status_code == 502 and e.value.detail == "start_failed"
    assert time.monotonic() - t0 < 2.5
    assert removed and "status = 'starting'" in pool.sql[0]


def test_cleanup_is_bounded_and_still_answers_502(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "1")
    monkeypatch.setattr(sessions, "CLEANUP_BOUND_S", 0.3)
    go, pool, removed, HTTPException = _failing_start(monkeypatch, "DELETE 1")

    class Stuck:
        async def start(self, spec):
            return "x"

        async def wait_ready(self, name, timeout):
            raise RuntimeError("gone")

        async def remove(self, name):
            await asyncio.sleep(30)

    req = next(c.cell_contents for c in go.__closure__ if hasattr(c.cell_contents, "app"))
    req.app.state.runtime = Stuck()
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as e:
        asyncio.run(go())
    assert e.value.status_code == 502
    assert time.monotonic() - t0 < 1.5
