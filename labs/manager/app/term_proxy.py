"""Terminal proxy: `/s/<session>/term` (HTTP and WebSocket) to the ttyd of `lab-<session>`.

Only the owner of a live session gets through. The request path and query are forwarded
unchanged (ttyd runs with base path `/s/<uuid>/term/`). The `lf_session` cookie never reaches
the lab: the student has a shell there."""
import asyncio
import contextlib
import logging
import os
import socket
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.requests import HTTPConnection
from starlette.responses import StreamingResponse
from websockets.asyncio.client import connect

from app import auth, db

log = logging.getLogger("lab-manager")
T = db.SCHEMA
TTYD_PORT = 7681
METHODS = ["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"]
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade", "proxy-connection",
}
# Dropped from the client's request; the proxy sets the forwarding headers itself.
NOT_FORWARDED = HOP_BY_HOP | {
    "cookie", "host", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip",
    "forwarded",
}
WS_NOT_FORWARDED = NOT_FORWARDED | {
    "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
    "sec-websocket-protocol", "sec-websocket-accept", "origin", "content-length",
}
RESPONSE_DROP = HOP_BY_HOP | {"set-cookie"}
MAX_WS_MESSAGE = 1024 * 1024
MAX_WS_QUEUE = 16
MAX_TERMINALS_PER_SESSION = 4
RESOLVE_TIMEOUT = 5.0
# Name lookups get their own small pool: a hung lookup must never hold a thread of the default
# executor (argon2 login checks run there).
_dns_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lf-dns")
_open_terminals: dict[uuid.UUID, int] = {}
UNSENDABLE_CLOSE = {1005, 1006, 1015}

router = APIRouter()


def upstream_address(session: uuid.UUID) -> str:
    return os.environ.get("LF_TERM_UPSTREAM") or f"lab-{session}:{TTYD_PORT}"


async def authorize(conn: HTTPConnection, raw_session: str, principal: auth.Principal) -> Any:
    """The one ownership rule for a session id (task API and terminal): returns the session row.

    404 (not a UUID / unknown), 403 (not yours), 410 (ended). The caller authenticated first."""
    try:
        sid = uuid.UUID(raw_session)
    except ValueError:
        raise HTTPException(404, "no_session") from None
    with auth.db_guard():
        row = await auth._pool(conn).fetchrow(
            f"select student, status, lab_id, current_task from {T}.lab_sessions "
            "where id = $1",
            sid,
        )
    if row is None:
        raise HTTPException(404, "no_session")
    if principal.kind != "student" or row["student"] != principal.subject:
        raise HTTPException(403, "not_your_session")
    if row["status"] == "ended":
        raise HTTPException(410, "session_ended")
    return row


def forward_headers(conn: HTTPConnection, drop: set[str]) -> list[tuple[str, str]]:
    named = {t.strip().lower() for t in conn.headers.get("connection", "").split(",")}
    skip = drop | named
    out = [(k, v) for k, v in conn.headers.items() if k not in skip]
    out.append(("x-forwarded-for", auth.client_ip(conn)))
    return out


async def resolve(address: str) -> str:
    """`host:port` with the host replaced by its IP. The name goes through the event loop's
    resolver as a str (anyio would hand the system resolver an IDNA-encoded bytes host)."""
    host, _, port = address.rpartition(":")
    infos = await asyncio.wait_for(
        asyncio.get_running_loop().run_in_executor(
            _dns_pool, lambda: socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
        ),
        RESOLVE_TIMEOUT,
    )
    ip = str(infos[0][4][0])
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def _target(conn: HTTPConnection, sid: uuid.UUID) -> tuple[str, str]:
    raw = conn.scope.get("raw_path")
    path = raw.decode("latin-1") if raw else conn.url.path
    query = conn.scope.get("query_string", b"").decode("latin-1")
    return upstream_address(sid), path + (f"?{query}" if query else "")


@router.api_route("/s/{session}/term", methods=METHODS)
@router.api_route("/s/{session}/term/{rest:path}", methods=METHODS)
async def http_proxy(session: str, request: Request) -> StreamingResponse:
    principal = await auth.current_principal(request)
    await authorize(request, session, principal)
    sid = uuid.UUID(session)
    address, target = _target(request, sid)
    headers = forward_headers(request, NOT_FORWARDED)
    has_body = "content-length" in request.headers or "transfer-encoding" in request.headers
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), trust_env=False)
    try:
        ip_address = await resolve(address)
        upstream = await client.send(
            client.build_request(
                request.method,
                f"http://{ip_address}{target}",
                headers=[*headers, ("host", address)],
                content=request.stream() if has_body else None,
            ),
            stream=True,
        )
    except (httpx.TimeoutException, TimeoutError):
        await client.aclose()
        raise HTTPException(504, "terminal_timeout") from None
    except (httpx.HTTPError, OSError, IndexError, ValueError):
        await client.aclose()
        raise HTTPException(502, "terminal_unreachable") from None

    async def chunks() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    resp = StreamingResponse(chunks(), status_code=upstream.status_code)
    resp.raw_headers = [
        (k, v)
        for k, v in upstream.headers.raw
        if k.decode("latin-1").lower() not in RESPONSE_DROP
    ]
    return resp


def origin_allowed(origin: str | None, public: str | None) -> bool:
    """Is a WebSocket upgrade's Origin header acceptable? No setting: no check. No header:
    allowed (not a browser). Otherwise it must equal the setting (ignoring case, trailing /)."""
    if not public or not public.strip() or origin is None:
        return True
    return origin.strip().lower() == public.strip().rstrip("/").lower()


async def _refuse(websocket: WebSocket, status: int, detail: str) -> None:
    if "websocket.http.response" in websocket.scope.get("extensions", {}):
        await websocket.send_denial_response(JSONResponse({"detail": detail}, status_code=status))
    else:
        await websocket.close(code=1008)


@router.websocket("/s/{session}/term")
@router.websocket("/s/{session}/term/{rest:path}")
async def ws_proxy(session: str, websocket: WebSocket) -> None:
    if not origin_allowed(websocket.headers.get("origin"), os.environ.get("LF_PUBLIC_ORIGIN")):
        await _refuse(websocket, 403, "origin_not_allowed")
        return
    try:
        principal = await auth.current_principal(websocket)
        await authorize(websocket, session, principal)
        sid = uuid.UUID(session)
    except HTTPException as exc:
        detail = exc.detail["code"] if isinstance(exc.detail, dict) else str(exc.detail)
        await _refuse(websocket, exc.status_code, detail)
        return
    if _open_terminals.get(sid, 0) >= MAX_TERMINALS_PER_SESSION:
        await _refuse(websocket, 429, "too_many_terminals")
        return
    _open_terminals[sid] = _open_terminals.get(sid, 0) + 1
    try:
        await _bridge(websocket, sid)
    finally:
        if _open_terminals[sid] <= 1:
            del _open_terminals[sid]
        else:
            _open_terminals[sid] -= 1


async def _bridge(websocket: WebSocket, sid: uuid.UUID) -> None:
    address, target = _target(websocket, sid)
    wanted = list(websocket.scope.get("subprotocols") or [])
    try:
        ip_host, _, ip_port = (await resolve(address)).rpartition(":")
        upstream = await connect(
            f"ws://{address}{target}",
            host=ip_host.strip("[]"),
            port=int(ip_port),
            subprotocols=[s for s in wanted if s] or None,
            additional_headers=forward_headers(websocket, WS_NOT_FORWARDED),
            max_size=MAX_WS_MESSAGE,
            max_queue=MAX_WS_QUEUE,
            compression=None,
            proxy=None,
            open_timeout=5,
        )
    except Exception as exc:
        log.warning("terminal upstream unreachable: %s", type(exc).__name__)
        if isinstance(exc, TimeoutError):
            await _refuse(websocket, 504, "terminal_timeout")
        else:
            await _refuse(websocket, 502, "terminal_unreachable")
        return
    try:
        await websocket.accept(subprotocol=upstream.subprotocol)
    except BaseException:
        await upstream.close()
        raise

    counts = {"up": 0, "down": 0}

    async def up() -> None:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            counts["up"] += 1
            if msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])
            else:
                await upstream.send(msg.get("text") or "")

    async def down() -> None:
        async for message in upstream:
            counts["down"] += 1
            if isinstance(message, str):
                await websocket.send_text(message)
            else:
                await websocket.send_bytes(message)

    tasks: list[asyncio.Task[None]] = [asyncio.create_task(up()), asyncio.create_task(down())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        code = upstream.close_code
        log.info(
            "terminal ws closed: frames to lab %d, from lab %d, lab close code %s",
            counts["up"], counts["down"], code,
        )
        await upstream.close()
        with contextlib.suppress(Exception):
            await websocket.close(code=code if code and code not in UNSENDABLE_CLOSE else 1000)
