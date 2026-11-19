"""Hard bounds of a task show request (#202)."""
import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import janitor, runtime, sessions, tasks


class _Conn:
    def __init__(self, hang_rollback=False):
        self.terminated = False
        self.hang_rollback = hang_rollback

    async def execute(self, *a):
        await asyncio.sleep(30)

    fetchrow = execute

    def transaction(self):
        return self

    async def start(self):
        pass

    async def rollback(self):
        await asyncio.sleep(30)

    def terminate(self):
        self.terminated = True


class _Pool:
    def __init__(self, conn=None, hang_acquire=False):
        self.conn = conn or _Conn()
        self.hang_acquire = hang_acquire
        self.released = 0
        self.acquire_timeout = None
        self.release_timeout = None
        self.fail_release = False

    async def acquire(self, timeout=None):
        self.acquire_timeout = timeout
        if self.hang_acquire:
            await asyncio.sleep(30)
        return self.conn

    async def release(self, conn, timeout=None):
        self.released += 1
        self.release_timeout = timeout
        if self.fail_release:
            raise RuntimeError("reset failed")


def _ctx(pool):
    return SimpleNamespace(
        pool=pool, student="s", sid="x", lab=SimpleNamespace(id="l"), started=time.monotonic()
    )


def _run(pool):
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks._do_show(_ctx(pool), SimpleNamespace(id="t", kind="quiz")))
    return exc.value.status_code, time.monotonic() - t0


def test_slow_statement_is_cut_off_at_the_bound_and_the_connection_terminated(monkeypatch):
    monkeypatch.setattr(tasks, "DB_STEP_BOUND_S", 0.2)
    pool = _Pool()
    status, took = _run(pool)
    assert status == 503 and took < 1
    assert pool.conn.terminated and pool.released == 1  # no wait for the hanging ROLLBACK
    assert pool.acquire_timeout == 0.2


def test_busy_pool_is_cut_off_at_the_bound(monkeypatch):
    monkeypatch.setattr(tasks, "DB_STEP_BOUND_S", 0.2)
    status, took = _run(_Pool(hang_acquire=True))
    assert status == 503 and took < 1


def test_server_side_bounds_are_below_the_step_bound():
    assert 0 < tasks.DB_SERVER_BOUND_S < tasks.DB_STEP_BOUND_S


def test_request_budget_is_under_nginx_and_covers_the_parts():
    assert tasks.REQUEST_BUDGET_S < 60
    assert tasks.SETUP_TIMEOUT_MAX_S + runtime.KILL_BOUND_S + tasks.DB_STEP_BOUND_S < 60


class _Rt:
    def __init__(self):
        self.timeout = None

    async def exec_script(self, name, script, timeout):
        self.timeout = timeout
        return SimpleNamespace(timed_out=False, exit_code=0, output="")


def _hands_on(tmp_path, started):
    d = tmp_path / "tasks" / "t"
    d.mkdir(parents=True)
    (d / "setup.sh").write_bytes(b"true\n")
    rt = _Rt()
    ctx = SimpleNamespace(
        pool=_Pool(),
        student="s",
        sid="x",
        lab=SimpleNamespace(id="l", path=tmp_path),
        started=started,
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=rt))),
    )
    return ctx, rt


def test_setup_timeout_env_is_clamped_in_what_exec_script_receives(monkeypatch, tmp_path):
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", "3600")
    ctx, rt = _hands_on(tmp_path, time.monotonic())
    with pytest.raises(HTTPException):  # the fake pool then fails; only the timeout matters
        asyncio.run(tasks._do_show(ctx, SimpleNamespace(id="t", kind="hands-on")))
    assert rt.timeout <= tasks.SETUP_TIMEOUT_MAX_S


def test_setup_timeout_shrinks_with_the_time_already_spent(monkeypatch, tmp_path):
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", "40")
    ctx, rt = _hands_on(tmp_path, time.monotonic() - 30)  # 30 s gone in auth and queueing
    with pytest.raises(HTTPException):
        asyncio.run(tasks._do_show(ctx, SimpleNamespace(id="t", kind="hands-on")))
    left = tasks.REQUEST_BUDGET_S - 30 - runtime.KILL_BOUND_S - tasks.DB_STEP_BOUND_S
    assert rt.timeout == pytest.approx(left, abs=1)


def test_no_time_left_answers_setup_timeout_without_running_the_script(tmp_path):
    ctx, rt = _hands_on(tmp_path, time.monotonic() - 50)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks._do_show(ctx, SimpleNamespace(id="t", kind="hands-on")))
    assert exc.value.status_code == 504 and exc.value.detail["code"] == "setup_timeout"
    assert rt.timeout is None


def test_a_hanging_kill_is_cut_off_at_the_kill_bound(monkeypatch):
    class Hangs(runtime.DockerRuntime):
        async def _run_exec(self, name, script, create, out, ids):
            ids.append("x")
            await asyncio.sleep(30)

        async def _kill(self, name, argv, exec_id):
            await asyncio.sleep(30)

    monkeypatch.setattr(runtime, "KILL_BOUND_S", 0.2)
    t0 = time.monotonic()
    res = asyncio.run(Hangs().exec_script("c", "true", 0.1))
    assert res.timed_out and time.monotonic() - t0 < 1.5


class _Commit(_Conn):
    async def execute(self, *a):
        pass

    async def fetchrow(self, *a):
        return {"shown_at": None, "passed_at": None}

    async def commit(self):
        pass


def test_a_failed_release_after_commit_does_not_fail_the_saved_change():
    pool = _Pool(_Commit())
    pool.fail_release = True
    row = asyncio.run(tasks._record_shown(_ctx(pool), SimpleNamespace(id="t", kind="quiz")))
    assert row["passed_at"] is None and pool.released == 1
    assert 0 < pool.release_timeout <= tasks.DB_STEP_BOUND_S


def test_the_db_step_bound_is_the_smaller_of_the_step_bound_and_the_time_left():
    pool = _Pool(_Commit())
    asyncio.run(tasks._record_shown(_ctx(pool), SimpleNamespace(id="t"), 1.5))
    assert pool.acquire_timeout == 1.5


def test_start_is_a_coroutine_function_and_sets_started_at_once():
    assert asyncio.iscoroutinefunction(tasks._start)
    request = SimpleNamespace(state=SimpleNamespace())
    asyncio.run(tasks._start(request))
    assert request.state.started <= time.monotonic()


def test_show_with_a_nearly_spent_budget_bounds_the_slot_wait(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks, "_EXECS", asyncio.Semaphore(0))
    ctx, _ = _hands_on(tmp_path, time.monotonic() - tasks.REQUEST_BUDGET_S + 0.3)
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks._do_show(ctx, SimpleNamespace(id="t", kind="hands-on")))
    assert exc.value.status_code == 504 and time.monotonic() - t0 < 2


def _check_ctx(tmp_path, spent):
    d = tmp_path / "tasks" / "t"
    d.mkdir(parents=True)
    (d / "check.sh").write_bytes(b"true" + bytes([10]))
    rt = _Rt()
    ctx = SimpleNamespace(
        sid="x",
        lab=SimpleNamespace(id="l", path=tmp_path),
        started=time.monotonic() - spent,
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=rt))),
    )
    return ctx, rt


def test_check_timeout_plus_kill_plus_db_step_stays_in_the_request_budget(tmp_path):
    ctx, rt = _check_ctx(tmp_path, 30)
    asyncio.run(tasks._run_check(ctx, SimpleNamespace(id="t")))
    total = rt.timeout + runtime.KILL_BOUND_S + tasks.DB_STEP_BOUND_S
    assert total <= tasks.REQUEST_BUDGET_S - 30 + 1
    ctx, rt = _check_ctx(tmp_path / "fresh", 0)
    asyncio.run(tasks._run_check(ctx, SimpleNamespace(id="t")))
    assert rt.timeout == tasks.CHECK_TIMEOUT_S


def test_check_with_no_time_left_runs_nothing(tmp_path):
    ctx, rt = _check_ctx(tmp_path, tasks.REQUEST_BUDGET_S)
    reason, _, ok = asyncio.run(tasks._run_check(ctx, SimpleNamespace(id="t")))
    assert reason == "error" and not ok and rt.timeout is None


def test_pool_command_timeout_is_below_nginx_read_timeout():
    from app import db

    assert db.POOL_COMMAND_TIMEOUT < 60


def test_sql_end_reason_literals_are_allowed():
    case = re.search(r"case(.*?)end as reason", janitor._DUE, re.S | re.I).group(1)
    found = set(re.findall(r"then '([a-z_]+)'", case)) | set(re.findall(r"else '([a-z_]+)'", case))
    assert found and found <= sessions.END_REASONS
    src = Path(sessions.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r"end_reason = '([a-z_]+)'", src))
    assert "orphan" in written and written <= sessions.END_REASONS


def _attempt_ctx(spent=0.0):
    return SimpleNamespace(
        pool=None,
        student="s",
        sid="x",
        lab=SimpleNamespace(id="l"),
        started=time.monotonic() - spent,
    )


class _AttemptConn(_Commit):
    async def fetchrow(self, *a):
        return None  # no shown row: the step ends at once, no attempt

    async def fetchval(self, *a):
        return "running"


def test_count_attempt_with_a_hanging_acquire_returns_within_the_bound(monkeypatch):
    monkeypatch.setattr(tasks, "DB_STEP_BOUND_S", 0.2)
    ctx = _attempt_ctx()
    ctx.pool = _Pool(hang_acquire=True)
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks._count_attempt(ctx, SimpleNamespace(id="t"), True))
    assert exc.value.status_code == 503 and time.monotonic() - t0 < 1
    assert ctx.pool.acquire_timeout == 0.2


class _CountConn(_AttemptConn):
    async def fetchrow(self, *a):
        return {"passed_at": None, "secs": 5}

    async def fetchval(self, *a):
        return 3


def test_count_attempt_survives_a_failing_release_after_commit(monkeypatch):
    monkeypatch.setattr(tasks, "fast_task_reason", lambda *a: None)
    ctx = _attempt_ctx()
    ctx.pool = _Pool(_CountConn())
    ctx.pool.fail_release = True
    assert asyncio.run(tasks._count_attempt(ctx, SimpleNamespace(id="t"), False)) == 3
    assert ctx.pool.release_timeout <= tasks.DB_STEP_BOUND_S


def test_count_attempt_terminates_the_connection_when_the_bound_fires(monkeypatch):
    monkeypatch.setattr(tasks, "DB_STEP_BOUND_S", 0.2)
    ctx = _attempt_ctx()
    ctx.pool = _Pool()  # its statements hang
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks._count_attempt(ctx, SimpleNamespace(id="t"), True))
    assert exc.value.status_code == 503 and time.monotonic() - t0 < 1
    assert ctx.pool.conn.terminated


def test_count_attempt_with_no_time_left_is_the_json_504_and_touches_no_pool():
    ctx = _attempt_ctx(tasks.REQUEST_BUDGET_S + 1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks._count_attempt(ctx, SimpleNamespace(id="t"), True))
    assert exc.value.status_code == 504 and exc.value.detail["code"] == "check_timeout"  # #224


def test_gradable_read_is_bounded_by_the_time_left():
    ctx = _attempt_ctx(tasks.REQUEST_BUDGET_S + 1)
    task = SimpleNamespace(id="t", kind="quiz")
    ctx.task = lambda _id: task
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks._gradable(ctx, "t", "quiz"))
    assert exc.value.status_code == 504 and exc.value.detail["code"] == "check_timeout"  # #224


def test_the_pool_is_created_with_the_pool_command_timeout(monkeypatch):
    from app import db

    seen = {}

    async def fake_create_pool(dsn, **kw):
        seen.update(kw)

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    asyncio.run(db.create_pool("postgresql://x"))
    assert seen["command_timeout"] == db.POOL_COMMAND_TIMEOUT
