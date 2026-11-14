"""Acceptance tests for issue #4: database schema, migrations, owner SQL.

Database tests need a superuser DSN to a throwaway Postgres in
LF_TEST_DATABASE_URL and skip locally when it is absent (CI sets it and
fails on any skip). Degraded-mode tests need no database.
"""
import asyncio
import datetime as dt
import itertools
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret

REQUIRED_COLUMNS = {
    "cohorts": {"id", "name", "password_hash", "access_until"},
    "students": {"id_hash", "name", "note", "cohort_id", "access_until", "disabled"},
    "admins": {"username", "password_hash"},
    "lab_sessions": {
        "id", "lab_id", "container", "status", "started_at", "ended_at", "last_heartbeat",
    },
    "task_results": {
        "lab_id", "session_id", "task_id", "shown_at", "passed_at", "attempts", "fast_flag",
    },
    "lab_runs": {"lab_id", "session_id", "active_seconds", "first_completion", "fast_flag"},
    "lab_completions": {"lab_id", "completed_at"},
}
# Tables that must reference a student (column named student or student_id)
STUDENT_REF_TABLES = ["lab_sessions", "task_results", "lab_runs", "lab_completions"]


# ---------------------------------------------------------------- degraded mode


def test_manager_starts_without_database_and_healthz_stays_ok(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    from app.main import app

    with TestClient(app) as c:
        resp = c.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_api_health_reports_degraded_when_database_not_configured(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    from app.main import app

    with TestClient(app) as c:
        resp = c.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database"] == "not_configured"


# ---------------------------------------------------------------- helpers


def _admin_dsn() -> str:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set (no Postgres available)")
    # These tests drop schema learning_factory: never point them at a shared database.
    host = urlsplit(dsn).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail(f"LF_TEST_DATABASE_URL must point at a local throwaway Postgres, not {host!r}")
    return dsn


def _app_dsn(admin_dsn: str) -> str:
    parts = urlsplit(admin_dsn)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def _connect(dsn: str):
    import asyncpg  # a missing asyncpg must fail, not skip

    return await asyncpg.connect(dsn, statement_cache_size=0)


def _run_as(dsn: str, fn):
    async def go():
        conn = await _connect(dsn)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


def _owner_sql() -> str:
    assert OWNER_SQL.is_file(), f"{OWNER_SQL} is missing"
    return OWNER_SQL.read_text(encoding="utf-8")


async def _reset_and_apply_owner_sql(conn) -> None:
    await conn.execute(f"drop schema if exists {SCHEMA} cascade")
    await conn.execute("drop schema if exists lf_other_test cascade")
    await conn.execute(_owner_sql())
    await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")


@pytest.fixture(scope="module")
def admin_dsn():
    return _admin_dsn()


@pytest.fixture(scope="module")
def migrated(admin_dsn):
    """Fresh schema via the owner SQL (superuser), then migrations run as lf_app."""
    _run_as(admin_dsn, _reset_and_apply_owner_sql)
    from app import db

    app_dsn = _app_dsn(admin_dsn)
    first = asyncio.run(db.migrate(app_dsn))
    return {"admin": admin_dsn, "app": app_dsn, "first": first}


_counter = itertools.count(1)


async def _insert(conn, table: str, **given):
    """Insert a row, filling any other NOT NULL column without a default."""
    cols = await conn.fetch(
        """select column_name, data_type, is_nullable, column_default, is_identity,
                  is_generated
           from information_schema.columns
           where table_schema = $1 and table_name = $2""",
        SCHEMA,
        table,
    )
    values = dict(given)
    for c in cols:
        name = c["column_name"]
        if name in values:
            continue
        if (
            c["is_nullable"] == "YES"
            or c["column_default"] is not None
            or c["is_identity"] == "YES"
            or c["is_generated"] == "ALWAYS"
        ):
            continue
        t = c["data_type"]
        n = next(_counter)
        if t in ("text", "character varying", "character"):
            values[name] = f"test-{name}-{n}"
        elif t in ("integer", "bigint", "smallint", "numeric", "double precision", "real"):
            values[name] = n
        elif t == "uuid":
            values[name] = uuid.uuid4()
        elif t == "boolean":
            values[name] = False
        elif t.startswith("timestamp"):
            now = dt.datetime.now(dt.UTC)
            values[name] = now.replace(tzinfo=None) if "without" in t else now
        elif t == "date":
            values[name] = dt.date.today()
        else:
            raise AssertionError(f"exam helper cannot fill {table}.{name} ({t})")
    names = list(values)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(names)))
    sql = (
        f"insert into {SCHEMA}.{table} ({', '.join(names)}) "
        f"values ({placeholders}) returning *"
    )
    return await conn.fetchrow(sql, *values.values())


async def _column_names(conn, table: str) -> set[str]:
    rows = await conn.fetch(
        "select column_name from information_schema.columns "
        "where table_schema = $1 and table_name = $2",
        SCHEMA,
        table,
    )
    return {r["column_name"] for r in rows}


# ---------------------------------------------------------------- migrations


def test_migrate_as_restricted_role_applies_migrations(migrated):
    assert migrated["first"], "first migrate() on an empty schema must apply at least one version"
    assert all(isinstance(v, str) for v in migrated["first"])
    assert migrated["first"] == sorted(migrated["first"]), "migrations apply in name order"

    async def versions(conn):
        rows = await conn.fetch(f"select version from {SCHEMA}.schema_migrations")
        return {r["version"] for r in rows}

    assert set(migrated["first"]) <= _run_as(migrated["app"], versions)


def test_migrate_second_call_applies_nothing(migrated):
    from app import db

    assert asyncio.run(db.migrate(migrated["app"])) == []


def test_migrations_are_recorded_in_schema_migrations(migrated):
    async def versions(conn):
        rows = await conn.fetch(
            f"select version from {SCHEMA}.schema_migrations order by version"
        )
        return [r["version"] for r in rows]

    recorded = _run_as(migrated["admin"], versions)
    assert recorded
    assert len(recorded) == len(set(recorded))


# ---------------------------------------------------------------- schema


def test_all_spec_tables_exist_in_learning_factory_schema(migrated):
    async def tables(conn):
        rows = await conn.fetch(
            "select table_name from information_schema.tables where table_schema = $1",
            SCHEMA,
        )
        return {r["table_name"] for r in rows}

    got = _run_as(migrated["admin"], tables)
    missing = set(REQUIRED_COLUMNS) - got
    assert not missing, f"missing tables: {sorted(missing)}"


@pytest.mark.parametrize("table", sorted(REQUIRED_COLUMNS))
def test_table_has_the_columns_the_spec_names(migrated, table):
    async def cols(conn):
        return await _column_names(conn, table)

    got = _run_as(migrated["admin"], cols)
    missing = REQUIRED_COLUMNS[table] - got
    assert not missing, f"{table} is missing columns {sorted(missing)}"


@pytest.mark.parametrize("table", STUDENT_REF_TABLES)
def test_table_references_a_student(migrated, table):
    async def cols(conn):
        return await _column_names(conn, table)

    got = _run_as(migrated["admin"], cols)
    assert got & {"student", "student_id", "student_id_hash"}, f"{table} has no student column"


def test_student_id_hash_is_unique(migrated):
    import asyncpg

    async def go(conn):
        cohort = await _insert(conn, "cohorts", name="exam-cohort")
        ref = {"cohort_id": cohort["id"]}
        await _insert(conn, "students", id_hash="hash-dup-0001", name="Test Student A", **ref)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert(conn, "students", id_hash="hash-dup-0001", name="Test Student B", **ref)
        # a different hash is fine
        await _insert(conn, "students", id_hash="hash-dup-0002", name="Test Student C", **ref)

    _run_as(migrated["app"], go)


# ---------------------------------------------------------------- owner SQL and role


def test_owner_sql_is_idempotent_when_run_twice_as_superuser(migrated):
    async def twice(conn):
        await conn.execute(_owner_sql())
        await conn.execute(_owner_sql())
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(migrated["admin"], twice)

    async def role(conn):
        return await conn.fetchrow(
            "select rolcanlogin, rolsuper from pg_roles where rolname = $1", APP_ROLE
        )

    row = _run_as(migrated["admin"], role)
    assert row is not None and row["rolcanlogin"] is True and row["rolsuper"] is False


def test_app_role_still_works_after_owner_sql_reran(migrated):
    async def count(conn):
        return await conn.fetchval(f"select count(*) from {SCHEMA}.cohorts")

    assert _run_as(migrated["app"], count) >= 0


def test_app_role_can_create_tables_in_its_schema(migrated):
    async def go(conn):
        await conn.execute(f"create table {SCHEMA}.lf_exam_scratch (x int)")
        await conn.execute(f"drop table {SCHEMA}.lf_exam_scratch")

    _run_as(migrated["app"], go)


def test_app_role_cannot_create_table_in_public(migrated):
    import asyncpg

    async def go(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("create table public.lf_exam_forbidden (x int)")

    _run_as(migrated["app"], go)


def test_app_role_cannot_read_table_in_another_schema(migrated):
    import asyncpg

    async def setup(conn):
        await conn.execute("create schema if not exists lf_other_test")
        await conn.execute("create table if not exists lf_other_test.t (x int)")
        await conn.execute("insert into lf_other_test.t values (1)")

    async def teardown(conn):
        await conn.execute("drop schema if exists lf_other_test cascade")

    async def attempt(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("select * from lf_other_test.t")

    _run_as(migrated["admin"], setup)
    try:
        _run_as(migrated["app"], attempt)
    finally:
        _run_as(migrated["admin"], teardown)
