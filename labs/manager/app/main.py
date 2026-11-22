import asyncio
import contextlib
import ipaddress
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from app import (
    auth,
    catalog,
    classtable,
    db,
    framing,
    google_auth,
    janitor,
    live,
    sessions,
    student_page,
    tasks,
    teacher,
    term_proxy,
)
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


def _browser_ipv6(addr: ipaddress.IPv6Address) -> str:
    """The host a browser puts in an Origin (WHATWG URL): compressed hex, never dotted.

    Newer Pythons print an IPv4-mapped address dotted (`::ffff:192.168.0.1`); browsers
    send `::ffff:c0a8:1`, so that case is built by hand.
    """
    if addr.ipv4_mapped is not None:
        v4 = int(addr.ipv4_mapped)
        return f"::ffff:{v4 >> 16:x}:{v4 & 0xFFFF:x}"
    return addr.compressed


_FORBIDDEN_HOST_CHARS = frozenset("#/:<>?@[\\]^|")
# WHATWG: a host whose last label is a number (decimal or 0x hex) is an IPv4 address.
_IPV4_LAST_LABEL = re.compile(r"^(\d+|0[xX][0-9a-fA-F]*)$")
_CANONICAL_IPV4 = re.compile(r"^(0|[1-9]\d{0,2})(\.(0|[1-9]\d{0,2})){3}$")


def _public_origin_problem(value: str) -> str | None:
    """What is wrong with an LF_PUBLIC_ORIGIN value, or None. Never rewrites it (D119)."""
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError:
        return "unparsable"
    if parts.scheme not in ("http", "https") or not host:
        return "no scheme or host"
    if parts.path.strip("/"):
        return "has a path"
    if parts.query:
        return "has a query"
    if parts.fragment:
        return "has a fragment"
    if parts.username is not None or parts.password is not None:
        return "has userinfo"
    if port == (443 if parts.scheme == "https" else 80):
        return "has the default port"
    if not host.isascii() or "%" in host:
        return "host is not ASCII or has a percent sign"
    if ":" in host:
        try:
            addr = ipaddress.IPv6Address(host)
        except ValueError:
            return "unparsable IPv6 literal"
        if host != _browser_ipv6(addr):
            return "IPv6 literal is not in the form a browser sends"
    else:
        if any(c in _FORBIDDEN_HOST_CHARS or c <= " " or c == "\x7f" for c in host):
            return "host has a character a browser refuses in a host"
        # A browser drops exactly one trailing dot; with two or more the host is a domain.
        bare = host[:-1] if host.endswith(".") else host
        if "" in bare.split("."):
            return "host has an empty label"
        if _IPV4_LAST_LABEL.match(bare.rsplit(".", 1)[-1]) and not (
            _CANONICAL_IPV4.match(host) and all(int(o) <= 255 for o in host.split("."))
        ):
            return "IPv4 host is not in the dotted form a browser sends"
    netloc = f"[{host}]" if ":" in host else host
    rebuilt = f"{parts.scheme}://{netloc}" + (f":{port}" if port is not None else "")
    if value.rstrip("/").lower() != rebuilt:
        return "is not exactly scheme://host[:port]"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Only the manager's own logger: never turn on INFO for every library.
    log.setLevel(logging.INFO)
    google_auth.install_log_filter()
    if not log.handlers and not logging.getLogger().handlers:
        log.addHandler(logging.StreamHandler())
    proxy_log = logging.getLogger("app.auth")
    proxy_log.setLevel(logging.INFO)
    if not proxy_log.handlers and not logging.getLogger().handlers:
        proxy_log.addHandler(logging.StreamHandler())
    try:
        # resolve once now, in a worker: logs the address, never stops startup
        await asyncio.to_thread(auth.trusted_proxy_addresses)
    except Exception:  # noqa: BLE001
        log.warning("trusted proxy lookup failed at startup")
    app.state.pool = None
    app.state.db_state = "not_configured"
    app.state.runtime = DockerRuntime()
    _load_content(app)
    janitor_task = asyncio.create_task(janitor.loop(app))
    task: asyncio.Task[None] | None = None
    public_origin = (os.environ.get("LF_PUBLIC_ORIGIN") or "").strip()
    if not public_origin:
        log.warning("LF_PUBLIC_ORIGIN is not set: the terminal WebSocket Origin check is off")
    elif problem := _public_origin_problem(public_origin):
        log.warning(
            "LF_PUBLIC_ORIGIN is malformed (%s): it must be scheme://host[:port] as the browser "
            "sends it, so the terminal WebSocket Origin check may refuse every browser",
            problem,
        )
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


class _RequestClock:
    """Start the request clock before routing, so the deadline covers the auth reads too."""

    def __init__(self, inner):  # type: ignore[no-untyped-def]
        self.inner = inner

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "http":
            db.mark_request_start()
        await self.inner(scope, receive, send)


app.add_middleware(_RequestClock)
app.add_middleware(framing.FrameGuard)
app.include_router(auth.router)
app.include_router(google_auth.router)
app.include_router(catalog.router)
app.include_router(sessions.router)
app.include_router(tasks.router)
app.include_router(teacher.router)
app.include_router(classtable.router)
app.include_router(student_page.router)
app.include_router(live.router)
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
        return FileResponse(index, headers={"Cache-Control": "no-cache", **framing.FRAME_HEADERS})

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(path: str) -> FileResponse:
        if path.split("/", 1)[0] in ("api", "s", "healthz"):
            raise HTTPException(status_code=404)
        if not path or path == "index.html":
            return page()
        try:
            target = (root / path).resolve()
            if target == index.resolve():  # an encoded `..%2F` path can still reach the shell
                return page()
            if target.is_relative_to(root) and target.is_file():
                resp = FileResponse(target)
                if (resp.media_type or "") in framing.HTML_MEDIA_TYPES:
                    resp.headers.update(framing.FRAME_HEADERS)
                return resp
        except (ValueError, OSError):
            raise HTTPException(status_code=404) from None
        # A missing asset must fail as 404 (stale bundle after a deploy), not come back as HTML.
        last = path.rsplit("/", 1)[-1]
        ext = last.rpartition(".")[2].lower() if "." in last else ""
        if path.startswith("assets/") or ext in _STATIC_EXT:
            raise HTTPException(status_code=404)
        return page()


_mount_web(os.environ.get("LF_WEB_DIR"))


async def quiet_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """A 422 never repeats the request: only the error type, location and message are kept."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = [
        {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in errors
    ]
    return JSONResponse({"detail": detail}, status_code=422)


app.add_exception_handler(RequestValidationError, quiet_validation_error)
