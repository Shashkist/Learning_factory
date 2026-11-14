"""Database-backed auth tests: skip without LF_TEST_DATABASE_URL (a local throwaway Postgres)."""
import asyncio
import datetime as dt
import itertools
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.main import lifespan

SCHEMA = "learning_factory"
OWNER_SQL = Path(__file__).resolve().parents[4] / "labs/deploy/db/001-create-schema-and-role.sql"
APP_PASSWORD = "test-only-" + "pw"  # throwaway
SECRET = "unit-db-" + "secret"  # throwaway
PASSWORD = "class-" + "password-2"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)
_n = itertools.count(1)


def _admin_dsn() -> str:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set (no Postgres available)")
    host = urlsplit(dsn).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail(f"LF_TEST_DATABASE_URL must point at a local throwaway Postgres, not {host!r}")
    return dsn


def _app_dsn(admin_dsn: str) -> str:
    parts = urlsplit(admin_dsn)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"lf_app:{APP_PASSWORD}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _sql(dsn: str, fn):
    async def go():
        import asyncpg

        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


@pytest.fixture(scope="module")
def _db():
    dsn = _admin_dsn()

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role lf_app password '{APP_PASSWORD}'")

    _sql(dsn, reset)
    return dsn


def _make_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(auth.router)

    @app.get("/student-only")
    async def student_only(p: Annotated[auth.Principal, Depends(auth.current_student)]) -> str:
        return p.name

    @app.get("/admin-only")
    async def admin_only(p: Annotated[auth.Principal, Depends(auth.current_admin)]) -> str:
        return p.name

    return app


@pytest.fixture
def ctx(_db, monkeypatch):
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", SECRET)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    monkeypatch.setenv("LF_LOGIN_IP_LIMIT", "1000")
    monkeypatch.setenv("LF_LOGIN_ID_LIMIT", "1000")
    ip = f"10.77.0.{next(_n) % 250 + 1}"
    with TestClient(_make_app(), base_url="https://testserver", client=(ip, 50000)) as client:
        yield _db, client


def _seed_student(dsn: str) -> str:
    raw = str(800000000 + next(_n))
    pw = auth.hash_password(PASSWORD)

    async def go(conn):
        cid = await conn.fetchval(
            f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
            "values ('Fake cohort', $1, $2) returning id",
            pw,
            TODAY + 30 * DAY,
        )
        await conn.execute(
            f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, 'Fake', $2)",
            auth.id_hash(raw, SECRET),
            cid,
        )

    _sql(dsn, go)
    return raw


def _seed_admin(dsn: str) -> tuple[str, str]:
    name = f"fake-admin-{next(_n)}"
    pw = auth.hash_password(PASSWORD)

    async def go(conn):
        await conn.execute(
            f"insert into {SCHEMA}.admins (username, password_hash) values ($1, $2)", name, pw
        )

    _sql(dsn, go)
    return name, PASSWORD


def _student_login(client, raw: str) -> None:
    r = client.post("/api/auth/login", json={"id": raw, "password": PASSWORD})
    assert r.status_code == 200, r.text


def test_student_disabled_after_login_is_refused_on_next_request(ctx) -> None:
    dsn, client = ctx
    raw = _seed_student(dsn)
    _student_login(client, raw)
    assert client.get("/api/auth/me").status_code == 200

    async def disable(conn):
        await conn.execute(
            f"update {SCHEMA}.students set disabled = true where id_hash = $1",
            auth.id_hash(raw, SECRET),
        )

    _sql(dsn, disable)
    assert client.get("/api/auth/me").status_code == 401


def test_student_access_moved_into_the_past_after_login_is_refused(ctx) -> None:
    dsn, client = ctx
    raw = _seed_student(dsn)
    _student_login(client, raw)
    assert client.get("/student-only").status_code == 200

    async def expire(conn):
        await conn.execute(
            f"update {SCHEMA}.students set access_until = $2 where id_hash = $1",
            auth.id_hash(raw, SECRET),
            TODAY - DAY,
        )

    _sql(dsn, expire)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/student-only").status_code == 401


def test_role_dependencies_refuse_the_other_kind(ctx) -> None:
    dsn, client = ctx
    raw = _seed_student(dsn)
    _student_login(client, raw)
    assert client.get("/student-only").status_code == 200
    assert client.get("/admin-only").status_code == 403
    client.post("/api/auth/logout")

    name, pw = _seed_admin(dsn)
    r = client.post("/api/auth/admin/login", json={"username": name, "password": pw})
    assert r.status_code == 200
    assert client.get("/admin-only").status_code == 200
    assert client.get("/student-only").status_code == 403


def test_cli_reset_signs_the_admin_out(ctx) -> None:
    from app import cli

    dsn, client = ctx
    name, pw = _seed_admin(dsn)
    body = {"username": name, "password": pw}
    assert client.post("/api/auth/admin/login", json=body).status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    app_dsn = _app_dsn(dsn)
    assert asyncio.run(cli.create_admin(app_dsn, name, "new-password-1", False)) == "exists"
    assert asyncio.run(cli.create_admin(app_dsn, name, "new-password-1", True)) == "reset"
    assert client.get("/api/auth/me").status_code == 401
    ok = client.post("/api/auth/admin/login", json={"username": name, "password": "new-password-1"})
    assert ok.status_code == 200
