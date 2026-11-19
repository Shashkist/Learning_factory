"""Database layer: plain asyncpg, plain SQL migrations. The DSN is never logged."""
import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import HTTPException

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA = "learning_factory"
_LOCK_NS = 4101  # namespace key for pg_advisory_xact_lock(key1, key2)
_LOCK_KEY = 2026
CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 60
# Request-serving pool only: below nginx's 60 s read timeout, so a stuck query fails with a
# JSON error before the client is cut off. Migrations (`connect`) keep the longer bound.
POOL_COMMAND_TIMEOUT = 50
# The longest a request-path read waits for a free pool connection (seconds), below nginx's 60 s.
POOL_ACQUIRE_TIMEOUT = 10.0
# The longest a whole request may wait for the database (seconds), below nginx's 60 s. Read when
# the request runs. It caps every wait for a connection, so a route that reads several times
# cannot add up per-wait limits past it.
REQUEST_DEADLINE_S = 55.0
_request_started: ContextVar[float | None] = ContextVar("lf_request_started", default=None)


def mark_request_start() -> None:
    """Start the request clock (once per HTTP request, before any dependency runs)."""
    _request_started.set(time.monotonic())


def _db_busy() -> HTTPException:
    return HTTPException(
        503, {"code": "db_busy", "message": "The database is busy. Try again in a moment."}
    )


class MigrationsMissing(RuntimeError):
    """No migration files found: a packaging/config error, not a database outage."""


def database_url_from_env() -> str | None:
    return os.environ.get("LF_DATABASE_URL") or None


async def connect(dsn: str) -> "asyncpg.Connection[asyncpg.Record]":
    # statement_cache_size=0: Supabase's transaction pooler keeps no prepared statements.
    return await asyncpg.connect(
        dsn,
        statement_cache_size=0,
        timeout=CONNECT_TIMEOUT,
        command_timeout=COMMAND_TIMEOUT,
    )


async def create_pool(dsn: str) -> "asyncpg.Pool[asyncpg.Record]":
    return await asyncpg.create_pool(
        dsn,
        statement_cache_size=0,
        min_size=1,
        max_size=5,
        timeout=CONNECT_TIMEOUT,
        command_timeout=POOL_COMMAND_TIMEOUT,
    )


async def migrate(dsn: str) -> list[str]:
    """Apply pending migrations in name order; return the versions applied now."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)
    if not files:
        raise MigrationsMissing(f"no migration files found in {MIGRATIONS_DIR}")
    applied: list[str] = []
    conn = await connect(dsn)
    try:
        for path in files:
            version = path.stem
            async with conn.transaction():
                await conn.execute("set local lock_timeout = '30s'")
                await conn.execute("select pg_advisory_xact_lock($1, $2)", _LOCK_NS, _LOCK_KEY)
                await conn.execute(
                    f"create table if not exists {SCHEMA}.schema_migrations ("
                    "version text primary key, applied_at timestamptz not null default now())"
                )
                done = await conn.fetchval(
                    f"select 1 from {SCHEMA}.schema_migrations where version = $1", version
                )
                if done:
                    continue
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(
                    f"insert into {SCHEMA}.schema_migrations (version) values ($1)", version
                )
                applied.append(version)
    finally:
        await conn.close()
    return applied


class BoundedPool:
    """A pool for request paths: waiting for a free connection is limited by
    `POOL_ACQUIRE_TIMEOUT` (read at call time), then the request answers 503 `db_busy` instead
    of hanging until nginx cuts the client off. `acquire()` is only an async context manager
    (`async with`): no `await pool.acquire()`, no `release`. fetch/fetchrow/fetchval/execute are
    as asyncpg's."""

    def __init__(self, pool: Any) -> None:
        if not hasattr(pool, "acquire"):
            raise TypeError("BoundedPool needs a pool with acquire()")
        self._pool = pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        wait = POOL_ACQUIRE_TIMEOUT
        started = _request_started.get()
        if started is not None:
            wait = min(wait, started + REQUEST_DEADLINE_S - time.monotonic())
        if wait <= 0:
            raise _db_busy()
        try:
            conn = await asyncio.wait_for(self._pool.acquire(timeout=wait), wait)
        except TimeoutError:
            raise _db_busy() from None
        try:
            yield conn
        finally:
            await self._pool.release(conn)

    async def _run(self, method: str, *args: Any) -> Any:
        async with self.acquire() as conn:
            return await getattr(conn, method)(*args)

    async def fetch(self, *args: Any) -> Any:
        return await self._run("fetch", *args)

    async def fetchrow(self, *args: Any) -> Any:
        return await self._run("fetchrow", *args)

    async def fetchval(self, *args: Any) -> Any:
        return await self._run("fetchval", *args)

    async def execute(self, *args: Any) -> Any:
        return await self._run("execute", *args)


def bounded(pool: Any) -> Any:
    """`pool` as a BoundedPool (None and an already bounded pool pass through)."""
    return pool if pool is None or isinstance(pool, BoundedPool) else BoundedPool(pool)
