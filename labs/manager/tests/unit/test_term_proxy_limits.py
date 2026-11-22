import asyncio
import socket
import threading

import pytest

from app import term_proxy as tp


def test_resolve_uses_its_own_pool_and_times_out(monkeypatch):
    release = threading.Event()
    names: list[str] = []

    def hung(host, port, *a, **k):
        names.append(threading.current_thread().name)
        release.wait(5)
        raise socket.gaierror(socket.EAI_NONAME, "hung")

    monkeypatch.setattr(socket, "getaddrinfo", hung)
    monkeypatch.setattr(tp, "RESOLVE_TIMEOUT", 0.1)
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(tp.resolve("lab-x.test:7681"))
    finally:
        release.set()
    assert names and names[0].startswith("lf-dns")


def test_resolve_brackets_ipv6(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("::1", 7681, 0, 0))]
    )
    assert asyncio.run(tp.resolve("lab-x:7681")) == "[::1]:7681"
