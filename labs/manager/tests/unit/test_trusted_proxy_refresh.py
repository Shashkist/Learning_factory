import asyncio
import socket
import threading
import time

from app import auth


def test_only_one_refresh_runs_at_a_time_and_reset_drops_its_answer(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY", "nginx-proxy3")
    auth.reset_trusted_proxy_cache()
    gate = threading.Event()
    calls: list[str] = []

    def slow(host, port=None, *args, **kwargs):
        calls.append(host)
        gate.wait(2.0)
        return [(socket.AF_INET, 0, 0, "", ("10.0.0.9", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", slow)

    async def scenario():
        for _ in range(5):
            assert auth.trusted_proxy_addresses() == frozenset()
        await asyncio.sleep(0.2)
        auth.reset_trusted_proxy_cache()
        gate.set()
        await asyncio.sleep(0.3)
        return auth.trusted_proxy_addresses()

    asyncio.run(scenario())
    assert calls[0] == "nginx-proxy3" and len(calls) == 2  # one before reset, one after
    time.sleep(0.3)
    auth.reset_trusted_proxy_cache()
