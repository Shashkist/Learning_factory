"""The task routes' remaining budget and the db deadline are one clock (#257)."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from app import db, tasks


class _Conn:
    async def fetchval(self, *a):
        return None


class _Pool:
    def __init__(self):
        self.timeouts = []

    async def acquire(self, timeout=None):
        self.timeouts.append(timeout)
        return _Conn()

    async def release(self, conn, timeout=None):
        pass


@pytest.mark.parametrize("with_started", [True, False])
def test_tasks_left_matches_the_wait_the_db_layer_takes(monkeypatch, with_started):
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 20.0)
    monkeypatch.setattr(db, "POOL_ACQUIRE_TIMEOUT", 30.0)  # the deadline is the tighter bound

    async def run():
        db.mark_request_start()
        started = time.monotonic() - 14.0  # 6 s left, below the acquire timeout
        db._request_started.set(started)
        ctx = SimpleNamespace(started=started) if with_started else SimpleNamespace()
        pool = _Pool()
        await db.BoundedPool(pool).fetchval("q")
        return tasks._left(ctx), pool.timeouts[0]

    left, waited = asyncio.run(run())
    assert waited == pytest.approx(left, abs=0.1)
    assert left == pytest.approx(6.0, abs=0.5)


def test_real_start_and_ctx_share_the_middleware_start(monkeypatch):
    """`_start` adopts the middleware's start, so `_left` and the db deadline are one clock."""
    import uuid

    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 55.0)

    async def run():
        db.mark_request_start()
        db._request_started.set(time.monotonic() - 6.0)
        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(state=SimpleNamespace(pool=object())),
        )
        await tasks._start(request)
        await asyncio.sleep(0.05)  # a later start point would show here
        ctx = tasks._Ctx(request, uuid.uuid4(), "s", SimpleNamespace(tasks=[]), None)
        return tasks._left(ctx)

    assert asyncio.run(run()) == pytest.approx(55.0 - 6.0, abs=0.5)
