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

    async def acquire(self, timeout=None):
        self.acquire_timeout = timeout
        if self.hang_acquire:
            await asyncio.sleep(30)
        return self.conn

    async def release(self, conn):
        self.released += 1


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


def test_kill_bound_in_the_runtime_is_the_constant():
    src = Path(runtime.__file__).read_text(encoding="utf-8")
    assert "min(KILL_BOUND_S," in src
    assert not re.search(r"min\(\s*\d", src)


def test_sql_end_reason_literals_are_allowed():
    case = re.search(r"case(.*?)end as reason", janitor._DUE, re.S | re.I).group(1)
    found = set(re.findall(r"then '([a-z_]+)'", case)) | set(re.findall(r"else '([a-z_]+)'", case))
    assert found and found <= sessions.END_REASONS
    src = Path(sessions.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r"end_reason = '([a-z_]+)'", src))
    assert "orphan" in written and written <= sessions.END_REASONS
