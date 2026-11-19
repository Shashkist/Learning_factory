"""Database layer: plain asyncpg, plain SQL migrations. The DSN is never logged."""
import os
from pathlib import Path

import asyncpg

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
