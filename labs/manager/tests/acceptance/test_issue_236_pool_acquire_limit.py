"""Acceptance tests for issue #236: a pool acquire limit on every request path.

No Docker, no database. A fake pool stands in for asyncpg's: with every connection held, its
`acquire` waits for the `timeout` it was given (with no timeout it waits "forever", here 1.5 s and
then fails), and its own fetch*/execute wait without any limit, as asyncpg's do.

1. One shared helper bounds the acquire on every request path; the limit is
   `app.db.POOL_ACQUIRE_TIMEOUT` (0 < t < 60, nginx's read timeout), read when the request runs.
   The code `db_busy` is produced in one module only, not copied per route.
2. With the pool exhausted, representative routes of each module answer 503 with detail
   {"code": "db_busy", "message": <text>} within the limit instead of hanging: the auth guard (me,
   student routes, admin routes, the terminal proxy's `authorize`), and the reads of catalog,
   sessions, tasks, teacher, class table, live sessions and the student page.
3. A pool that answers keeps working (no route answers db_busy when it is not busy).
"""
import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, db, term_proxy
from app.main import app

LIMIT = 0.3
HANG = 1.5  # what "wait without a limit" costs the test before it fails
FAST = 1.2  # a bounded request must be answered well inside this
SID = "00000000-0000-0000-0000-000000000001"
APP_DIR = Path(__file__).resolve().parents[2] / "app"
STUDENT = auth.Principal("student", "Dana Test", "hash-1")
ADMIN = auth.Principal("admin", "teacher", "teacher")


class Conn:
    def __init__(self):
        self.terminated = False

    async def fetchrow(self, *a, **k):
        return None

    async def fetchval(self, *a, **k):
        return None

    async def fetch(self, *a, **k):
        return []

    async def execute(self, *a, **k):
        return "OK"

    def terminate(self):
        self.terminated = True


class ExhaustedPool:
    """Every connection is held. Only a bounded acquire gives up in time."""

    def __init__(self):
        self.acquire_timeouts: list[float | None] = []

    async def acquire(self, timeout=None):
        self.acquire_timeouts.append(timeout)
        await asyncio.wait_for(asyncio.sleep(HANG), timeout)
        raise TimeoutError

    async def release(self, conn, timeout=None):
        pass

    async def _unbounded(self, *a, **k):
        await asyncio.sleep(HANG)
        raise TimeoutError

    fetch = fetchrow = fetchval = execute = _unbounded


class FreePool:
    """Answers at once; knows no session and no student (so every guarded route says 401)."""

    async def acquire(self, timeout=None):
        return Conn()

    async def release(self, conn, timeout=None):
        pass

    async def fetchrow(self, *a, **k):
        return None

    async def fetchval(self, *a, **k):
        return None

    async def fetch(self, *a, **k):
        return []

    async def execute(self, *a, **k):
        return "OK"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(db, "POOL_ACQUIRE_TIMEOUT", LIMIT)
    monkeypatch.setenv("LF_ID_SECRET", "test-secret")
    old = getattr(app.state, "pool", None)
    app.state.pool = ExhaustedPool()
    c = TestClient(app, base_url="https://testserver", client=("10.236.0.1", 50000))
    c.cookies.set("lf_session", "tok")
    yield c
    app.state.pool = old
    app.dependency_overrides.clear()


def _busy(r, took):
    assert r.status_code == 503, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert isinstance(detail, dict) and detail.get("code") == "db_busy", detail
    assert isinstance(detail.get("message"), str) and detail["message"].strip()
    assert took < FAST, f"took {took:.2f}s: the acquire is not bounded by the limit"


def _call(client, method, url, **kw):
    t = time.monotonic()
    r = client.request(method, url, **kw)
    return r, time.monotonic() - t


# Routes whose own auth guard is the first (and only) database touch.
GUARD_ROUTES = [
    ("GET", "/api/auth/me", None),
    ("GET", "/api/labs", None),
    ("GET", "/api/sessions/current", None),
    ("POST", f"/api/sessions/{SID}/heartbeat", None),
    ("GET", f"/api/sessions/{SID}/tasks", None),
    ("POST", f"/api/sessions/{SID}/tasks/t1/check", {}),
    ("POST", f"/api/sessions/{SID}/tasks/t1/answer", {"choice": 0}),
    ("GET", "/api/admin/cohorts", None),
    ("GET", "/api/admin/class", None),
    ("GET", "/api/admin/live", None),
    ("GET", "/api/admin/students/abc", None),
    ("GET", f"/s/{SID}/term/x", None),
]


@pytest.mark.parametrize(
    "method,url,body", GUARD_ROUTES, ids=[f"{m} {u}" for m, u, _ in GUARD_ROUTES]
)
def test_auth_guarded_routes_answer_db_busy_when_the_pool_is_exhausted(client, method, url, body):
    r, took = _call(client, method, url, **({"json": body} if body is not None else {}))
    _busy(r, took)


def test_current_principal_answers_db_busy(client):
    async def go():
        req = SimpleNamespace(cookies={"lf_session": "tok"}, app=app)
        with pytest.raises(HTTPException) as exc:
            await auth.current_principal(req)
        return exc.value

    t = time.monotonic()
    exc = asyncio.run(go())
    assert exc.status_code == 503 and exc.detail["code"] == "db_busy"
    assert time.monotonic() - t < FAST


def test_term_proxy_authorize_answers_db_busy(client):
    async def go():
        conn = SimpleNamespace(app=app)
        with pytest.raises(HTTPException) as exc:
            await term_proxy.authorize(conn, SID, STUDENT)
        return exc.value

    t = time.monotonic()
    exc = asyncio.run(go())
    assert exc.status_code == 503 and exc.detail["code"] == "db_busy"
    assert time.monotonic() - t < FAST


# Routes reached with the guard already passed: the module's own reads must be bounded too.
STUDENT_READS = [
    ("GET", "/api/labs"),
    ("GET", "/api/sessions/current"),
    ("POST", f"/api/sessions/{SID}/heartbeat"),
    ("POST", f"/api/sessions/{SID}/end"),
    ("GET", f"/api/sessions/{SID}/tasks"),
    ("GET", f"/api/sessions/{SID}/tasks/current"),
]
ADMIN_READS = [
    ("GET", "/api/admin/cohorts"),
    ("GET", "/api/admin/class"),
    ("GET", "/api/admin/live"),
    ("POST", f"/api/admin/live/{SID}/end"),
    # A well-formed key (a UUID), so the read reaches the pool; a malformed key
    # is refused with 404 before any read (#217).
    ("GET", "/api/admin/students/00000000-0000-4000-8000-000000000236"),
]


@pytest.mark.parametrize("method,url", STUDENT_READS, ids=[f"{m} {u}" for m, u in STUDENT_READS])
def test_student_route_reads_answer_db_busy(client, method, url):
    app.dependency_overrides[auth.current_principal] = lambda: STUDENT
    r, took = _call(client, method, url)
    _busy(r, took)


@pytest.mark.parametrize("method,url", ADMIN_READS, ids=[f"{m} {u}" for m, u in ADMIN_READS])
def test_admin_route_reads_answer_db_busy(client, method, url):
    app.dependency_overrides[auth.current_principal] = lambda: ADMIN
    r, took = _call(client, method, url)
    _busy(r, took)


def test_student_login_answers_db_busy(client):
    client.cookies.clear()
    r, took = _call(client, "POST", "/api/auth/login", json={"id": "123456782", "password": "x"})
    _busy(r, took)


def test_acquires_are_bounded_by_the_pool_acquire_timeout(client):
    pool = app.state.pool
    for url in ("/api/auth/me", "/api/labs", "/api/sessions/current"):
        client.get(url)
    assert pool.acquire_timeouts, "request paths must pass a timeout to pool.acquire"
    for t in pool.acquire_timeouts:
        assert t is not None and 0 < t <= LIMIT + 0.01, t


def test_the_limit_is_below_nginx_read_timeout():
    assert 0 < db.POOL_ACQUIRE_TIMEOUT < 60


def test_the_limit_is_read_from_db_when_the_request_runs(client, monkeypatch):
    """Not copied into the helper at import time: a changed constant changes the wait."""
    monkeypatch.setattr(db, "POOL_ACQUIRE_TIMEOUT", 0.1)
    t = time.monotonic()
    r = client.get("/api/auth/me")
    took = time.monotonic() - t
    assert r.status_code == 503 and r.json()["detail"]["code"] == "db_busy"
    assert took < 0.9


def test_db_busy_is_defined_in_one_module_only():
    holders = [
        p.name
        for p in APP_DIR.glob("*.py")
        if re.search(r"['\"]db_busy['\"]", p.read_text(encoding="utf-8"))
    ]
    assert len(holders) == 1, f"db_busy is written in {holders}: use one shared helper"


def test_a_free_pool_is_not_reported_as_busy(client):
    app.state.pool = FreePool()
    r, _ = _call(client, "GET", "/api/auth/me")
    assert r.status_code == 401, r.text
    r, _ = _call(client, "GET", "/api/admin/cohorts")
    assert r.status_code == 401, r.text
