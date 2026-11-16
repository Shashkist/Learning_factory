import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from app import auth, catalog, db, google_auth, janitor, sessions, tasks, teacher, term_proxy
from app.content import load_labs
from app.runtime import DockerRuntime

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


def _load_content(app: FastAPI) -> None:
    root = Path(os.environ.get("LF_CONTENT_DIR") or Path(__file__).resolve().parents[2] / "content")
    result = load_labs(root)
    app.state.labs = result.labs
    log.info("labs loaded: %d valid, %d with errors", len(result.labs), len(result.errors))
    for rel, found in result.warnings.items():
        for warning in found:
            log.warning("lab %s: %s", rel, warning)


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
    google_auth.install_log_filter()
    if not log.handlers and not logging.getLogger().handlers:
        log.addHandler(logging.StreamHandler())
    app.state.pool = None
    app.state.db_state = "not_configured"
    app.state.runtime = DockerRuntime()
    _load_content(app)
    janitor_task = asyncio.create_task(janitor.loop(app))
    task: asyncio.Task[None] | None = None
    dsn = db.database_url_from_env()
    if dsn is None:
        log.warning("LF_DATABASE_URL is not set: running without a database (degraded)")
    elif await _try_start_db(app, dsn) and app.state.db_state != "ok":
        task = asyncio.create_task(_retry_db(app, dsn))
    try:
        yield
    finally:
        janitor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await janitor_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if app.state.pool is not None:
            try:
                await app.state.pool.close()
            except Exception as exc:  # shutdown must finish; the class only, never the message
                log.warning("closing the database pool failed: %s", type(exc).__name__)


_DOCS = os.environ.get("LF_API_DOCS") == "1"
app = FastAPI(
    title="Learning Factory lab manager",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)
app.include_router(auth.router)
app.include_router(google_auth.router)
app.include_router(catalog.router)
app.include_router(sessions.router)
app.include_router(tasks.router)
app.include_router(teacher.router)
app.include_router(term_proxy.router)


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.api_route("/api/health", methods=["GET", "HEAD"])
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


_STATIC_EXT = frozenset(
    "js mjs css map ico png jpg jpeg gif svg webp webmanifest json txt woff woff2 ttf".split()
)


def _mount_web(web_dir: str | None) -> None:
    """Serve the built UI (SPA fallback). Registered last, so API routes always win."""
    root = Path(web_dir).resolve() if web_dir else None
    if root is None or not (root / "index.html").is_file():
        logging.getLogger("lab-manager").warning(
            "LF_WEB_DIR is not set or has no index.html: serving the API only"
        )
        return
    index = root / "index.html"

    def page() -> FileResponse:
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(path: str) -> FileResponse:
        if path.split("/", 1)[0] in ("api", "s", "healthz"):
            raise HTTPException(status_code=404)
        if not path or path == "index.html":
            return page()
        try:
            target = (root / path).resolve()
            if target.is_relative_to(root) and target.is_file():
                return FileResponse(target)
        except (ValueError, OSError):
            raise HTTPException(status_code=404) from None
        # A missing asset must fail as 404 (stale bundle after a deploy), not come back as HTML.
        last = path.rsplit("/", 1)[-1]
        ext = last.rpartition(".")[2].lower() if "." in last else ""
        if path.startswith("assets/") or ext in _STATIC_EXT:
            raise HTTPException(status_code=404)
        return page()


_mount_web(os.environ.get("LF_WEB_DIR"))
