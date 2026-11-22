"""The terminal WebSocket path: bounded lookup, and a failed accept closes the lab connection."""
import asyncio
import threading
import uuid

import pytest
from starlette.websockets import WebSocket

from app import term_proxy


class FakeWS(WebSocket):
    def __init__(self, accept_error=None):
        super().__init__(
            {
                "type": "websocket", "path": "/s/x/term", "raw_path": b"/s/x/term",
                "query_string": b"", "headers": [], "subprotocols": [],
                "client": ("127.0.0.1", 1),
            },
            receive=None,
            send=None,
        )
        self._accept_error = accept_error

    async def accept(self, subprotocol=None, headers=None):
        if self._accept_error:
            raise self._accept_error

    async def close(self, code=1000, reason=None):
        pass


def test_hung_lookup_does_not_starve_default_executor(monkeypatch):
    asyncio.run(_test_hung_lookup_does_not_starve_default_executor(monkeypatch))


async def _test_hung_lookup_does_not_starve_default_executor(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(term_proxy, "RESOLVE_TIMEOUT", 0.2)
    monkeypatch.setattr(
        term_proxy.socket, "getaddrinfo", lambda *a, **k: release.wait(5) or []
    )
    refused = []

    async def refuse(ws, status, detail):
        refused.append((status, detail))

    monkeypatch.setattr(term_proxy, "_refuse", refuse)
    try:
        await term_proxy._bridge(FakeWS(), uuid.uuid4())
        assert refused == [(504, "terminal_timeout")]
        assert await asyncio.wait_for(asyncio.to_thread(lambda: 1), 1) == 1
    finally:
        release.set()


def test_connect_gets_ip_and_keeps_name_in_uri(monkeypatch):
    asyncio.run(_test_connect_gets_ip_and_keeps_name_in_uri(monkeypatch))


class _Pending:
    """Stands in for the object `connect(...)` returns: awaitable, takes process_redirect."""

    def __init__(self, outcome):
        self.outcome = outcome

    def __await__(self):
        async def run():
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

        return run().__await__()


async def _test_connect_gets_ip_and_keeps_name_in_uri(monkeypatch):
    async def fake_resolve(address):
        return "[::1]:7681"

    seen = {}

    def fake_connect(uri, **kw):
        seen.update(uri=uri, **kw)
        return _Pending(OSError("stop"))

    async def refuse(*a):
        pass

    monkeypatch.setattr(term_proxy, "resolve", fake_resolve)
    monkeypatch.setattr(term_proxy, "connect", fake_connect)
    monkeypatch.setattr(term_proxy, "_refuse", refuse)
    sid = uuid.uuid4()
    await term_proxy._bridge(FakeWS(), sid)
    assert seen["uri"].startswith(f"ws://lab-{sid}:7681/")
    assert seen["host"] == "::1" and seen["port"] == 7681


def test_failed_accept_closes_upstream(monkeypatch):
    asyncio.run(_test_failed_accept_closes_upstream(monkeypatch))


async def _test_failed_accept_closes_upstream(monkeypatch):
    closed = []

    class Up:
        subprotocol = None

        async def close(self):
            closed.append(True)

    async def fake_resolve(address):
        return "127.0.0.1:7681"

    def fake_connect(uri, **kw):
        return _Pending(Up())

    monkeypatch.setattr(term_proxy, "resolve", fake_resolve)
    monkeypatch.setattr(term_proxy, "connect", fake_connect)
    with pytest.raises(RuntimeError):
        await term_proxy._bridge(FakeWS(RuntimeError("gone")), uuid.uuid4())
    assert closed == [True]
