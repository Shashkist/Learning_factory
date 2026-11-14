import asyncio
import hashlib
import hmac
import time
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from app import auth, cli
from app.main import app

SECRET = "unit-test-" + "secret"  # throwaway


def test_normalize_strips_whitespace_then_leading_zeros() -> None:
    assert auth.normalize(" 0 00 12 34 ") == "1234"
    assert auth.normalize("000") == ""


def test_id_hash_is_hmac_of_normalized_id() -> None:
    expected = hmac.new(SECRET.encode(), b"1234", hashlib.sha256).hexdigest()
    assert auth.id_hash("0012 34", SECRET) == expected
    assert auth.id_hash("1234", SECRET) == expected
    assert auth.id_hash("1234", "other") != expected


def test_empty_id_never_hashes() -> None:
    assert auth.id_hash("  000 ", SECRET) == ""


def test_password_roundtrip_and_missing_hash() -> None:
    stored = auth.hash_password("pw-for-unit-test")
    assert asyncio.run(auth.verify_password(stored, "pw-for-unit-test")) is True
    assert asyncio.run(auth.verify_password(stored, "wrong")) is False
    assert asyncio.run(auth.verify_password(None, "pw-for-unit-test")) is False
    assert asyncio.run(auth.verify_password("not-a-hash", "x")) is False


def test_rate_limiter_window_and_keys() -> None:
    now = [0.0]
    rl = auth.RateLimiter(window=10, clock=lambda: now[0])
    for _ in range(3):
        assert rl.hit(("a", 3), ("", 3))
    assert not rl.hit(("a", 3))
    assert not rl.hit(("b", 3), ("a", 3))
    assert rl.hit(("b", 3))  # the refused attempt recorded nothing on b
    now[0] = 11.0
    assert rl.hit(("a", 3))


def test_rate_limiter_forgive_removes_one_event() -> None:
    rl = auth.RateLimiter()
    assert rl.hit(("a", 1))
    assert not rl.hit(("a", 1))
    rl.forgive(("a", 1))
    assert rl.hit(("a", 1))


def test_rate_limiter_sweeps_expired_keys() -> None:
    now = [0.0]
    rl = auth.RateLimiter(window=10, clock=lambda: now[0], sweep_at=3, sweep_interval=0)
    for i in range(3):
        rl.hit((f"old{i}", 1))
    now[0] = 20.0
    rl.hit(("new0", 1))
    rl.hit(("new1", 1))
    assert set(rl._events) == {"new0", "new1"}


def test_blocked_id_survives_a_flood_of_distinct_ips_past_the_cap() -> None:
    rl = auth.RateLimiter(max_keys=50, sweep_at=10, sweep_interval=0)
    for _ in range(3):
        assert rl.hit(("id:victim", 3))
    assert not rl.hit(("id:victim", 3))
    for i in range(500):
        rl.hit((f"ip:{i}", 10), ("id:other", 10_000))
    assert not rl.hit(("id:victim", 3))
    assert len([k for k in rl._events if k.startswith("ip:")]) < 60


def test_sweep_runs_at_most_once_per_interval() -> None:
    now = [0.0]
    rl = auth.RateLimiter(window=1000, clock=lambda: now[0], sweep_at=2, sweep_interval=5)
    sweeps = 0
    orig = rl._sweep

    def counting() -> None:
        nonlocal sweeps
        sweeps += 1
        orig()

    rl._sweep = counting  # type: ignore[method-assign]
    for i in range(20):
        rl.hit((f"ip:{i}", 10))
    assert sweeps == 1
    now[0] = 6.0
    rl.hit(("ip:x", 10))
    rl.hit(("ip:y", 10))
    assert sweeps == 2


def test_valid_id_rule() -> None:
    assert auth.valid_id(" 00 123 ")
    assert not auth.valid_id("000")
    assert not auth.valid_id("12a")
    assert not auth.valid_id("1" * 33)


def test_client_ip_groups_ipv6_by_64(monkeypatch) -> None:
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    one = auth.client_ip(_req("2001:db8:1:2::1"))  # type: ignore[arg-type]
    two = auth.client_ip(_req("2001:db8:1:2:ffff::9"))  # type: ignore[arg-type]
    other = auth.client_ip(_req("2001:db8:1:3::1"))  # type: ignore[arg-type]
    assert one == two != other
    assert auth.client_ip(_req("1.2.3.4")) == "1.2.3.4"  # type: ignore[arg-type]
    assert auth.client_ip(_req("not-an-ip")) == "not-an-ip"  # type: ignore[arg-type]


def _req(peer: str | None, xff: str | None = None) -> SimpleNamespace:
    headers = {"x-forwarded-for": xff} if xff else {}
    client = SimpleNamespace(host=peer) if peer else None
    return SimpleNamespace(client=client, headers=headers)


def test_client_ip_ignores_xff_without_trusted_proxy(monkeypatch) -> None:
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    assert auth.client_ip(_req("1.2.3.4", "9.9.9.9")) == "1.2.3.4"  # type: ignore[arg-type]
    assert auth.client_ip(_req(None)) == "unknown"  # type: ignore[arg-type]


def test_client_ip_uses_last_xff_hop_only_from_trusted_proxy(monkeypatch) -> None:
    monkeypatch.setenv("LF_TRUSTED_PROXY", "172.18.0.1")
    assert auth.client_ip(_req("172.18.0.1", "6.6.6.6, 5.5.5.5")) == "5.5.5.5"  # type: ignore[arg-type]
    assert auth.client_ip(_req("172.18.0.1")) == "172.18.0.1"  # type: ignore[arg-type]
    assert auth.client_ip(_req("8.8.8.8", "6.6.6.6")) == "8.8.8.8"  # type: ignore[arg-type]


class _Pool:
    """Fake asyncpg pool: counts lookups; raises `error` when set."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def fetchrow(self, *args: object) -> None:
        self.calls += 1
        if self.error:
            raise self.error
        return None


def _client(pool: _Pool) -> httpx.AsyncClient:
    app.state.pool = pool
    transport = httpx.ASGITransport(app=app, client=("10.9.9.9", 1))
    return httpx.AsyncClient(transport=transport, base_url="https://testserver")


def _isolate(monkeypatch) -> None:
    monkeypatch.setattr(auth, "limiter", auth.RateLimiter())
    monkeypatch.setattr(auth, "_verify_gate", None)
    monkeypatch.setenv("LF_ID_SECRET", SECRET)
    monkeypatch.delenv("LF_LOGIN_IP_LIMIT", raising=False)
    monkeypatch.delenv("LF_LOGIN_ID_LIMIT", raising=False)


def _login_body(i: str) -> dict[str, str]:
    return {"id": i, "password": "x"}


def test_concurrent_burst_cannot_exceed_the_limit(monkeypatch) -> None:
    _isolate(monkeypatch)
    verifies = 0

    async def slow_verify(stored: str | None, password: str) -> bool:
        nonlocal verifies
        verifies += 1
        await asyncio.sleep(0.05)
        return False

    monkeypatch.setattr(auth, "verify_password", slow_verify)
    pool = _Pool()

    async def burst() -> list[int]:
        async with _client(pool) as c:
            rs = await asyncio.gather(
                *[c.post("/api/auth/login", json=_login_body("123456")) for _ in range(50)]
            )
        return [r.status_code for r in rs]

    codes = asyncio.run(burst())
    assert codes.count(401) == 10
    assert codes.count(429) == 40
    assert verifies == 10


def test_limits_come_from_env(monkeypatch) -> None:
    _isolate(monkeypatch)
    monkeypatch.setenv("LF_LOGIN_IP_LIMIT", "2")

    async def run() -> list[int]:
        out = []
        async with _client(_Pool()) as c:
            for i in range(4):
                r = await c.post("/api/auth/login", json=_login_body(str(100 + i)))
                out.append(r.status_code)
        return out

    assert asyncio.run(run()) == [401, 401, 429, 429]


def test_busy_when_no_verify_slot_frees_up(monkeypatch) -> None:
    _isolate(monkeypatch)
    monkeypatch.setattr(auth, "VERIFY_CONCURRENCY", 1)
    monkeypatch.setattr(auth, "VERIFY_WAIT", 0.05)
    monkeypatch.setenv("LF_LOGIN_IP_LIMIT", "100")
    monkeypatch.setenv("LF_LOGIN_ID_LIMIT", "100")
    monkeypatch.setattr(auth, "_verify_sync", lambda stored, pw: time.sleep(0.4) or False)

    async def run() -> list[httpx.Response]:
        async with _client(_Pool()) as c:
            return list(
                await asyncio.gather(
                    *[c.post("/api/auth/login", json=_login_body("555")) for _ in range(3)]
                )
            )

    rs = asyncio.run(run())
    busy = [r for r in rs if r.status_code == 503]
    assert busy and busy[0].json() == {"detail": "busy"}
    assert any(r.status_code == 401 for r in rs)


def test_bad_input_is_401_without_touching_the_database(monkeypatch) -> None:
    _isolate(monkeypatch)
    pool = _Pool()
    ids = ["12ab", "0 0", "9" * 40, "\u0661\u0662\u0663"]
    names = ["a\x00b", "", "x" * 65]

    async def run() -> list[int]:
        out = []
        async with _client(pool) as c:
            for i in ids:
                out.append((await c.post("/api/auth/login", json=_login_body(i))).status_code)
            for n in names:
                body = {"username": n, "password": "x"}
                out.append((await c.post("/api/auth/admin/login", json=body)).status_code)
        return out

    assert asyncio.run(run()) == [401] * 7
    assert pool.calls == 0


def test_database_errors_after_startup_are_503(monkeypatch) -> None:
    _isolate(monkeypatch)
    pool = _Pool(error=OSError("connection reset: postgresql://user:hunter2@host/db"))

    async def run() -> httpx.Response:
        async with _client(pool) as c:
            return await c.post("/api/auth/login", json=_login_body("123"))

    r = asyncio.run(run())
    assert r.status_code == 503
    assert r.json() == {"detail": "database unavailable"}
    assert "hunter2" not in r.text


def test_login_routes_503_without_database() -> None:
    client = TestClient(app, base_url="https://testserver")  # no lifespan: no pool
    app.state.pool = None
    resp = client.post("/api/auth/login", json={"id": "1", "password": "x"})
    assert resp.status_code == 503
    admin = {"username": "a", "password": "x"}
    assert client.post("/api/auth/admin/login", json=admin).status_code == 503


def test_me_without_cookie_is_401() -> None:
    assert TestClient(app).get("/api/auth/me").status_code == 401


def test_cli_rejects_short_password_and_missing_dsn(monkeypatch, capsys) -> None:
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    assert cli.main(["create-admin", "someone"]) == 1
    monkeypatch.setenv("LF_DATABASE_URL", "postgresql://u@localhost/x")
    monkeypatch.setattr(cli, "_read_password", lambda: "short")
    assert cli.main(["create-admin", "someone"]) == 1
    assert "at least 8" in capsys.readouterr().err
