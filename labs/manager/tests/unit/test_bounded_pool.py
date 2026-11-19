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

    async def release(self, conn):
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
