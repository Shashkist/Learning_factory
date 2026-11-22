import socket
import threading

import pytest

from app import auth


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY", "proxy.test")
    auth.reset_trusted_proxy_cache()
    yield
    auth.reset_trusted_proxy_cache()


def test_failed_thread_start_clears_the_in_flight_flag(monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", boom)
    auth._refresh_proxy_in_background("proxy.test")  # swallowed: logged, not raised
    assert auth._proxy_refreshing is False


def test_interrupt_from_thread_start_propagates_and_keeps_the_flag(monkeypatch):
    def interrupt(self, *a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(threading.Thread, "start", interrupt)
    with pytest.raises(KeyboardInterrupt):
        auth._refresh_proxy_in_background("proxy.test")
    assert auth._proxy_refreshing is True


def test_warm_returns_before_claiming_for_blank_or_ip(monkeypatch):
    for value in ("", "  ", "10.0.0.1"):
        monkeypatch.setenv("LF_TRUSTED_PROXY", value)
        monkeypatch.setattr(auth, "_claim_lookup", lambda: pytest.fail("claimed"))
        auth.warm_trusted_proxy()


def test_failed_thread_start_after_a_reset_leaves_the_new_generation_alone(monkeypatch):
    def reset_then_boom(self, *a, **k):
        auth.reset_trusted_proxy_cache()
        assert auth._claim_lookup() is not None  # a newer lookup is now in flight
        raise RuntimeError("no thread")

    monkeypatch.setattr(threading.Thread, "start", reset_then_boom)
    auth._refresh_proxy_in_background("proxy.test")
    assert auth._proxy_refreshing is True


def test_resolve_uses_the_generation_it_was_given(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("1.2.3.4", 0))]
    )
    old = auth._proxy_generation
    auth.reset_trusted_proxy_cache()
    assert auth._resolve_proxy("proxy.test", old) == frozenset({"1.2.3.4"})
    assert auth._proxy_cache is None
