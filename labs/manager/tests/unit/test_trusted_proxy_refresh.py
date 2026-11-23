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


def test_lookup_after_start_failures_logs_settled_line_then_addresses_even_if_unchanged(
    monkeypatch, caplog
):
    auth.reset_trusted_proxy_cache()
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("10.0.0.7", 0))]
    )
    with caplog.at_level("INFO", logger=auth._proxy_log.name):
        auth._resolve_proxy("px530", auth._proxy_generation)
        caplog.clear()
        auth._proxy_warned["px530"] = (time.monotonic(), 3, time.monotonic())
        auth._resolve_proxy("px530", auth._proxy_generation)  # same addresses as before
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2
    assert "3 similar warnings suppressed" in msgs[0]
    assert msgs[1] == "trusted proxy px530 -> 10.0.0.7"
    auth.reset_trusted_proxy_cache()
