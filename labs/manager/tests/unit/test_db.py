import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.main import app


def test_database_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    assert db.database_url_from_env() is None
    monkeypatch.setenv("LF_DATABASE_URL", "postgresql://x/y")
    assert db.database_url_from_env() == "postgresql://x/y"


def test_migration_files_are_numbered_and_sorted() -> None:
    names = [p.name for p in sorted(db.MIGRATIONS_DIR.glob("*.sql"))]
    assert names and names[0] == "001_initial.sql"
    assert all(n[:3].isdigit() for n in names)
    assert "create schema" not in (db.MIGRATIONS_DIR / names[0]).read_text().lower()


def test_migrate_raises_when_no_migration_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(db.MigrationsMissing, match="no migration files"):
        asyncio.run(db.migrate("postgresql://u:p@127.0.0.1:1/db"))


def test_missing_migrations_is_a_config_error_and_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LF_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(main, "_BACKOFF_START", 0.01)
    calls = 0
    real = main._try_start_db

    async def counting(a: Any, d: str) -> bool:
        nonlocal calls
        calls += 1
        return await real(a, d)

    monkeypatch.setattr(main, "_try_start_db", counting)
    with TestClient(app) as c:
        time.sleep(0.1)
        assert c.get("/api/health").json()["database"] == "unavailable"
    assert calls == 1


def test_unreachable_database_still_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LF_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}
        assert c.get("/api/health").json() == {"status": "degraded", "database": "unavailable"}


def test_malformed_url_does_not_leak_secret_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "sec"
    monkeypatch.setenv("LF_DATABASE_URL", "postgresql://u:sec/ret@host:6543/db")
    calls = 0
    real = main._try_start_db

    async def counting(a: Any, d: str) -> bool:
        nonlocal calls
        calls += 1
        return await real(a, d)

    monkeypatch.setattr(main, "_try_start_db", counting)
    monkeypatch.setattr(main, "_BACKOFF_START", 0.01)
    with caplog.at_level(logging.INFO), TestClient(app) as c:
        assert c.get("/api/health").json()["database"] == "unavailable"
        time.sleep(0.1)
    assert calls == 1
    assert secret not in caplog.text


class _FakePool:
    closed = False

    async def fetchval(self, *_: Any) -> int:
        return 1

    async def close(self) -> None:
        self.closed = True


def test_background_retry_eventually_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LF_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(main, "_BACKOFF_START", 0.01)
    attempts = 0
    pool = _FakePool()

    async def flaky_migrate(_: str) -> list[str]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("down")
        return ["001_initial"]

    async def fake_pool(_: str) -> _FakePool:
        return pool

    monkeypatch.setattr(db, "migrate", flaky_migrate)
    monkeypatch.setattr(db, "create_pool", fake_pool)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["database"] in ("unavailable", "ok")
        for _ in range(100):
            if c.get("/api/health").json() == {"status": "ok", "database": "ok"}:
                break
                time.sleep(0.05)
        else:
            pytest.fail("database never became ok")
    assert attempts == 3
    assert pool.closed


def test_background_retry_is_cancelled_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LF_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(main, "_BACKOFF_START", 3600)

    async def down(_: str) -> list[str]:
        raise OSError("down")

    monkeypatch.setattr(db, "migrate", down)
    start = time.monotonic()
    with TestClient(app) as c:
        assert c.get("/api/health").json()["database"] == "unavailable"
    # leaving the context returns promptly although the retry sleeps for an hour
    assert time.monotonic() - start < 2


def test_health_ping_times_out_instead_of_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    monkeypatch.setattr(main, "_PING_TIMEOUT", 0.05)

    class Slow(_FakePool):
        async def fetchval(self, *_: Any) -> int:
            await asyncio.sleep(5)
            return 1

    with TestClient(app) as c:
        app.state.pool = Slow()
        assert c.get("/api/health").json() == {"status": "degraded", "database": "unavailable"}
        app.state.pool = None


def test_migrate_is_idempotent_against_real_postgres() -> None:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set")
    if urlsplit(dsn).hostname not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail("LF_TEST_DATABASE_URL must point at localhost: this test drops a schema")

    async def go() -> tuple[list[str], list[str]]:
        conn = await db.connect(dsn)
        try:
            await conn.execute("drop schema if exists learning_factory cascade")
            await conn.execute("create schema learning_factory")
        finally:
            await conn.close()
        return await db.migrate(dsn), await db.migrate(dsn)

    first, second = asyncio.run(go())
    assert first == sorted(first) and first
    assert second == []
