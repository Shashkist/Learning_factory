"""Exam for #93: LF_TRUSTED_PROXY may be a host name that is re-resolved on a short TTL.

Contract (D132): an IP literal is compared to the socket peer with no DNS; a name is resolved
with `socket.getaddrinfo(name, None)`, every returned address is trusted, the result (or the
failure) is cached for LF_TRUSTED_PROXY_TTL_S seconds (default 30). A failed or empty lookup
trusts nobody (X-Forwarded-For ignored, socket peer used). `app.auth.reset_trusted_proxy_cache()`
clears the cache. Logging: INFO `trusted proxy <name> -> <addresses>` on a change, WARNING
`trusted proxy <name>` once per failed lookup. deploy.sh exports the name (see exam #13).
"""

import logging
import socket
import time

import pytest
from starlette.requests import Request

from app import auth

NAME = "nginx-proxy-test.invalid"
A = "10.93.0.5"
B = "10.93.0.77"
REAL = "203.0.113.9"
TTL = 0.2


def _req(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    scope = {
        "type": "http", "method": "GET", "path": "/", "headers": headers,
        "client": (peer, 40000), "query_string": b"", "server": ("testserver", 443),
        "scheme": "https",
    }
    return Request(scope)


def _answer(*addrs: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addrs]


class Resolver:
    """Fake getaddrinfo: step n answers call n (the last step repeats)."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list[str] = []

    def __call__(self, host, port=None, *args, **kwargs):
        self.calls.append(host)
        step = self.steps[min(len(self.calls), len(self.steps)) - 1]
        if isinstance(step, Exception):
            raise step
        return _answer(*step)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY", NAME)
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", str(TTL))
    # Absent on main: reset through getattr so the failure is per test, not at collection.
    reset = getattr(auth, "reset_trusted_proxy_cache", lambda: None)
    reset()
    yield
    reset()


def _use(monkeypatch, *steps) -> Resolver:
    fake = Resolver(*steps)
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return fake


def _wait_ttl() -> None:
    time.sleep(TTL + 0.25)


def _proxy_lines(caplog, level: int | None = None):
    return [
        r for r in caplog.records
        if f"trusted proxy {NAME}" in r.getMessage() and (level is None or r.levelno == level)
    ]


def test_name_is_resolved_and_xff_honoured_from_its_address(monkeypatch):
    _use(monkeypatch, [A])
    assert auth.client_ip(_req(A, f"9.9.9.9, {REAL}")) == REAL


def test_peer_that_is_not_the_named_proxy_gets_no_xff_trust(monkeypatch):
    _use(monkeypatch, [A])
    assert auth.client_ip(_req(B, f"9.9.9.9, {REAL}")) == B


def test_every_address_the_name_resolves_to_is_trusted(monkeypatch):
    _use(monkeypatch, [A, B])
    assert auth.client_ip(_req(A, REAL)) == REAL
    assert auth.client_ip(_req(B, REAL)) == REAL
    assert auth.client_ip(_req("10.93.0.99", REAL)) == "10.93.0.99"


def test_address_change_new_address_trusted_and_old_one_no_longer(monkeypatch):
    _use(monkeypatch, [A], [B])
    assert auth.client_ip(_req(A, REAL)) == REAL
    _wait_ttl()
    assert auth.client_ip(_req(B, REAL)) == REAL
    assert auth.client_ip(_req(A, REAL)) == A


def test_no_new_lookup_within_the_ttl(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    fake = _use(monkeypatch, [A], [B])
    for _ in range(5):
        assert auth.client_ip(_req(A, REAL)) == REAL
    assert auth.client_ip(_req(B, REAL)) == B  # still the cached answer
    assert fake.calls == [NAME]


def test_lookup_repeats_after_the_ttl(monkeypatch):
    fake = _use(monkeypatch, [A])
    auth.client_ip(_req(A, REAL))
    _wait_ttl()
    auth.client_ip(_req(A, REAL))
    assert len(fake.calls) == 2


def test_default_ttl_is_thirty_seconds(monkeypatch):
    monkeypatch.delenv("LF_TRUSTED_PROXY_TTL_S", raising=False)
    fake = _use(monkeypatch, [A])
    auth.client_ip(_req(A, REAL))
    time.sleep(0.5)
    auth.client_ip(_req(A, REAL))
    assert len(fake.calls) == 1


def test_dns_failure_ignores_xff_and_uses_the_peer_without_raising(monkeypatch):
    _use(monkeypatch, socket.gaierror(-2, "Name or service not known"))
    assert auth.client_ip(_req(A, REAL)) == A


def test_plain_oserror_from_the_lookup_is_also_fail_closed(monkeypatch):
    _use(monkeypatch, OSError("resolver down"))
    assert auth.client_ip(_req(A, REAL)) == A


def test_failed_lookup_is_cached_for_the_ttl(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    fake = _use(monkeypatch, socket.gaierror(-2, "nope"), [A])
    for _ in range(4):
        assert auth.client_ip(_req(A, REAL)) == A
    assert len(fake.calls) == 1


def test_dns_recovers_after_the_ttl_and_trusts_again(monkeypatch):
    _use(monkeypatch, socket.gaierror(-2, "nope"), [A])
    assert auth.client_ip(_req(A, REAL)) == A
    _wait_ttl()
    assert auth.client_ip(_req(A, REAL)) == REAL


def test_empty_lookup_result_trusts_nobody(monkeypatch):
    _use(monkeypatch, [])
    for peer in (A, B, "127.0.0.1", "0.0.0.0"):
        assert auth.client_ip(_req(peer, REAL)) == peer


def test_failure_after_a_success_stops_trusting_the_old_address(monkeypatch):
    _use(monkeypatch, [A], OSError("gone"))
    assert auth.client_ip(_req(A, REAL)) == REAL
    _wait_ttl()
    assert auth.client_ip(_req(A, REAL)) == A


def _boom(*a, **k):
    raise AssertionError("getaddrinfo must not be called here")


def test_ip_literal_needs_no_lookup(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY", A)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert auth.client_ip(_req(A, f"9.9.9.9, {REAL}")) == REAL
    assert auth.client_ip(_req(B, REAL)) == B


def test_ipv6_literal_needs_no_lookup(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY", "fd93::5")
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert auth.client_ip(_req("fd93::5", REAL)) == REAL


@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_proxy_uses_the_peer(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    else:
        monkeypatch.setenv("LF_TRUSTED_PROXY", value)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert auth.client_ip(_req(A, REAL)) == A


def test_info_line_when_the_resolved_set_changes(monkeypatch, caplog):
    _use(monkeypatch, [A], [A], [B])
    with caplog.at_level(logging.INFO, logger="app.auth"):
        auth.client_ip(_req(A, REAL))
        first = _proxy_lines(caplog)
        assert len(first) == 1 and first[0].levelno == logging.INFO
        assert A in first[0].getMessage() and "->" in first[0].getMessage()
        _wait_ttl()
        auth.client_ip(_req(A, REAL))  # same set again: no new line
        assert len(_proxy_lines(caplog)) == 1
        _wait_ttl()
        auth.client_ip(_req(B, REAL))
    lines = _proxy_lines(caplog)
    assert len(lines) == 2
    assert lines[1].levelno == logging.INFO and B in lines[1].getMessage()


def test_one_warning_per_failed_lookup_not_per_request(monkeypatch, caplog):
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    _use(monkeypatch, socket.gaierror(-2, "nope"))
    with caplog.at_level(logging.INFO, logger="app.auth"):
        for _ in range(6):
            auth.client_ip(_req(A, REAL))
    assert len(_proxy_lines(caplog, logging.WARNING)) == 1


def test_a_second_failed_lookup_after_the_ttl_warns_again(monkeypatch, caplog):
    _use(monkeypatch, OSError("down"))
    with caplog.at_level(logging.INFO, logger="app.auth"):
        auth.client_ip(_req(A, REAL))
        _wait_ttl()
        auth.client_ip(_req(A, REAL))
    assert len(_proxy_lines(caplog, logging.WARNING)) == 2


def test_cache_reset_forces_a_new_lookup(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    fake = _use(monkeypatch, [A], [B])
    assert auth.client_ip(_req(A, REAL)) == REAL
    auth.reset_trusted_proxy_cache()
    assert auth.client_ip(_req(B, REAL)) == REAL
    assert len(fake.calls) == 2


def test_manager_resolves_the_name_at_startup_and_logs_it(monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    fake = _use(monkeypatch, [A])
    with caplog.at_level(logging.INFO, logger="app.auth"):
        with TestClient(app):
            pass
    assert fake.calls and fake.calls[0] == NAME
    assert any(A in r.getMessage() for r in _proxy_lines(caplog))
