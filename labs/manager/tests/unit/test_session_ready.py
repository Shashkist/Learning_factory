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
    # the worst failed start (budget + the two cleanup bounds) keeps a margin to nginx's 60 s
    assert sessions.READY_TIMEOUT_MAX_S + 2 * sessions.CLEANUP_BOUND_S < 60


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
        self.deleted, self.sql, self.status = deleted, [], "ended"

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

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if "set status = 'ended'" in sql:  # ends only a live row, like `where ... and LIVE`
            if self.status not in ("starting", "running"):
                return None
            self.status = "ended"
            return args[0]
        return self.status


class _RT:
    """Runtime fake: start/wait_ready/remove behaviour is replaced per test by subclassing."""

    def __init__(self, removed):
        self.removed = removed

    async def start(self, spec):
        return "lab-x"

    async def wait_ready(self, name, timeout):
        raise RuntimeError("gone")

    async def remove(self, name):
        self.removed.append(name)


def _env(monkeypatch, deleted="DELETE 1", runtime=None, live=None):
    """Fakes shared by the direct and the TestClient tests: (state, pool, removed)."""
    from types import SimpleNamespace

    pool = _Pool(deleted)
    removed = []
    lab = SimpleNamespace(id="l", status="public", time_limit_min=5, title={})
    monkeypatch.setattr(sessions, "container_spec", lambda lab, sid: {})
    monkeypatch.setattr(sessions, "memory_limit_mb", lambda lab: 1)
    monkeypatch.setattr(sessions.admission, "resources_ok", lambda mb: True)

    async def ok(*a):
        return True

    async def fake_live(*a):
        return live

    monkeypatch.setattr(sessions.admission, "cap_ok", ok)
    monkeypatch.setattr(sessions, "_live", fake_live)
    state = SimpleNamespace(labs={"l": lab}, pool=pool, runtime=runtime or _RT(removed))
    return state, pool, removed


def _call_start(state):
    from types import SimpleNamespace

    from fastapi import Response

    req = SimpleNamespace(
        app=SimpleNamespace(state=state), state=SimpleNamespace(started=time.monotonic())
    )
    p = SimpleNamespace(subject="s")
    return sessions.start(sessions.StartBody(lab_id="l"), req, Response(), p)


def _answer(state):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        asyncio.run(_call_start(state))
    return e.value


def test_failed_start_removes_container_deletes_starting_row_502(monkeypatch):
    state, pool, removed = _env(monkeypatch, "DELETE 1")
    e = _answer(state)
    assert e.status_code == 502 and e.detail == "start_failed"
    assert removed and "status = 'starting'" in pool.sql[0]


def test_failed_start_of_an_ended_session_is_409(monkeypatch):
    state, _, removed = _env(monkeypatch, "DELETE 0")
    assert _answer(state).status_code == 409 and removed


def test_committed_final_update_is_ended_and_answers_502_not_409(monkeypatch):
    state, pool, removed = _env(monkeypatch, "DELETE 0")
    pool.status = "running"  # the deadline cancelled the update after the server committed it
    e = _answer(state)
    assert (e.status_code, e.detail) == (502, "start_failed")
    assert pool.status == "ended" and removed == ["lab-11111111-1111-1111-1111-111111111111"]
    assert any("end_reason = 'orphan'" in q for q in pool.sql)  # an allowed reason (migration 003)


def test_failed_end_after_delete_0_answers_502_and_still_removes(monkeypatch):
    state, pool, removed = _env(monkeypatch, "DELETE 0")

    async def broken(sql, *args):
        raise ConnectionError("database dropped")

    pool.fetchval = broken
    e = _answer(state)
    assert (e.status_code, e.detail) == (502, "start_failed")
    assert removed


def test_row_step_shares_one_bound_and_the_answer_stays_under_nginx(monkeypatch):
    monkeypatch.setattr(sessions, "CLEANUP_BOUND_S", 0.3)
    state, pool, removed = _env(monkeypatch, "DELETE 0")

    async def slow_delete(sql, *args):
        await asyncio.sleep(0.25)
        return "DELETE 0"

    async def slow_end(sql, *args):
        await asyncio.sleep(30)

    pool.execute, pool.fetchval = slow_delete, slow_end
    t0 = time.monotonic()
    assert _answer(state).status_code == 502
    # The removal is instant, so only the row step's one 0.3 s bound may elapse. A separate bound
    # for the update would take at least 0.25 + 0.3 s (sleeps end at most one clock tick
    # early), above the limit.
    assert removed and time.monotonic() - t0 < 0.3 + 0.2


def test_every_end_reason_the_app_writes_is_allowed_by_the_schema():
    import re
    from pathlib import Path

    root = Path(sessions.__file__).resolve().parents[1]
    checks = [  # the newest migration that sets the constraint wins
        m.group(1)
        for f in sorted((root / "migrations").glob("*.sql"))
        for m in re.finditer(r"end_reason in \(([^)]*)\)", f.read_text(encoding="utf-8"))
    ]
    allowed = set(re.findall(r"'(\w+)'", checks[-1]))
    used, computed = set(), set()
    for f in (root / "app").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        used |= set(re.findall(r"end_reason = '(\w+)'", text))
        used |= set(re.findall(r"end_session\([^)]*?,\s*[\"'](\w+)[\"']\s*\)", text, re.S))
        # reasons computed in SQL (the janitor's `case ... end as reason`, passed to end_session)
        for case in re.findall(r"\bcase\b(.*?)\bend as reason\b", text, re.S):
            computed |= set(re.findall(r"\b(?:then|else)\s+'(\w+)'", case))
    assert computed, "the janitor's `case ... end as reason` was not found"
    used |= computed
    assert used <= allowed, used - allowed


def test_slow_start_over_budget_answers_502_within_budget_and_removes(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "1")
    removed = []

    class Hang(_RT):
        async def start(self, spec):
            await asyncio.sleep(30)

    state, pool, _ = _env(monkeypatch, runtime=Hang(removed))
    t0 = time.monotonic()
    e = _answer(state)
    assert e.status_code == 502 and e.detail == "start_failed"
    assert time.monotonic() - t0 < 2.5
    assert removed and "status = 'starting'" in pool.sql[0]


def test_stuck_remove_still_deletes_the_row_first_and_answers_502(monkeypatch):
    monkeypatch.setattr(sessions, "CLEANUP_BOUND_S", 0.3)

    class Stuck(_RT):
        async def remove(self, name):
            await asyncio.sleep(30)

    state, pool, _ = _env(monkeypatch, runtime=Stuck([]))
    t0 = time.monotonic()
    e = _answer(state)
    assert e.status_code == 502
    assert pool.sql and "status = 'starting'" in pool.sql[0]
    assert time.monotonic() - t0 < 1.5


def test_stuck_row_delete_is_bounded_and_the_container_is_still_removed(monkeypatch):
    monkeypatch.setattr(sessions, "CLEANUP_BOUND_S", 0.3)
    state, pool, removed = _env(monkeypatch)

    async def hang(sql, *args):
        await asyncio.sleep(30)

    pool.execute = hang
    t0 = time.monotonic()
    assert _answer(state).status_code == 502
    assert removed and time.monotonic() - t0 < 1.5


def test_failed_row_delete_still_removes_the_container_and_answers_502(monkeypatch):
    state, pool, removed = _env(monkeypatch)

    async def broken(sql, *args):
        raise ConnectionError("database dropped")

    pool.execute = broken
    e = _answer(state)
    assert (e.status_code, e.detail) == (502, "start_failed")
    assert removed


def test_db_call_over_the_budget_answers_502_not_503(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "0.5")
    state, pool, _ = _env(monkeypatch)

    async def slow(sql, *a):
        await asyncio.sleep(30)

    pool.fetchrow = slow  # the insert
    t0 = time.monotonic()
    e = _answer(state)
    assert e.status_code == 502 and e.detail == "start_failed"
    assert time.monotonic() - t0 < 1.5
    assert not pool.sql  # no row was inserted: nothing to delete


def _client(monkeypatch, runtime=None, live=None):
    """The real route through TestClient, so the _stamp_request dependency runs."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    state, pool, removed = _env(monkeypatch, runtime=runtime, live=live)
    app = FastAPI()
    app.include_router(sessions.router)
    app.state.labs, app.state.pool, app.state.runtime = state.labs, pool, state.runtime

    async def student():
        return SimpleNamespace(subject="s", kind="student")

    app.dependency_overrides[sessions.current_student] = student
    return TestClient(app), pool, removed


def test_route_slow_start_answers_502_within_the_budget(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "1")

    class Hang(_RT):
        async def start(self, spec):
            await asyncio.sleep(30)

    client, pool, _ = _client(monkeypatch, Hang([]))
    t0 = time.monotonic()
    r = client.post("/api/sessions", json={"lab_id": "l"})
    assert r.status_code == 502 and r.json()["detail"] == "start_failed"
    assert time.monotonic() - t0 < 1 + 0.5
    assert "status = 'starting'" in pool.sql[0]


def test_route_slow_forced_end_answers_502_within_the_budget(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "1")
    old = {"id": "22222222-2222-2222-2222-222222222222", "lab_id": "other"}

    async def slow_end(pool, runtime, sid, reason):
        await asyncio.sleep(30)

    monkeypatch.setattr(sessions, "end_session", slow_end)
    client, pool, _ = _client(monkeypatch, live=old)
    t0 = time.monotonic()
    r = client.post("/api/sessions", json={"lab_id": "l", "force": True})
    assert r.status_code == 502 and r.json()["detail"] == "start_failed"
    assert time.monotonic() - t0 < 1 + 0.5
    assert not pool.sql  # nothing inserted, nothing to delete


def test_route_stamps_the_request_before_authentication(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "5")
    seen = []

    class R(_RT):
        async def wait_ready(self, name, timeout):
            seen.append(timeout)

    client, _, _ = _client(monkeypatch, R([]))

    async def slow_student():
        from types import SimpleNamespace

        await asyncio.sleep(0.3)
        return SimpleNamespace(subject="s", kind="student")

    client.app.dependency_overrides[sessions.current_student] = slow_student
    client.post("/api/sessions", json={"lab_id": "l"})
    assert seen and seen[0] < 5 - 0.25  # the 0.3 s of auth came out of the wait


def test_db_busy_after_the_insert_still_cleans_up_and_answers_503(monkeypatch):
    from fastapi import HTTPException

    class Ready(_RT):
        async def wait_ready(self, name, timeout):
            pass

    state, pool, removed = _env(monkeypatch, "DELETE 1", runtime=Ready([]))
    removed = state.runtime.removed

    async def busy(sql, *args):  # the final 'running' update: the pool is exhausted
        raise HTTPException(503, {"code": "db_busy", "message": "busy"})

    pool.fetchval = busy
    e = _answer(state)
    assert e.status_code == 503 and e.detail["code"] == "db_busy"
    assert removed and "status = 'starting'" in pool.sql[0]  # the row deleted, container removed
