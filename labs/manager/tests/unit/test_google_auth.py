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


def test_pending_flows_are_capped_oldest_first(configured: None) -> None:
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
