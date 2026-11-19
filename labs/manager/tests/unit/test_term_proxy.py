"""Unit tests for the terminal proxy with a fake pool and a stub ttyd (no database)."""
import datetime as dt
import threading
import time
import uuid
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app import auth, term_proxy

SID = str(uuid.uuid4())


class FakePool:
    def __init__(self) -> None:
        self.owner = "owner-hash"
        self.status = "running"
        self.disabled = False

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "auth_sessions" in sql:
            if args[0] == auth._token_hash("good"):
                return {"kind": "student", "subject": "owner-hash"}
            return None
        if "from learning_factory.students" in sql:
            return {
                "name": "n",
                "disabled": self.disabled,
                "password_hash": "x",
                "until": dt.date(2999, 1, 1),
            }
        if str(args[0]) == SID:
            return {"student": self.owner, "status": self.status}
        return None


@pytest.fixture(scope="module")
def stub():
    seen: list[dict[str, Any]] = []

    async def page(request):
        seen.append(
            {
                "path": request.url.path,
                "query": request.url.query,
                "headers": dict(request.headers),
                "body": await request.body(),
            }
        )
        return PlainTextResponse("hi", status_code=418, headers={"x-stub": "1"})

    async def sock(ws):
        await ws.accept(subprotocol="tty" if "tty" in ws.scope["subprotocols"] else None)
        if ws.url.path.endswith("/tt/ws"):  # ttyd: silent until the JSON init, then title, prefs
            init = await ws.receive_bytes()
            assert init.startswith(b"{")
            await ws.send_bytes(b"1title")
            await ws.send_bytes(b"2{}")
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            if m.get("bytes") is not None:
                await ws.send_bytes(m["bytes"])
            else:
                await ws.send_text(m["text"])
                if m["text"] == "bye":
                    await ws.close(1000)
                    return

    app = Starlette(
        routes=[
            WebSocketRoute("/{r:path}", sock),
            Route("/{r:path}", page, methods=["GET", "POST"]),
        ]
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"127.0.0.1:{port}", seen
    server.should_exit = True


@pytest.fixture
def ctx(stub, monkeypatch):
    addr, seen = stub
    seen.clear()
    monkeypatch.setenv("LF_TERM_UPSTREAM", addr)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    app = FastAPI()
    app.include_router(term_proxy.router)
    app.state.pool = FakePool()
    client = TestClient(app, client=("10.1.2.3", 5000))
    client.cookies.set("lf_session", "good")
    return client, app.state.pool, seen


def test_proxies_status_body_path_query_and_strips_the_cookie(ctx):
    client, _, seen = ctx
    r = client.post(
        f"/s/{SID}/term/a/b?x=1",
        content=b"body",
        headers={"cookie": "lf_session=good; k=v", "x-forwarded-for": "6.6.6.6"},
    )
    assert (r.status_code, r.text, r.headers["x-stub"]) == (418, "hi", "1")
    s = seen[0]
    assert (s["path"], s["query"], s["body"]) == (f"/s/{SID}/term/a/b", "x=1", b"body")
    assert "cookie" not in s["headers"]
    assert s["headers"]["x-forwarded-for"] == "10.1.2.3"


def test_refusals_never_reach_the_lab(ctx):
    client, pool, seen = ctx
    assert client.get("/s/not-a-uuid/term/").status_code == 404
    assert client.get(f"/s/{uuid.uuid4()}/term/").status_code == 404
    pool.owner = "someone-else"
    assert client.get(f"/s/{SID}/term/").status_code == 403
    pool.owner = "owner-hash"
    pool.status = "ended"
    assert client.get(f"/s/{SID}/term/").status_code == 410
    pool.status = "running"
    pool.disabled = True
    assert client.get(f"/s/{SID}/term/").status_code == 401
    client.cookies.clear()
    assert client.get(f"/s/{SID}/term/").status_code == 401
    assert seen == []


def test_dead_upstream_is_502(ctx, monkeypatch):
    client, _, _ = ctx
    monkeypatch.setenv("LF_TERM_UPSTREAM", "127.0.0.1:1")
    assert client.get(f"/s/{SID}/term/").status_code == 502


def test_default_upstream_is_the_lab_container(monkeypatch):
    monkeypatch.delenv("LF_TERM_UPSTREAM", raising=False)
    assert term_proxy.upstream_address(uuid.UUID(SID)) == f"lab-{SID}:7681"


def test_websocket_relays_frames_subprotocol_and_close(ctx):
    client, _, _ = ctx
    with client.websocket_connect(f"/s/{SID}/term/ws", subprotocols=["tty"]) as ws:
        assert ws.accepted_subprotocol == "tty"
        ws.send_bytes(b"\x00" * 300_000)
        assert ws.receive_bytes() == b"\x00" * 300_000
        ws.send_text("bye")
        assert ws.receive_text() == "bye"
        assert ws.receive()["type"] == "websocket.close"


def test_websocket_refusal_never_reaches_the_lab(ctx):
    client, pool, seen = ctx
    pool.owner = "someone-else"
    with pytest.raises((WebSocketDenialResponse, WebSocketDisconnect)):
        with client.websocket_connect(f"/s/{SID}/term/ws"):
            pass
    assert seen == []


def _spy_resolver(monkeypatch):
    import socket

    looked_up: list[tuple[Any, Any]] = []
    real = socket.getaddrinfo

    def spy(host, port, *args, **kwargs):
        if isinstance(host, str) and host.startswith("lab-"):
            looked_up.append((host, port))
            raise socket.gaierror(socket.EAI_NONAME, "no such lab")
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", spy)
    return looked_up


def test_http_default_upstream_is_resolved_by_name_on_7681(ctx, monkeypatch):
    client, _, _ = ctx
    monkeypatch.delenv("LF_TERM_UPSTREAM")
    looked_up = _spy_resolver(monkeypatch)
    assert client.get(f"/s/{SID}/term/").status_code == 502
    assert {(h, int(p)) for h, p in looked_up} == {(f"lab-{SID}", 7681)}


def test_websocket_default_upstream_is_resolved_by_name_on_7681(ctx, monkeypatch):
    client, _, _ = ctx
    monkeypatch.delenv("LF_TERM_UPSTREAM")
    looked_up = _spy_resolver(monkeypatch)
    with pytest.raises((WebSocketDenialResponse, WebSocketDisconnect)):
        with client.websocket_connect(f"/s/{SID}/term/ws", subprotocols=["tty"]):
            pass
    assert {(h, int(p)) for h, p in looked_up} == {(f"lab-{SID}", 7681)}


def test_websocket_like_ttyd_waits_for_the_json_init_then_streams(ctx):
    client, _, _ = ctx
    with client.websocket_connect(f"/s/{SID}/term/tt/ws", subprotocols=["tty"]) as ws:
        ws.send_bytes(b'{"AuthToken":"","columns":80,"rows":24}')
        ws.send_bytes(b"0echo hi\r")
        assert ws.receive_bytes() == b"1title"
        assert ws.receive_bytes() == b"2{}"
        assert ws.receive_bytes() == b"0echo hi\r"


def test_websocket_refusal_for_a_coded_detail_sends_the_code(monkeypatch):
    import asyncio

    from fastapi import HTTPException

    sent = []

    async def refuse(websocket, status, detail):
        sent.append((status, detail))

    async def busy(conn):
        raise HTTPException(503, {"code": "db_busy", "message": "busy"})

    monkeypatch.setattr("app.term_proxy._refuse", refuse)
    monkeypatch.setattr("app.term_proxy.auth.current_principal", busy)
    asyncio.run(term_proxy.ws_proxy("x", object()))
    assert sent == [(503, "db_busy")]
