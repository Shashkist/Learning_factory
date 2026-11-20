"""Acceptance tests for issue #224: check path read bounds, `check_timeout`, pool acquire timeout.

No Docker, no database. Fakes stand in for the asyncpg pool and connection, as in
tests/unit/test_task_db_step_bound.py. The task functions are called with a real `tasks._Ctx`.

1. Single reads on the check/answer path (`_gradable`'s "was the task shown" read, the "attempts
   so far" read of a Check that failed on the server, `_count_attempt`'s session-status read) and
   the navigation guard read of `_show` go through the bounded step contract of `_step` /
   `_step_bound` (D67): acquire with a timeout no larger than the step bound, the read, and the
   release all fit in the bound; a connection whose read hit the bound is terminated, never
   released as healthy; the call raises 503 (db_guard) within the bound. The pool's own
   fetch/fetchrow/fetchval (which wait, shielded, for the cancel and the reset without limit)
   are not used for these reads.
2. A request with no time left on the check or answer path answers 504 with detail
   {"code": "check_timeout", "message": <text>}. The show/navigation path keeps `setup_timeout`.
3. Pool-wide acquire timeout: `app.db.POOL_ACQUIRE_TIMEOUT` (seconds, 0 < t < 60, nginx's read
   timeout) is the longest a request-path read waits for a free connection. With every
   connection held, the task list and current-task reads fail with 503 instead of waiting.
   Request-path reads pass an acquire timeout (they use `pool.acquire(timeout=...)`, directly or
   through a helper); `pool.fetch*` alone waits for a free connection without a limit.
"""
import asyncio
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import db, tasks
from app.content import Lab, Task

SID = uuid.UUID("00000000-0000-0000-0000-000000000001")
BOUND = 0.2
SHOWN = {"shown_at": "2026-11-19T09:00:00Z", "passed_at": None}


class Conn:
    def __init__(self, row=None, val=None, hang=()):
        self.row, self.val, self.hang = row, val, set(hang)
        self.terminated = False

    async def _go(self, name, result):
        if name in self.hang:
            await asyncio.sleep(30)
        return result

    async def execute(self, *a, **k):
        return "OK"

    async def fetchrow(self, *a, **k):
        return await self._go("fetchrow", self.row)

    async def fetchval(self, *a, **k):
        return await self._go("fetchval", self.val)

    async def fetch(self, *a, **k):
        return await self._go("fetch", [])

    def transaction(self):
        return self

    async def start(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        await asyncio.sleep(30)

    def terminate(self):
        self.terminated = True


class Pool:
    """Hands out `conns` in order (the last one repeats). The pool's own fetch*/execute are the
    unbounded path: they ignore cancellation for 2 s (as asyncpg's shielded wait can) and count."""

    def __init__(self, *conns, hang_acquire=False, hang_release=False):
        self.conns = list(conns) or [Conn()]
        self.hang_acquire, self.hang_release = hang_acquire, hang_release
        self.acquired = 0
        self.acquire_timeout = None
        self.release_timeout = None
        self.released = []
        self.direct = 0

    async def acquire(self, timeout=None):
        self.acquire_timeout = timeout
        n = self.acquired
        self.acquired += 1
        if self.hang_acquire:
            await asyncio.wait_for(asyncio.sleep(30), timeout)
        return self.conns[min(n, len(self.conns) - 1)]

    async def release(self, conn, timeout=None):
        self.release_timeout = timeout
        self.released.append(conn)
        if self.hang_release:
            await asyncio.wait_for(asyncio.sleep(30), timeout)

    async def _unbounded(self, *a, **k):
        self.direct += 1
        end = time.monotonic() + 2
        while time.monotonic() < end:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass
        raise TimeoutError

    fetch = fetchrow = fetchval = execute = _unbounded


class NoPool:
    """No time left: the database must not be touched at all."""

    def __getattr__(self, name):
        raise AssertionError(f"database touched: {name}")


class Runtime:
    def __init__(self, fail=True):
        self.fail = fail

    async def exec_script(self, name, script, timeout):
        if self.fail:
            raise RuntimeError("docker down")
        return SimpleNamespace(timed_out=False, exit_code=0, output="")


def _lab(tmp_path: Path, *task_list: Task) -> Lab:
    for t in task_list:
        d = tmp_path / "tasks" / t.id
        d.mkdir(parents=True, exist_ok=True)
        (d / "check.sh").write_bytes(b"true\n")
        (d / "setup.sh").write_bytes(b"true\n")
    return Lab(
        id="lab", title={"en": "L", "he": "ל"}, status="published", image="img", module=1,
        lesson=1, path=tmp_path, tasks=list(task_list),
    )


def _ctx(pool, lab, spent=0.0, current=None, runtime=None):
    request = SimpleNamespace(
        state=SimpleNamespace(started=time.monotonic() - spent),
        app=SimpleNamespace(state=SimpleNamespace(pool=pool, runtime=runtime or Runtime())),
    )
    return tasks._Ctx(request, SID, "student-key", lab, current)


QUIZ = Task(
    id="t1", kind="quiz", question={"en": "q", "he": "ש"},
    options=[{"en": "a", "he": "א"}] * 2, correct=0,
)
HANDS = Task(id="t1", kind="hands-on", checklist=[{"en": "c", "he": "ג"}])


@pytest.fixture(autouse=True)
def _small_bound(monkeypatch):
    monkeypatch.setattr(tasks, "DB_STEP_BOUND_S", BOUND)


def _raises(coro, status=None):
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    took = time.monotonic() - t0
    if status is not None:
        assert exc.value.status_code == status
    return exc.value, took


# ---------------------------------------------------------------- 1. bounded single reads


def test_check_read_of_the_shown_row_that_hangs_is_cut_off_and_the_connection_terminated(tmp_path):
    conn = Conn(hang={"fetchrow"})
    pool = Pool(conn)
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    _, took = _raises(tasks._gradable(ctx, "t1", "quiz"), 503)
    assert took < 1
    assert conn.terminated
    assert pool.direct == 0  # not the pool's unbounded fetch*


def test_check_read_with_every_connection_busy_gives_up_at_the_bound(tmp_path):
    pool = Pool(hang_acquire=True)
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    _, took = _raises(tasks._gradable(ctx, "t1", "quiz"), 503)
    assert took < 1
    assert pool.acquire_timeout is not None and 0 < pool.acquire_timeout <= BOUND
    assert pool.direct == 0


def test_check_read_with_a_hanging_release_still_returns_within_the_bound(tmp_path):
    conn = Conn(row=SHOWN)
    pool = Pool(conn, hang_release=True)
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    t0 = time.monotonic()
    task = asyncio.run(tasks._gradable(ctx, "t1", "quiz"))
    assert task.id == "t1"
    assert time.monotonic() - t0 < 1
    assert pool.release_timeout is not None and 0 < pool.release_timeout <= BOUND
    assert pool.direct == 0


def test_check_read_of_a_healthy_connection_is_released_not_terminated(tmp_path):
    conn = Conn(row=SHOWN)
    pool = Pool(conn)
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    asyncio.run(tasks._gradable(ctx, "t1", "quiz"))
    assert not conn.terminated and pool.released == [conn]


def test_check_read_is_bounded_by_the_time_left_in_the_request(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "DB_STEP_BOUND_S", 8.0)
    pool = Pool(Conn(row=SHOWN))
    ctx = _ctx(pool, _lab(tmp_path, QUIZ), spent=db.REQUEST_DEADLINE_S - 0.3)
    asyncio.run(tasks._gradable(ctx, "t1", "quiz"))
    assert pool.acquire_timeout is not None and pool.acquire_timeout <= 0.35


def test_unshown_task_is_still_409_and_unknown_or_wrong_kind_unchanged(tmp_path):
    pool = Pool(Conn(row=None))
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    err, _ = _raises(tasks._gradable(ctx, "t1", "quiz"), 409)
    assert err.detail == "task_not_shown"
    _raises(tasks._gradable(ctx, "nope", "quiz"), 404)
    _raises(tasks._gradable(ctx, "t1", "hands-on"), 409)


def test_attempts_read_after_a_server_side_check_failure_is_bounded(tmp_path):
    conn = Conn(row=SHOWN, hang={"fetchval"})  # the shown row reads fine, attempts hang
    pool = Pool(conn)
    ctx = _ctx(pool, _lab(tmp_path, HANDS), runtime=Runtime(fail=True))
    _, took = _raises(tasks.check_task("t1", ctx), 503)
    assert took < 1
    assert conn.terminated
    assert pool.direct == 0


def test_server_side_check_failure_returns_the_attempt_count_without_counting(tmp_path):
    pool = Pool(Conn(row=SHOWN, val=2))
    ctx = _ctx(pool, _lab(tmp_path, HANDS), runtime=Runtime(fail=True))
    out = asyncio.run(tasks.check_task("t1", ctx))
    assert out["reason"] == "error" and out["attempts"] == 2 and out["passed"] is False
    assert pool.direct == 0


def test_session_status_read_after_an_unrecorded_attempt_is_bounded_and_terminates(tmp_path):
    first = Conn(row=None)  # the attempt step finds no shown row and commits
    second = Conn(hang={"fetchval"})  # then the status read hangs
    pool = Pool(first, second)
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    _, took = _raises(tasks._count_attempt(ctx, QUIZ, True), 503)
    assert took < 1
    assert second.terminated and not first.terminated
    assert pool.direct == 0


@pytest.mark.parametrize(
    "status, code, detail",
    [("running", 409, "task_not_shown"), ("ended", 410, "session_ended")],
)
def test_session_status_read_keeps_its_answers(tmp_path, status, code, detail):
    pool = Pool(Conn(row=None), Conn(val=status))
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    err, _ = _raises(tasks._count_attempt(ctx, QUIZ, True), code)
    assert err.detail == detail
    assert pool.direct == 0


def test_navigation_guard_read_is_bounded_and_terminates(tmp_path):
    conn = Conn(hang={"fetchval"})
    pool = Pool(conn)
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    _, took = _raises(tasks._show(ctx, QUIZ, moving_from="other"), 503)
    assert took < 1
    assert conn.terminated
    assert pool.direct == 0


def test_navigation_guard_read_still_answers_task_changed(tmp_path):
    pool = Pool(Conn(val="somewhere-else"))
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    err, _ = _raises(tasks._show(ctx, QUIZ, moving_from="other"), 409)
    assert err.detail == "task_changed"


# ---------------------------------------------------------------- 2. check_timeout


def _assert_check_timeout(err: HTTPException) -> None:
    assert err.status_code == 504
    assert isinstance(err.detail, dict)
    assert err.detail["code"] == "check_timeout"
    assert isinstance(err.detail["message"], str) and err.detail["message"].strip()


def test_check_with_no_time_left_answers_504_check_timeout_and_touches_nothing(tmp_path):
    ctx = _ctx(NoPool(), _lab(tmp_path, HANDS), spent=db.REQUEST_DEADLINE_S + 1)
    err, _ = _raises(tasks.check_task("t1", ctx))
    _assert_check_timeout(err)


def test_answer_with_no_time_left_answers_504_check_timeout_and_touches_nothing(tmp_path):
    ctx = _ctx(NoPool(), _lab(tmp_path, QUIZ), spent=db.REQUEST_DEADLINE_S + 1)
    err, _ = _raises(tasks.answer_task("t1", tasks._Answer(option=0), ctx))
    _assert_check_timeout(err)


def test_reading_the_shown_row_with_no_time_left_is_check_timeout(tmp_path):
    ctx = _ctx(NoPool(), _lab(tmp_path, QUIZ), spent=db.REQUEST_DEADLINE_S + 1)
    err, _ = _raises(tasks._gradable(ctx, "t1", "quiz"))
    _assert_check_timeout(err)


def test_counting_an_attempt_with_no_time_left_is_check_timeout(tmp_path):
    ctx = _ctx(NoPool(), _lab(tmp_path, QUIZ), spent=db.REQUEST_DEADLINE_S + 1)
    err, _ = _raises(tasks._count_attempt(ctx, QUIZ, True))
    _assert_check_timeout(err)


def test_budget_spent_during_the_check_script_gives_check_timeout_for_the_attempt_step(tmp_path):
    class Slow(Runtime):
        async def exec_script(self, name, script, timeout):
            ctx.request.state.started = time.monotonic() - db.REQUEST_DEADLINE_S - 1
            return SimpleNamespace(timed_out=False, exit_code=0, output="")

    pool = Pool(Conn(row=SHOWN))  # the first read is fine, the budget then runs out
    ctx = _ctx(pool, _lab(tmp_path, HANDS), runtime=Slow())
    err, _ = _raises(tasks.check_task("t1", ctx))
    _assert_check_timeout(err)


def test_a_show_with_no_time_left_keeps_the_setup_timeout_code(tmp_path):
    ctx = _ctx(NoPool(), _lab(tmp_path, HANDS), spent=db.REQUEST_DEADLINE_S + 1)
    err, _ = _raises(tasks._do_show(ctx, HANDS), 504)
    assert err.detail["code"] == "setup_timeout"


# ---------------------------------------------------------------- 3. pool acquire timeout


def test_the_pool_acquire_timeout_is_below_nginx_and_below_the_request_budget():
    assert 0 < db.POOL_ACQUIRE_TIMEOUT < 60
    assert db.POOL_ACQUIRE_TIMEOUT <= db.REQUEST_DEADLINE_S


class HeldPool:
    """All connections are held: acquire waits for the timeout it is given, and the pool's own
    fetch*/execute (which acquire without a limit) never return."""

    def __init__(self):
        self.acquire_timeout = "never called"

    async def acquire(self, timeout=None):
        self.acquire_timeout = timeout
        if timeout is None:
            await asyncio.sleep(30)
        await asyncio.sleep(min(timeout, 0.05))
        raise TimeoutError

    async def _wait(self, *a, **k):
        await asyncio.sleep(30)

    fetch = fetchrow = fetchval = execute = _wait


def _fast(coro):
    async def go():
        return await asyncio.wait_for(coro, 3)

    t0 = time.monotonic()
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(go())
    except TimeoutError:
        pytest.fail("the read waited for a free connection without a limit")
    return exc.value, time.monotonic() - t0


def test_task_list_with_every_connection_held_fails_fast_with_503(tmp_path):
    pool = HeldPool()
    ctx = _ctx(pool, _lab(tmp_path, QUIZ))
    err, took = _fast(tasks.task_list(ctx))
    assert err.status_code == 503 and took < 3
    assert pool.acquire_timeout not in (None, "never called")
    assert 0 < pool.acquire_timeout < 60


def test_current_task_with_every_connection_held_fails_fast_with_503(tmp_path):
    pool = HeldPool()
    ctx = _ctx(pool, _lab(tmp_path, QUIZ), current="t1")
    err, took = _fast(tasks.current_task(ctx))
    assert err.status_code == 503 and took < 3
    assert pool.acquire_timeout not in (None, "never called")
    assert 0 < pool.acquire_timeout < 60
