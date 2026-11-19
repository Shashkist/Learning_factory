import asyncio

import pytest
from fastapi import HTTPException

from app import db


class _Conn:
    async def fetchval(self, *a):
        return a


class _Pool:
    def __init__(self, hold=False):
        self.hold, self.timeouts, self.released = hold, [], []

    async def acquire(self, timeout=None):
        self.timeouts.append(timeout)
        if self.hold:
            raise TimeoutError
        return _Conn()

    async def release(self, conn, timeout=None):
        self.released.append(conn)


def test_runs_on_an_acquired_connection_and_releases_it():
    pool = _Pool()
    assert asyncio.run(db.BoundedPool(pool).fetchval("q", 1)) == ("q", 1)
    assert len(pool.released) == 1


def test_limit_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(db, "POOL_ACQUIRE_TIMEOUT", 0.25)
    pool = _Pool()
    asyncio.run(db.BoundedPool(pool).fetchval("q"))
    assert pool.timeouts == [0.25]


def test_exhausted_pool_is_503_db_busy():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(db.BoundedPool(_Pool(hold=True)).fetchval("q"))
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "db_busy" and exc.value.detail["message"]


def test_bounded_passes_none_and_does_not_wrap_twice():
    pool = db.bounded(_Pool())
    assert db.bounded(None) is None and db.bounded(pool) is pool


def test_a_stand_in_pool_without_acquire_is_refused():
    class Plain:
        async def fetchval(self, *a):
            return "x"

    with pytest.raises(TypeError):
        db.BoundedPool(Plain())


def test_the_request_deadline_caps_the_wait_and_an_expired_one_is_db_busy(monkeypatch):
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 2.0)
    pool = _Pool()

    async def go():
        db.mark_request_start()
        await db.BoundedPool(pool).fetchval("q")
        monkeypatch.setattr(db, "REQUEST_DEADLINE_S", -1.0)
        await db.BoundedPool(pool).fetchval("q")

    with pytest.raises(HTTPException) as e:
        asyncio.run(go())
    assert len(pool.timeouts) == 1 and 0 < pool.timeouts[0] <= 2.0
    assert e.value.status_code == 503 and e.value.detail["code"] == "db_busy"


def test_a_statement_slower_than_the_time_left_is_db_busy_within_the_deadline(monkeypatch):
    import time

    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 0.3)

    class Slow(_Conn):
        async def fetchval(self, *a):
            await asyncio.sleep(30)

    class SlowPool(_Pool):
        async def acquire(self, timeout=None):
            return Slow()

    async def go():
        db.mark_request_start()
        await db.BoundedPool(SlowPool()).fetchval("q")

    t0 = time.monotonic()
    with pytest.raises(HTTPException) as e:
        asyncio.run(go())
    assert time.monotonic() - t0 < 2
    assert e.value.status_code == 503 and e.value.detail["code"] == "db_busy"


def test_a_release_that_times_out_is_db_busy_and_keeps_one_already_on_its_way():
    class StuckRelease(_Pool):
        async def release(self, conn, timeout=None):
            self.timeouts.append(("release", timeout))
            raise TimeoutError

    async def plain():
        async with db.BoundedPool(StuckRelease()).acquire():
            pass

    with pytest.raises(HTTPException) as e:
        asyncio.run(plain())
    assert e.value.status_code == 503 and e.value.detail["code"] == "db_busy"

    async def raising():
        async with db.BoundedPool(StuckRelease()).acquire():
            raise HTTPException(404, "gone")

    with pytest.raises(HTTPException) as e2:
        asyncio.run(raising())
    assert e2.value.status_code == 404
