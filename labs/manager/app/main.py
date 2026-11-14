import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request

from app import auth, db

log = logging.getLogger("lab-manager")

_BACKOFF_START = 5.0
_BACKOFF_MAX = 60.0
_PING_TIMEOUT = 2.0


async def _try_start_db(app: FastAPI, dsn: str) -> bool:
    """One attempt: migrate and create the pool. Returns False for a config error (no retry).

    Never logs the exception message (it can contain parts of the URL), except for
    PostgresError, whose sqlstate and server message carry no connection details.
    """
    try:
        applied = await db.migrate(dsn)
        app.state.pool = await db.create_pool(dsn)
    except asyncpg.PostgresError as exc:
        log.error("database unavailable: sqlstate=%s %s", exc.sqlstate, exc.message)
    except db.MigrationsMissing as exc:
        log.error("%s: not retrying", exc)
        app.state.db_state = "unavailable"
        return False
    except ValueError as exc:
        log.error("database URL is invalid (%s): not retrying", type(exc).__name__)
        app.state.db_state = "unavailable"
        return False
    except Exception as exc:
        log.error("database unavailable (%s)", type(exc).__name__)
    else:
        app.state.db_state = "ok"
        log.info("database ready, migrations applied: %s", applied or "none")
        return True
    app.state.db_state = "unavailable"
    return True


async def _retry_db(app: FastAPI, dsn: str) -> None:
    delay = _BACKOFF_START
    while True:
        await asyncio.sleep(delay)
        delay = min(delay * 2, _BACKOFF_MAX)
        if not await _try_start_db(app, dsn) or app.state.db_state == "ok":
            return


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Only the manager's own logger: never turn on INFO for every library.
    log.setLevel(logging.INFO)
    if not log.handlers and not logging.getLogger().handlers:
        log.addHandler(logging.StreamHandler())
    app.state.pool = None
    app.state.db_state = "not_configured"
    task: asyncio.Task[None] | None = None
    dsn = db.database_url_from_env()
    if dsn is None:
        log.warning("LF_DATABASE_URL is not set: running without a database (degraded)")
    elif await _try_start_db(app, dsn) and app.state.db_state != "ok":
        task = asyncio.create_task(_retry_db(app, dsn))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if app.state.pool is not None:
            await app.state.pool.close()


app = FastAPI(title="Learning Factory lab manager", lifespan=lifespan)
app.include_router(auth.router)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/health")
async def api_health(request: Request) -> dict[str, str]:
    state: str = request.app.state.db_state
    pool = request.app.state.pool
    if pool is not None:
        try:
            await asyncio.wait_for(pool.fetchval("select 1"), _PING_TIMEOUT)
            state = "ok"
        except Exception:
            state = "unavailable"
    return {"status": "ok" if state == "ok" else "degraded", "database": state}
