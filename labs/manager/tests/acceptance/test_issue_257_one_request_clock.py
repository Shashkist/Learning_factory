"""Acceptance tests for issue #257: one request clock for the task routes and the database.

No Docker, no database. The contract (internals named on purpose, they exist today):

* `app.db.REQUEST_DEADLINE_S` is THE request deadline, read when the request runs. The request
  starts when the middleware calls `db.mark_request_start()`.
* `app.tasks._do_show(ctx, task)` (the show/navigate worker) and `app.tasks._left(ctx)` (the
  seconds of the request budget still unspent) answer from that clock: remaining budget =
  `db.REQUEST_DEADLINE_S - (now - request start)`. `tasks.REQUEST_BUDGET_S` may be removed or be
  an alias; whichever it is, changing it must not change what the routes do.
* An exhausted budget is the #236/#220 answer: HTTP 504, detail code `setup_timeout`, and the
  database is not touched.

Each case starts the request clock (`db.mark_request_start()`) inside one event-loop run and
gives the context the same start (`started`), as the middleware and the route dependency do.
"""
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import db, tasks
from app.content import Task


class _Touched(Exception):
    """The fake pool was reached: the route had budget left."""


class _Pool:
    def __init__(self):
        self.calls = []

    async def acquire(self, *a, **k):
        self.calls.append("acquire")
        raise _Touched

    async def release(self, *a, **k):
        pass

    async def fetchrow(self, *a, **k):
        self.calls.append("fetchrow")
        raise _Touched

    fetch = fetchval = execute = fetchrow


def _ctx(pool, spent, with_started=True):
    task = Task(id="t1", kind="quiz")
    lab = SimpleNamespace(id="lab", path=Path("."), tasks=[task])
    state = SimpleNamespace(started=time.monotonic() - spent) if with_started else SimpleNamespace()
    ctx = SimpleNamespace(
        pool=pool,
        lab=lab,
        sid="00000000-0000-0000-0000-000000000000",
        student="s",
        current=None,
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), state=state),
    )
    if with_started:
        ctx.started = state.started
    return ctx, task


def _show(spent, with_started=True):
    """Run a show of a quiz task `spent` seconds into the request; return (pool, raised)."""
    pool = _Pool()

    async def run():
        db.mark_request_start()  # what the middleware does, then pretend `spent` has passed
        db._request_started.set(time.monotonic() - spent)
        ctx, task = _ctx(pool, spent, with_started)
        try:
            await tasks._do_show(ctx, task)
        except BaseException as exc:  # noqa: BLE001
            return exc
        return None

    return pool, asyncio.run(run())


def _left(spent, with_started=True):
    async def run():
        db.mark_request_start()
        db._request_started.set(time.monotonic() - spent)
        ctx, _ = _ctx(_Pool(), spent, with_started)
        return tasks._left(ctx)

    return asyncio.run(run())


def _assert_timeout(pool, exc):
    assert isinstance(exc, HTTPException), repr(exc)
    assert exc.status_code == 504
    assert isinstance(exc.detail, dict) and exc.detail.get("code") == "setup_timeout"
    assert pool.calls == []


def test_a_smaller_db_deadline_cuts_a_show_the_old_budget_would_allow(monkeypatch):
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 5.0)
    pool, exc = _show(spent=10.0)  # 10 s in: over 5 s, far under 55 s
    _assert_timeout(pool, exc)


def test_a_larger_db_deadline_lets_a_show_the_old_budget_would_cut(monkeypatch):
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 200.0)
    pool, exc = _show(spent=70.0)  # 70 s in: over 55 s, under 200 s
    assert isinstance(exc, _Touched), repr(exc)
    assert pool.calls


def test_the_old_budget_constant_no_longer_drives_the_routes(monkeypatch):
    """If REQUEST_BUDGET_S is still there it is not a second clock: it may not cut a request
    that the db deadline still allows."""
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 55.0)
    monkeypatch.setattr(tasks, "REQUEST_BUDGET_S", 1.0, raising=False)
    pool, exc = _show(spent=10.0)
    assert isinstance(exc, _Touched), repr(exc)


@pytest.mark.parametrize("deadline,spent", [(55.0, 3.0), (12.0, 3.0), (7.5, 0.2)])
def test_remaining_budget_and_db_deadline_agree_for_the_same_request(monkeypatch, deadline, spent):
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", deadline)
    assert _left(spent) == pytest.approx(deadline - spent, abs=0.5)


def test_a_context_without_its_own_start_uses_the_request_clock(monkeypatch):
    """The start is the one the middleware recorded, not 'now' of whoever builds the context."""
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 5.0)
    assert _left(spent=4.0, with_started=False) == pytest.approx(1.0, abs=0.5)
