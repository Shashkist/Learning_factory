"""Google admin sign-in: the success path and session handling against a fake pool (no Postgres).

The exam covers the refusals without a database and the success path with one; this covers the
success path locally, the flow store cap and `current_principal` for a `google:` subject.
"""
import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, google_auth

CLIENT_ID = "unit-client.example.test"
EMAIL = "unit.admin@example.test"


class FakePool:
    def __init__(self, kind: str = "admin", subject: str = "google:unit-sub") -> None:
        self.row = {"kind": kind, "subject": subject}
        self.inserted: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        if sql.startswith("insert"):
            self.inserted.append(args)

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, str]:
        return self.row

    async def fetchval(self, sql: str, *args: Any) -> None:
        return None


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LF_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("LF_GOOGLE_CLIENT_SECRET", "unit-" + "secret")
    monkeypatch.setenv("LF_ADMIN_EMAILS", EMAIL)
    monkeypatch.delenv("LF_ADMIN_PASSWORD_LOGIN", raising=False)
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, base_url="https://testserver", client=("10.93.0.1", 5000))


def _fake_google(monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any]) -> None:
    async def verify(client: Any, token: str, client_id: str) -> dict[str, Any]:
        return claims

    monkeypatch.setattr(google_auth, "_verify", verify)
    handler = lambda r: httpx.Response(200, json={"id_token": "x"})  # noqa: E731
    from app.main import app

    app.state.google_transport = httpx.MockTransport(handler)


def _sign_in(c: TestClient) -> httpx.Response:
    r = c.get("/auth/google/start", follow_redirects=False)
    q = httpx.URL(r.headers["location"]).params
    return c.get(
        f"/auth/google/callback?code=c&state={q['state']}",
        headers={"cookie": f"lf_oauth={r.cookies['lf_oauth']}"},
        follow_redirects=False,
    )


def test_success_starts_a_google_admin_session_without_an_admins_row(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims: dict[str, Any] = {"iss": "accounts.google.com", "sub": "unit-sub", "email": EMAIL,
                              "email_verified": True, "nonce": "?"}  # fmt: skip
    _fake_google(monkeypatch, claims)
    with _client() as c:
        from app.main import app

        app.state.pool = pool = FakePool()
        try:
            r0 = c.get("/auth/google/start", follow_redirects=False)
            q = httpx.URL(r0.headers["location"]).params
            claims["nonce"] = q["nonce"]
            r = c.get(
                f"/auth/google/callback?code=c&state={q['state']}",
                headers={"cookie": f"lf_oauth={r0.cookies['lf_oauth']}"},
                follow_redirects=False,
            )
        finally:
            app.state.pool = None
            app.state.google_transport = None
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert "lf_session=" in r.headers["set-cookie"]
    assert [a[1:3] for a in pool.inserted] == [("admin", "google:unit-sub")]


def test_nonce_mismatch_is_refused_even_with_a_valid_token(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_google(monkeypatch, {"iss": "accounts.google.com", "sub": "s", "email": EMAIL,
                               "email_verified": True, "nonce": "other"})  # fmt: skip
    with _client() as c:
        r = _sign_in(c)
        from app.main import app

        app.state.google_transport = None
    assert r.status_code == 403


def test_pending_flows_are_capped_oldest_first(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LF_GOOGLE_START_LIMIT", "100000")
    google_auth._flows.clear()
    with _client() as c:
        first = httpx.URL(c.get("/auth/google/start", follow_redirects=False).headers["location"])
        for _ in range(google_auth.MAX_FLOWS):
            c.get("/auth/google/start", follow_redirects=False)
    assert len(google_auth._flows) <= google_auth.MAX_FLOWS
    assert first.params["state"] not in google_auth._flows


def test_google_subject_is_an_admin_named_admin_only_while_google_is_configured(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.requests import Request

    class _App:
        class state:
            pool = FakePool()

    scope = {"type": "http", "headers": [(b"cookie", b"lf_session=t")], "app": _App}
    p = asyncio.run(auth.current_principal(Request(scope)))
    assert (p.kind, p.name, p.subject) == ("admin", "admin", "google:unit-sub")
    monkeypatch.delenv("LF_ADMIN_EMAILS")
    with pytest.raises(auth.HTTPException):
        asyncio.run(auth.current_principal(Request(scope)))


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, (False, True)),
        ({"LF_ADMIN_PASSWORD_LOGIN": "1"}, (False, True)),
        ({"google": "1"}, (True, False)),
        ({"google": "1", "LF_ADMIN_PASSWORD_LOGIN": "1"}, (True, True)),
        ({"google": "1", "LF_ADMIN_PASSWORD_LOGIN": "yes"}, (True, False)),
    ],
)
def test_methods_matrix(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: tuple[bool, bool]
) -> None:
    for k in ("LF_GOOGLE_CLIENT_ID", "LF_GOOGLE_CLIENT_SECRET", "LF_ADMIN_EMAILS",
              "LF_ADMIN_PASSWORD_LOGIN"):  # fmt: skip
        monkeypatch.delenv(k, raising=False)
    if env.pop("google", None):
        monkeypatch.setenv("LF_GOOGLE_CLIENT_ID", "a")
        monkeypatch.setenv("LF_GOOGLE_CLIENT_SECRET", "b")
        monkeypatch.setenv("LF_ADMIN_EMAILS", "c@example.test")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert (auth.google_config() is not None, auth.password_login_allowed()) == expected


def test_access_log_drops_the_query_string() -> None:
    import logging

    google_auth.install_log_filter()
    google_auth.install_log_filter()
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, google_auth._StripQuery) for f in access.filters) == 1
    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/auth/google/callback?code=SECRETCODE&state=SECRETSTATE", "1.1", 303),
        None,
    )  # fmt: skip
    assert access.filter(rec)
    text = rec.getMessage()
    assert "SECRETCODE" not in text and "SECRETSTATE" not in text
    assert "/auth/google/callback" in text and "303" in text


def test_start_is_async(configured: None) -> None:
    import inspect

    assert inspect.iscoroutinefunction(google_auth.start)


def test_start_is_rate_limited_per_ip(configured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LF_GOOGLE_START_LIMIT", "3")
    auth.limiter._events.pop("gstart:10.93.0.1", None)
    with _client() as c:
        codes = [c.get("/auth/google/start", follow_redirects=False).status_code for _ in range(5)]
    auth.limiter._events.pop("gstart:10.93.0.1", None)
    assert codes == [303, 303, 303, 429, 429]


def _fail_case(caplog: pytest.LogCaptureFixture, handler: Any) -> None:
    from app.main import app

    app.state.google_transport = httpx.MockTransport(handler)
    google_auth._jwks.update(keys={}, at=0.0)
    caplog.set_level("INFO", logger="lab-manager")
    try:
        with _client() as c:
            r = _sign_in(c)
    finally:
        app.state.google_transport = None
    assert r.status_code == 403
    assert "lf_session" not in r.headers.get("set-cookie", "")
    text = caplog.text
    for secret in ("code=c", EMAIL, "unit-sub", "eyJ", "unit-secret"):
        assert secret not in text
    assert "google sign-in refused" in text


def test_token_call_timeout_is_refused(
    configured: None, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    _fail_case(caplog, handler)
    assert "ConnectTimeout" in caplog.text


def _rs256_token(kid: str | None) -> str:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return jwt.encode(
        {"sub": "unit-sub"}, key, algorithm="RS256", headers={"kid": kid} if kid else None
    )


@pytest.mark.parametrize("case", ["jwks500", "nokid", "unknownkid"])
def test_id_token_key_failures_are_refused(
    case: str, configured: None, caplog: pytest.LogCaptureFixture
) -> None:
    token = _rs256_token(None if case == "nokid" else "kid-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"id_token": token})
        if case == "jwks500":
            return httpx.Response(500)
        return httpx.Response(200, json={"keys": [{"kid": "other", "kty": "RSA"}]})

    _fail_case(caplog, handler)


def test_non_ascii_email_is_refused_before_the_allow_list(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # U+212A (Kelvin sign) lowercases to "k"
    monkeypatch.setenv("LF_ADMIN_EMAILS", "kim@example.test")
    claims: dict[str, Any] = {"iss": "accounts.google.com", "sub": "s", "email_verified": True,
                              "email": "Kim@example.test", "nonce": "?"}  # fmt: skip
    _fake_google(monkeypatch, claims)
    with _client() as c:
        from app.main import app

        r0 = c.get("/auth/google/start", follow_redirects=False)
        q = httpx.URL(r0.headers["location"]).params
        google_auth._flows[q["state"]].nonce = "?"
        r = c.get(
            f"/auth/google/callback?code=c&state={q['state']}",
            headers={"cookie": f"lf_oauth={r0.cookies['lf_oauth']}"},
            follow_redirects=False,
        )
        app.state.google_transport = None
    assert r.status_code == 403


def test_non_ascii_admin_emails_are_ignored_in_config(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LF_ADMIN_EMAILS", "Kim@example.test, ok@example.test")
    cfg = auth.google_config()
    assert cfg is not None and cfg.emails == frozenset({"ok@example.test"})


def test_colon_is_refused_in_admin_usernames(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import cli

    monkeypatch.setenv("LF_DATABASE_URL", "postgresql://x@127.0.0.1/none")
    assert cli.main(["create-admin", "google:abc"]) == 1
    monkeypatch.delenv("LF_DATABASE_URL")
    monkeypatch.delenv("LF_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("LF_GOOGLE_CLIENT_ID", raising=False)
    with _client() as c:
        from app.main import app

        app.state.pool = FakePool()
        try:
            r = c.post("/api/auth/admin/login", json={"username": "google:abc", "password": "pw"})
        finally:
            app.state.pool = None
    assert r.status_code == 401
