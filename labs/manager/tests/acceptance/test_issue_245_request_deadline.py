"""Acceptance tests for issue #245: one deadline for the whole request, and no unbounded pool.

No Docker, no database. The names the implementation must use:

* `app.db.REQUEST_DEADLINE_S` (float seconds, 0 < t < 60, nginx's read timeout; default about 55),
  read when the request runs (monkeypatched here), NOT copied at import time. It bounds a whole
  request that reads the database several times, on top of `POOL_ACQUIRE_TIMEOUT` (per wait).
  When it runs out the request answers 503 with detail {"code": "db_busy", "message": <text>},
  the same body as #236 (and not the plain "database unavailable" that `db_guard` writes).
* `db.BoundedPool(obj)` and `db.bounded(obj)` raise `TypeError` for an object without `acquire`
  (at construction), instead of silently using its fetch*/execute with no limit.

The slow pool: every acquire (and so every read) takes DELAY, just under the per-wait limit, so
no single wait ever times out; only the sum is too long. Tasks routes (REQUEST_BUDGET_S, #236)
are not touched here.
"""
import asyncio
import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app

LIMIT = 2.0  # per-wait limit
DELAY = 0.3  # each read: under LIMIT, so only the sum is too long
DEADLINE = 0.6  # the whole request
FAST = 1.1  # a deadline-bound answer must arrive well inside this (a 5+ read route needs >1.5 s)
KEY = "00000000-0000-4000-8000-000000000245"
ADMIN = auth.Principal("admin", "teacher", "teacher")


class _Conn:
    def __init__(self, pool):
        self.pool = pool

    async def _read(self, sql):
        self.pool.reads += 1
        if "auth_sessions" in sql:
            return {"kind": "student", "subject": "hash-1"}
        if "s.id_hash, s.key" in sql:  # the student page's student lookup
            return {
                "id_hash": "hash-1", "key": KEY, "name": "Dana Test", "note": "",
                "disabled": False, "access_until": None, "cohort": "c1",
                "cohort_until": dt.date(2099, 1, 1),
            }
        if "s.disabled" in sql:  # the auth guard's student lookup
            far = dt.date(2099, 1, 1)
            return {"name": "Dana Test", "disabled": False, "until": far, "password_hash": "x"}
        return None

    async def fetchrow(self, sql, *a, **k):
        return await self._read(sql)

    async def fetchval(self, sql, *a, **k):
        await self._read(sql)
        return 1

    async def fetch(self, sql, *a, **k):
        await self._read(sql)
        return []

    async def execute(self, sql, *a, **k):
        await self._read(sql)
        return "OK"


class SlowPool:
    """Faithful to asyncpg's pool for what the manager uses: acquire/release plus fetch*."""

    def __init__(self):
        self.reads = 0
        self.acquire_timeouts: list[float | None] = []

    async def acquire(self, timeout=None):
        self.acquire_timeouts.append(timeout)
        await asyncio.sleep(DELAY)  # a wait that ends just in time
        return _Conn(self)

    async def release(self, conn, timeout=None):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(db, "POOL_ACQUIRE_TIMEOUT", LIMIT)
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", DEADLINE, raising=False)
    monkeypatch.setenv("LF_ID_SECRET", "test-secret")
    old = getattr(app.state, "pool", None)
    app.state.pool = SlowPool()
    c = TestClient(app, base_url="https://testserver", client=("10.245.0.1", 50000))
    c.cookies.set("lf_session", "tok")
    yield c
    app.state.pool = old
    app.dependency_overrides.clear()


def _busy(r, took):
    assert r.status_code == 503, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert isinstance(detail, dict) and detail.get("code") == "db_busy", detail
    assert isinstance(detail.get("message"), str) and detail["message"].strip()
    assert took < FAST, f"took {took:.2f}s: the request is not bounded as a whole"


def _get(client, url):
    t = time.monotonic()
    r = client.get(url)
    return r, time.monotonic() - t


def test_the_deadline_is_below_nginx_read_timeout():
    value = getattr(db, "REQUEST_DEADLINE_S", None)
    assert isinstance(value, int | float) and 0 < value < 60, value


def test_student_page_with_slow_reads_answers_db_busy_within_the_deadline(client):
    """Student page: auth guard (override) + the student read + 6 page reads, each just in time."""
    app.dependency_overrides[auth.current_principal] = lambda: ADMIN
    r, took = _get(client, f"/api/admin/students/{KEY}")
    _busy(r, took)


def test_class_table_with_slow_reads_answers_db_busy_within_the_deadline(client):
    app.dependency_overrides[auth.current_principal] = lambda: ADMIN
    r, took = _get(client, "/api/admin/class")
    _busy(r, took)


def test_auth_guard_with_two_slow_reads_answers_db_busy_within_the_deadline(client, monkeypatch):
    """Session lookup then student lookup: 2 x DELAY is over this smaller deadline."""
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", DELAY * 1.5, raising=False)
    r, took = _get(client, "/api/auth/me")
    _busy(r, took)


def test_the_deadline_is_read_when_the_request_runs(client, monkeypatch):
    """A larger deadline lets the same two-read request through (it is not frozen at import)."""
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 5.0, raising=False)
    r, took = _get(client, "/api/auth/me")
    assert r.status_code == 200, (r.status_code, r.text)
    assert took >= DELAY * 2 * 0.9


def test_a_request_inside_the_deadline_is_not_reported_as_busy(client, monkeypatch):
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 5.0, raising=False)
    app.dependency_overrides[auth.current_principal] = lambda: ADMIN
    r, _ = _get(client, "/api/admin/class")
    assert r.status_code == 200, (r.status_code, r.text)


# ---------------------------------------------------------------- item 2: no unbounded fallback


class NoAcquire:
    """Only fetch*/execute, as the old test fakes: it would run with no limit at all."""

    def __init__(self):
        self.calls = 0

    async def fetchrow(self, *a, **k):
        self.calls += 1

    fetch = fetchval = execute = fetchrow


def test_bounded_pool_refuses_an_object_without_acquire():
    fake = NoAcquire()
    with pytest.raises(TypeError):
        db.BoundedPool(fake)
    assert fake.calls == 0


def test_bounded_refuses_an_object_without_acquire():
    with pytest.raises(TypeError):
        db.bounded(NoAcquire())


def test_bounded_still_accepts_none_a_bounded_pool_and_a_pool_with_acquire():
    assert db.bounded(None) is None
    pool = db.bounded(SlowPool())
    assert isinstance(pool, db.BoundedPool)
    assert db.bounded(pool) is pool
