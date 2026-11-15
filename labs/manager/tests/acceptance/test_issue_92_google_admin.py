# ruff: noqa: E501
"""Acceptance tests for issue #92: admin sign-in with Google (decision D39).

Everything runs against a fake Google: an `httpx.MockTransport` that serves the token endpoint
and the JWKS and signs ID tokens with an RSA key generated here. The manager is pointed at it
through the seam `app.state.google_transport`. No network, no real Google, no real address:
every value below is synthetic (example.test).

Needs `pyjwt[crypto]` (PyJWT + cryptography) in the manager's dependencies; a missing package
must fail these tests, not skip them.

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and skip
locally when it is absent (CI sets it and fails on any skip). Tests that need no database (flow
checks that fail before a session would be written, configuration, rate limit) use an app
started without LF_DATABASE_URL: a failed sign-in must not depend on the database.

Interface the implementation must honour beyond D39 (the exam relies on it):
- the callback URL is `/auth/google/callback?code=...&state=...`; a Google `error=` parameter
  or a missing `code` is a 403 like any other failure;
- a failure answers 403 before any database access and before any call to Google when the
  state, the cookie or the flow is wrong;
- `GET /api/auth/admin/methods` needs no login and no database.
"""
import asyncio
import base64
import hashlib
import itertools
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway

CLIENT_ID = "fake-client-92.example.test"
CLIENT_SECRET = "fake-client-" + "secret-92"  # throwaway, never a real secret
ADMIN_EMAIL = "admin.one@example.test"
ALLOW_LIST = " Admin.One@Example.TEST , second.admin@example.test "
DEFAULT_REDIRECT = "https://labs.shashkist.com/auth/google/callback"

TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
KID = "fake-kid-1"

_n = itertools.count(1)


# ---------------------------------------------------------------- database helpers (as in #6)


def _admin_dsn() -> str:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set (no Postgres available)")
    host = urlsplit(dsn).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail(f"LF_TEST_DATABASE_URL must point at a local throwaway Postgres, not {host!r}")
    return dsn


def _app_dsn(admin_dsn: str) -> str:
    parts = urlsplit(admin_dsn)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_as(dsn: str, fn):
    async def go():
        import asyncpg  # a missing asyncpg must fail, not skip

        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


def fresh_ip() -> str:
    n = next(_n)
    return f"10.92.{n // 250}.{n % 250 + 1}"


def fresh_sub() -> str:
    return f"fake-google-sub-{next(_n)}-{time.monotonic_ns()}"


# ---------------------------------------------------------------- the fake Google


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_b64u(n: int) -> str:
    return _b64u(n.to_bytes((n.bit_length() + 7) // 8, "big"))


class Keypair:
    def __init__(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        nums = key.public_key().public_numbers()
        self.jwk = {
            "kty": "RSA", "alg": "RS256", "use": "sig", "kid": KID,
            "n": _int_b64u(nums.n), "e": _int_b64u(nums.e),
        }  # fmt: skip


class FakeGoogle:
    """Serves the two Google endpoints; signs whatever claims the test asks for.

    Set `claims` (overrides; a value of None removes the claim) and optionally `sign_with`
    ("other" = a different key under the same kid, "none" = unsigned, "hs256") before the
    callback; `nonce` is filled by the test from the start redirect.
    """

    def __init__(self) -> None:
        self.keys = Keypair()
        self.other_keys = Keypair()
        self.sub = fresh_sub()
        self.email = ADMIN_EMAIL
        self.nonce = "unset"
        self.claims: dict = {}
        self.sign_with = "good"
        self.token_status = 200
        self.token_requests: list[dict[str, str]] = []
        self.jwks_requests = 0
        self.unexpected: list[str] = []
        self.issued: list[str] = []  # id tokens handed out
        self.codes: list[str] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == TOKEN_URL and request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            if form.get("code") not in self.codes:
                return httpx.Response(400, json={"error": "invalid_grant"})
            token = self.make_token()
            self.issued.append(token)
            return httpx.Response(
                200, json={"access_token": "fake-access-token", "token_type": "Bearer",
                           "expires_in": 3599, "id_token": token},
            )  # fmt: skip
        if url == JWKS_URL and request.method == "GET":
            self.jwks_requests += 1
            return httpx.Response(200, json={"keys": [self.keys.jwk]})
        self.unexpected.append(f"{request.method} {request.url}")
        return httpx.Response(404, json={"error": "not_found"})

    def new_code(self) -> str:
        code = f"fake-auth-code-{next(_n)}-{time.monotonic_ns()}"
        self.codes.append(code)
        return code

    def make_token(self) -> str:
        import jwt

        now = int(time.time())
        claims: dict = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": self.sub,
            "email": self.email,
            "email_verified": True,
            "nonce": self.nonce,
            "iat": now,
            "exp": now + 3600,
        }
        for k, v in self.claims.items():
            if v is None:
                claims.pop(k, None)
            else:
                claims[k] = v
        if self.sign_with == "other":
            return jwt.encode(claims, self.other_keys.pem, algorithm="RS256", headers={"kid": KID})
        if self.sign_with == "none":
            return jwt.encode(claims, None, algorithm="none", headers={"kid": KID})
        if self.sign_with == "hs256":
            return jwt.encode(claims, "x" * 48, algorithm="HS256", headers={"kid": KID})
        return jwt.encode(claims, self.keys.pem, algorithm="RS256", headers={"kid": KID})


# ---------------------------------------------------------------- the flow, driven like a browser


class Flow:
    def __init__(self, resp: httpx.Response):
        assert resp.status_code in (302, 303, 307), (resp.status_code, resp.text)
        self.location = resp.headers["location"]
        self.url = urlsplit(self.location)
        self.query = {k: v[0] for k, v in parse_qs(self.url.query).items()}
        self.set_cookie = next(
            (c for c in resp.headers.get_list("set-cookie") if c.startswith("lf_oauth=")), ""
        )
        m = re.match(r"lf_oauth=([^;]*)", self.set_cookie)
        self.cookie_value = m.group(1) if m else ""

    @property
    def state(self) -> str:
        return self.query.get("state", "")

    @property
    def nonce(self) -> str:
        return self.query.get("nonce", "")


def start(client, **kw) -> Flow:
    return Flow(client.get("/auth/google/start", follow_redirects=False, **kw))


def callback(client, flow: Flow, code: str, *, state: str | None = None,
             cookie: str | None = "", extra: str = "", **kw):  # fmt: skip
    """Hit the callback. `cookie`: "" = the flow's own cookie, None = send no lf_oauth cookie,
    anything else = that value. The cookie goes in an explicit header so the test controls it."""
    st = flow.state if state is None else state
    value = flow.cookie_value if cookie == "" else cookie
    headers = {"cookie": f"lf_oauth={value}" if value is not None else "unrelated=1"}
    headers.update(kw.pop("headers", {}))
    url = f"/auth/google/callback?code={code}&state={st}{extra}"
    return client.get(url, headers=headers, follow_redirects=False, **kw)


def sign_in(client, google: FakeGoogle, **claims) -> tuple[Flow, str, httpx.Response]:
    """Start, let the fake mint a token for that nonce, and call the callback."""
    flow = start(client)
    google.nonce = flow.nonce
    google.claims.update(claims)
    code = google.new_code()
    return flow, code, callback(client, flow, code)


# ---------------------------------------------------------------- fixtures


def _configure(monkeypatch, *, redirect: str | None = None) -> None:
    monkeypatch.setenv("LF_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("LF_GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("LF_ADMIN_EMAILS", ALLOW_LIST)
    monkeypatch.delenv("LF_ADMIN_PASSWORD_LOGIN", raising=False)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    if redirect:
        monkeypatch.setenv("LF_GOOGLE_REDIRECT_URI", redirect)
    else:
        monkeypatch.delenv("LF_GOOGLE_REDIRECT_URI", raising=False)


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


class Env:
    def __init__(self, client, google, admin_dsn: str | None):
        self.client = client
        self.google = google
        self.admin_dsn = admin_dsn

    def sql(self, fn):
        assert self.admin_dsn, "this test needs the database"
        return _run_as(self.admin_dsn, fn)

    def count(self, table: str) -> int:
        async def go(conn):
            return await conn.fetchval(f"select count(*) from {SCHEMA}.{table}")

        return self.sql(go)

    def sessions_for(self, subject: str) -> list[dict]:
        async def go(conn):
            rows = await conn.fetch(
                f"select kind, subject from {SCHEMA}.auth_sessions where subject = $1", subject
            )
            return [dict(r) for r in rows]

        return self.sql(go)

    def add_password_admin(self, username: str, password: str) -> None:
        from argon2 import PasswordHasher

        pw = PasswordHasher().hash(password)

        async def go(conn):
            await conn.execute(
                f"insert into {SCHEMA}.admins (username, password_hash) values ($1, $2)",
                username, pw,
            )  # fmt: skip

        self.sql(go)


def _client(app, ip: str):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="https://testserver", client=(ip, 50000))


@pytest.fixture
def nodb(monkeypatch, google):
    """Configured manager started without a database."""
    _configure(monkeypatch)
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        app.state.google_transport = google.transport
        try:
            yield Env(c, google, None)
        finally:
            app.state.google_transport = None


@pytest.fixture
def nodb_breakglass(monkeypatch, google):
    """Like `nodb`, with the break-glass flag set before the manager starts."""
    _configure(monkeypatch)
    monkeypatch.setenv("LF_ADMIN_PASSWORD_LOGIN", "1")
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        yield Env(c, google, None)


@pytest.fixture(scope="module")
def _db():
    admin_dsn = _admin_dsn()

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        await conn.execute("drop schema if exists lf_other_test cascade")
        assert OWNER_SQL.is_file(), f"{OWNER_SQL} is missing"
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)
    return admin_dsn


@pytest.fixture
def env_breakglass(_db, monkeypatch, google):
    """Like `env`, with the break-glass flag set before the manager starts."""
    _configure(monkeypatch)
    monkeypatch.setenv("LF_ADMIN_PASSWORD_LOGIN", "1")
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        yield Env(c, google, _db)


@pytest.fixture
def env(_db, monkeypatch, google):
    """Configured manager with a throwaway database."""
    _configure(monkeypatch)
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        app.state.google_transport = google.transport
        try:
            yield Env(c, google, _db)
        finally:
            app.state.google_transport = None


def _forbidden(resp: httpx.Response) -> None:
    assert resp.status_code == 403, (resp.status_code, resp.text[:300])
    assert "lf_session=" not in resp.headers.get("set-cookie", "")
    body = resp.text.lower()
    assert "traceback" not in body
    assert ADMIN_EMAIL not in body


# ================================================================== success path (database)


def test_allow_listed_verified_email_signs_in_as_admin_with_a_google_subject(env):
    g = env.google
    admins_before = env.count("admins")
    flow, code, resp = sign_in(env.client, g)
    assert resp.status_code == 303, (resp.status_code, resp.text[:300])
    assert resp.headers["location"] == "/admin"
    cookie = resp.headers.get("set-cookie", "")
    assert "lf_session=" in cookie
    assert re.search(r";\s*httponly", cookie, re.I) and re.search(r";\s*secure", cookie, re.I)
    me = env.client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"kind": "admin", "name": "admin"}
    rows = env.sessions_for(f"google:{g.sub}")
    assert rows == [{"kind": "admin", "subject": f"google:{g.sub}"}]
    assert env.count("admins") == admins_before, "a Google sign-in must not create an admins row"


def test_the_google_admin_can_reach_an_admin_only_route_and_logout_ends_the_session(env):
    sign_in(env.client, env.google)
    assert env.client.get("/api/auth/me").status_code == 200
    out = env.client.post("/api/auth/logout")
    assert out.status_code == 204
    assert env.client.get("/api/auth/me").status_code == 401
    assert env.sessions_for(f"google:{env.google.sub}") == []


@pytest.mark.parametrize("iss", ["https://accounts.google.com", "accounts.google.com"])
def test_both_google_issuer_spellings_are_accepted(env, iss):
    _, _, resp = sign_in(env.client, env.google, iss=iss)
    assert resp.status_code == 303, resp.text[:300]


def test_email_match_is_case_insensitive_both_ways(env):
    # the list holds "Admin.One@Example.TEST"; the token carries other spellings
    for spelling in ("admin.one@example.test", "ADMIN.ONE@EXAMPLE.TEST", "Second.Admin@Example.Test"):
        g = FakeGoogle()
        g.email = spelling
        client = _client(env.client.app, fresh_ip())
        client.portal = env.client.portal
        client.app.state.google_transport = g.transport
        _, _, resp = sign_in(client, g)
        assert resp.status_code == 303, (spelling, resp.status_code, resp.text[:200])


def test_the_code_verifier_sent_to_google_matches_the_challenge_and_the_request_is_complete(env):
    g = env.google
    flow, code, resp = sign_in(env.client, g)
    assert resp.status_code == 303
    assert len(g.token_requests) == 1
    req = g.token_requests[0]
    assert req["grant_type"] == "authorization_code"
    assert req["code"] == code
    assert req["client_id"] == CLIENT_ID
    assert req["client_secret"] == CLIENT_SECRET
    assert req["redirect_uri"] == DEFAULT_REDIRECT
    verifier = req["code_verifier"]
    assert 43 <= len(verifier) <= 128
    expect = _b64u(hashlib.sha256(verifier.encode()).digest())
    assert flow.query["code_challenge"] == expect
    assert g.unexpected == [], g.unexpected


# ================================================================== refusals (database: nothing stored)


def _assert_nothing_stored(env, before: tuple[int, int]) -> None:
    assert (env.count("auth_sessions"), env.count("admins")) == before
    assert env.sessions_for(f"google:{env.google.sub}") == []


def _snapshot(env) -> tuple[int, int]:
    return env.count("auth_sessions"), env.count("admins")


def test_email_not_on_the_list_is_403_and_nothing_is_stored(env):
    env.google.email = "stranger@example.test"
    before = _snapshot(env)
    _, _, resp = sign_in(env.client, env.google)
    _forbidden(resp)
    assert env.client.get("/api/auth/me").status_code == 401
    _assert_nothing_stored(env, before)


@pytest.mark.parametrize(
    "email",
    [
        "xadmin.one@example.test",  # list entry is a suffix of the token email
        "min.one@example.test",  # token email is a suffix of the list entry
        "admin.one@example.test.evil.test",  # list entry is a prefix
        "admin.one@example.tes",
        "admin.one+tag@example.test",
        "second.admin@example.test@evil.test",
        " ",
        "",
    ],
)
def test_email_match_is_exact_not_a_substring(env, email):
    env.google.email = email
    before = _snapshot(env)
    _, _, resp = sign_in(env.client, env.google)
    _forbidden(resp)
    _assert_nothing_stored(env, before)


@pytest.mark.parametrize("verified", [False, None, "true", "false", 1])
def test_email_not_verified_is_403_and_nothing_is_stored(env, verified):
    # None removes the claim; a string or a number is not the boolean true either
    before = _snapshot(env)
    _, _, resp = sign_in(env.client, env.google, email_verified=verified)
    _forbidden(resp)
    _assert_nothing_stored(env, before)


def test_missing_email_claim_is_403(env):
    before = _snapshot(env)
    _, _, resp = sign_in(env.client, env.google, email=None)
    _forbidden(resp)
    _assert_nothing_stored(env, before)


@pytest.mark.parametrize(
    ("what", "claims"),
    [
        ("wrong aud", {"aud": "someone-elses-client.example.test"}),
        ("missing aud", {"aud": None}),
        ("wrong iss", {"iss": "https://evil.example.test"}),
        ("missing iss", {"iss": None}),
        ("expired", {"iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600}),
        ("missing exp", {"exp": None}),
        ("bad nonce", {"nonce": "not-the-nonce-of-this-flow"}),
        ("missing nonce", {"nonce": None}),
    ],
)
def test_bad_id_token_claims_are_403_and_nothing_is_stored(env, what, claims):
    before = _snapshot(env)
    _, _, resp = sign_in(env.client, env.google, **claims)
    _forbidden(resp)
    _assert_nothing_stored(env, before)


@pytest.mark.parametrize("how", ["other", "none", "hs256"])
def test_id_token_with_a_bad_signature_is_403_and_nothing_is_stored(env, how):
    # "other": signed by a different RSA key under the right kid; "none": unsigned;
    # "hs256": the wrong algorithm family. Only RS256 against Google's keys is accepted.
    env.google.sign_with = how
    before = _snapshot(env)
    _, _, resp = sign_in(env.client, env.google)
    _forbidden(resp)
    _assert_nothing_stored(env, before)


def test_google_refusing_the_code_is_not_a_sign_in(env):
    env.google.token_status = 400
    before = _snapshot(env)
    _, _, resp = sign_in(env.client, env.google)
    assert resp.status_code >= 400, resp.status_code
    assert "lf_session=" not in resp.headers.get("set-cookie", "")
    _assert_nothing_stored(env, before)


def test_replayed_callback_is_403_the_second_time(env):
    g = env.google
    flow, code, first = sign_in(env.client, g)
    assert first.status_code == 303
    sessions = env.count("auth_sessions")
    second = callback(env.client, flow, code)
    _forbidden(second)
    assert env.count("auth_sessions") == sessions, "the replay must not start another session"
    assert len(g.token_requests) == 1, "the replay must not even reach Google"


# ================================================================== flow binding (no database needed)


def _assert_no_google_call(g: FakeGoogle) -> None:
    assert g.token_requests == [], "the code was exchanged although the flow was not valid"


def test_state_mismatch_is_403_and_the_code_is_not_exchanged(nodb):
    g = nodb.google
    flow = start(nodb.client)
    g.nonce = flow.nonce
    resp = callback(nodb.client, flow, g.new_code(), state="forged-state-value")
    _forbidden(resp)
    _assert_no_google_call(g)


def test_missing_lf_oauth_cookie_is_403_and_the_code_is_not_exchanged(nodb):
    g = nodb.google
    flow = start(nodb.client)
    g.nonce = flow.nonce
    resp = callback(nodb.client, flow, g.new_code(), cookie=None)
    _forbidden(resp)
    _assert_no_google_call(g)


def test_wrong_lf_oauth_cookie_value_is_403_and_the_code_is_not_exchanged(nodb):
    g = nodb.google
    flow = start(nodb.client)
    g.nonce = flow.nonce
    resp = callback(nodb.client, flow, g.new_code(), cookie="a-cookie-from-somewhere-else")
    _forbidden(resp)
    _assert_no_google_call(g)


def test_cookie_of_another_flow_does_not_complete_this_flow(nodb):
    g = nodb.google
    mine = start(nodb.client)
    theirs = start(nodb.client)
    assert mine.cookie_value != theirs.cookie_value
    g.nonce = mine.nonce
    resp = callback(nodb.client, mine, g.new_code(), cookie=theirs.cookie_value)
    _forbidden(resp)
    _assert_no_google_call(g)


def test_a_state_that_was_never_issued_is_403_even_with_a_cookie(nodb):
    g = nodb.google
    flow = start(nodb.client)
    resp = callback(nodb.client, flow, g.new_code(), state="never-issued-by-this-server-12345")
    _forbidden(resp)
    _assert_no_google_call(g)


def test_callback_without_any_parameters_is_403(nodb):
    resp = nodb.client.get("/auth/google/callback", follow_redirects=False)
    _forbidden(resp)


def test_google_error_parameter_is_403(nodb):
    g = nodb.google
    flow = start(nodb.client)
    url = f"/auth/google/callback?error=access_denied&state={flow.state}"
    resp = nodb.client.get(
        url, headers={"cookie": f"lf_oauth={flow.cookie_value}"}, follow_redirects=False
    )
    _forbidden(resp)
    _assert_no_google_call(g)


def test_callback_without_a_code_is_403(nodb):
    flow = start(nodb.client)
    url = f"/auth/google/callback?state={flow.state}"
    resp = nodb.client.get(
        url, headers={"cookie": f"lf_oauth={flow.cookie_value}"}, follow_redirects=False
    )
    _forbidden(resp)
    _assert_no_google_call(nodb.google)


# ================================================================== start (no database needed)


def test_start_redirects_to_google_with_state_nonce_and_pkce(nodb):
    resp = nodb.client.get("/auth/google/start", follow_redirects=False)
    assert resp.status_code in (302, 303, 307), (resp.status_code, resp.text[:200])
    flow = Flow(resp)
    assert flow.url.scheme == "https"
    assert flow.url.netloc == "accounts.google.com"
    q = flow.query
    assert q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == DEFAULT_REDIRECT
    assert q["response_type"] == "code"
    assert {"openid", "email"} <= set(q["scope"].split())
    assert q["code_challenge_method"] == "S256"
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", q["code_challenge"]), q["code_challenge"]
    assert len(q["state"]) >= 16 and len(q["nonce"]) >= 16
    assert q["state"] != q["nonce"]
    assert CLIENT_SECRET not in flow.location


def test_start_gives_every_flow_its_own_state_nonce_challenge_and_cookie(nodb):
    a, b = start(nodb.client), start(nodb.client)
    for key in ("state", "nonce", "code_challenge"):
        assert a.query[key] != b.query[key], key
    assert a.cookie_value != b.cookie_value
    assert a.cookie_value not in (a.state, a.nonce) and len(a.cookie_value) >= 16


def test_lf_oauth_cookie_is_httponly_secure_samesite_lax_short_lived_and_scoped(nodb):
    flow = start(nodb.client)
    c = flow.set_cookie
    assert c.startswith("lf_oauth="), c
    assert re.search(r";\s*httponly", c, re.I), c
    assert re.search(r";\s*secure", c, re.I), c
    assert re.search(r";\s*samesite=lax", c, re.I), c
    m = re.search(r";\s*max-age=(\d+)", c, re.I)
    assert m and 0 < int(m.group(1)) <= 900, c
    assert re.search(r";\s*path=/auth/google\b", c, re.I), c


def test_redirect_uri_comes_from_config_not_from_the_host_header(nodb):
    flow = start(
        nodb.client, headers={"host": "evil.example.test", "x-forwarded-host": "evil.example.test"}
    )
    assert flow.query["redirect_uri"] == DEFAULT_REDIRECT


def test_redirect_uri_can_be_set_by_config(monkeypatch, google):
    custom = "https://labs.example.test/auth/google/callback"
    _configure(monkeypatch, redirect=custom)
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        flow = start(c, headers={"host": "evil.example.test"})
    assert flow.query["redirect_uri"] == custom


def test_start_talks_to_nobody_it_only_redirects(nodb):
    start(nodb.client)
    assert nodb.google.token_requests == [] and nodb.google.jwks_requests == 0


# ================================================================== not configured


@pytest.mark.parametrize(
    "missing",
    ["LF_GOOGLE_CLIENT_ID", "LF_GOOGLE_CLIENT_SECRET", "LF_ADMIN_EMAILS", "ADMIN_EMAILS_BLANK"],
)
def test_not_configured_start_is_503_the_app_still_starts_and_methods_say_google_false(
    monkeypatch, missing
):
    _configure(monkeypatch)
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    if missing == "ADMIN_EMAILS_BLANK":
        monkeypatch.setenv("LF_ADMIN_EMAILS", "  ,  ")
    else:
        monkeypatch.delenv(missing)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        assert c.get("/healthz").status_code == 200  # the manager started
        resp = c.get("/auth/google/start", follow_redirects=False)
        assert resp.status_code == 503, (resp.status_code, resp.text[:200])
        assert "not configured" in resp.text.lower()
        assert "accounts.google.com" not in resp.headers.get("location", "")
        methods = c.get("/api/auth/admin/methods")
        assert methods.status_code == 200
        assert methods.json()["google"] is False
        assert methods.json()["password"] is True


def test_not_configured_callback_never_signs_anyone_in(monkeypatch, google):
    _configure(monkeypatch)
    monkeypatch.delenv("LF_GOOGLE_CLIENT_SECRET")
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        app.state.google_transport = google.transport
        try:
            resp = c.get(
                "/auth/google/callback?code=x&state=y",
                headers={"cookie": "lf_oauth=z"}, follow_redirects=False,
            )  # fmt: skip
        finally:
            app.state.google_transport = None
    assert resp.status_code in (403, 503)
    assert "lf_session=" not in resp.headers.get("set-cookie", "")
    assert google.token_requests == []


def test_methods_endpoint_when_configured_says_google_only(nodb):
    resp = nodb.client.get("/api/auth/admin/methods")
    assert resp.status_code == 200
    assert resp.json() == {"google": True, "password": False}


def test_methods_endpoint_shows_password_again_with_the_break_glass_flag(nodb_breakglass):
    resp = nodb_breakglass.client.get("/api/auth/admin/methods")
    assert resp.status_code == 200
    assert resp.json() == {"google": True, "password": True}


# ================================================================== password admin login (database)

PW = "break-glass-" + "pw-92"  # throwaway


def _pw_login(client, username: str, password: str):
    return client.post("/api/auth/admin/login", json={"username": username, "password": password})


def test_password_admin_login_is_refused_when_google_is_configured(env):
    username = f"fake-admin-{next(_n)}"
    env.add_password_admin(username, PW)
    resp = _pw_login(env.client, username, PW)  # correct credentials: refused all the same
    assert resp.status_code == 403
    assert resp.json() == {"detail": "password_login_disabled"}
    assert "lf_session=" not in resp.headers.get("set-cookie", "")
    assert env.client.get("/api/auth/me").status_code == 401


def test_password_admin_login_works_with_the_break_glass_flag(env_breakglass):
    env = env_breakglass
    username = f"fake-admin-{next(_n)}"
    env.add_password_admin(username, PW)
    resp = _pw_login(env.client, username, PW)
    assert resp.status_code == 200
    assert resp.json()["kind"] == "admin"
    assert env.client.get("/api/auth/me").json()["kind"] == "admin"
    assert _pw_login(env.client, username, "wrong-" + PW).status_code == 401


def test_password_admin_login_works_when_google_is_not_configured(_db, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("LF_GOOGLE_CLIENT_ID")
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        e = Env(c, None, _db)
        username = f"fake-admin-{next(_n)}"
        e.add_password_admin(username, PW)
        resp = _pw_login(c, username, PW)
        assert resp.status_code == 200, resp.text
        assert resp.json()["kind"] == "admin"


def test_a_google_session_stops_working_once_google_is_unconfigured(env, monkeypatch):
    # D39: `current_principal` accepts a `google:` admin subject only while Google is configured.
    sign_in(env.client, env.google)
    assert env.client.get("/api/auth/me").status_code == 200
    token = env.client.cookies.get("lf_session")
    assert token
    from app.main import app

    monkeypatch.delenv("LF_ADMIN_EMAILS")
    with _client(app, fresh_ip()) as c:  # the manager restarted without the allow-list
        resp = c.get("/api/auth/me", headers={"cookie": f"lf_session={token}"})
        assert resp.status_code == 401


# ================================================================== rate limit


def test_callback_is_rate_limited_per_ip_like_the_login_route(monkeypatch, google):
    _configure(monkeypatch)
    monkeypatch.setenv("LF_LOGIN_IP_LIMIT", "3")
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        app.state.google_transport = google.transport
        try:
            flow = start(c)
            for i in range(3):
                r = callback(c, flow, "some-code", state=f"forged-{i}")
                assert r.status_code == 403, (i, r.status_code)
            blocked = callback(c, flow, "some-code", state="forged-again")
            assert blocked.status_code == 429, blocked.status_code
            assert blocked.json() == {"detail": "too_many_attempts"}
            # another source address is not affected
            other = _client(app, fresh_ip())
            ok = callback(other, flow, "some-code", state="forged-other")
            assert ok.status_code == 403
        finally:
            app.state.google_transport = None


# ================================================================== logs


def _log_text(caplog, capsys) -> str:
    """Everything the manager logged. The test client's own request line (`httpx` logger, URL on
    the fake host `testserver`) is the browser side and carries the code and state by design."""
    lines = []
    for r in caplog.records:
        msg = r.getMessage()
        if r.name.startswith("httpx") and "testserver" in msg:
            continue
        lines.append(f"{r.name}: {msg} {r.exc_text or ''} {r.stack_info or ''}")
        if r.exc_info:
            lines.append(logging.Formatter().formatException(r.exc_info))
    out = capsys.readouterr()
    return "\n".join(lines) + out.out + out.err


def _assert_no_secrets_in(text: str, secrets: dict[str, str]) -> None:
    low = text.lower()
    for name, value in secrets.items():
        if value:
            assert value.lower() not in low, f"{name} appears in the logs"


def test_no_log_line_has_the_email_code_state_or_a_token_on_any_path(
    env, caplog, capsys
):
    g = env.google
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    seen: dict[str, str] = {}

    # success
    flow, code, resp = sign_in(env.client, g)
    assert resp.status_code == 303
    seen.update(code=code, state=flow.state, nonce=flow.nonce, cookie=flow.cookie_value,
                verifier=g.token_requests[-1]["code_verifier"], id_token=g.issued[-1])  # fmt: skip
    # replay
    callback(env.client, flow, code)
    # not on the list
    g2 = env.google
    g2.email = "stranger@example.test"
    flow2, code2, _ = sign_in(env.client, g2)
    seen.update(code2=code2, state2=flow2.state, id_token2=g2.issued[-1])
    # bad signature, bad nonce, state mismatch, wrong cookie
    g2.email = ADMIN_EMAIL
    g2.sign_with = "other"
    flow3, code3, _ = sign_in(env.client, g2)
    seen.update(code3=code3, state3=flow3.state, id_token3=g2.issued[-1])
    g2.sign_with = "good"
    sign_in(env.client, g2, nonce="wrong-nonce-92-logs")
    flow4 = start(env.client)
    callback(env.client, flow4, g2.new_code(), state="forged-state-92-logs")
    callback(env.client, flow4, g2.new_code(), cookie="forged-cookie-92-logs")
    seen.update(state4=flow4.state, cookie4=flow4.cookie_value)

    text = _log_text(caplog, capsys)
    assert text.strip(), "expected the manager to log something (which check failed)"
    _assert_no_secrets_in(
        text,
        {
            "email": ADMIN_EMAIL, "other email": "stranger@example.test",
            "client secret": CLIENT_SECRET, "forged state": "forged-state-92-logs",
            "forged cookie": "forged-cookie-92-logs", "wrong nonce": "wrong-nonce-92-logs",
            "sub": g.sub, **seen,
        },
    )  # fmt: skip
    # a bare local part of the address is as identifying as the address
    assert "admin.one" not in text.lower()


def test_no_log_line_has_the_email_or_state_on_the_database_free_paths(nodb, caplog, capsys):
    g = nodb.google
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    flow = start(nodb.client)
    callback(nodb.client, flow, g.new_code(), state="forged-state-92-nodb")
    callback(nodb.client, flow, g.new_code(), cookie=None)
    text = _log_text(caplog, capsys)
    _assert_no_secrets_in(
        text,
        {"state": flow.state, "forged state": "forged-state-92-nodb",
         "cookie": flow.cookie_value, "client secret": CLIENT_SECRET},
    )  # fmt: skip


def test_no_log_line_has_the_sign_in_values_even_when_the_database_is_missing(nodb, caplog, capsys):
    # a valid token for a listed address, but nowhere to store the session: whatever the answer is,
    # it must not leak the email, the code, the state or the tokens into the logs
    g = nodb.google
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    flow, code, resp = sign_in(nodb.client, g)
    assert "lf_session=" not in resp.headers.get("set-cookie", "")
    text = _log_text(caplog, capsys)
    values = {"email": ADMIN_EMAIL, "code": code, "state": flow.state, "nonce": flow.nonce,
              "cookie": flow.cookie_value, "client secret": CLIENT_SECRET}  # fmt: skip
    if g.issued:
        values["id_token"] = g.issued[-1]
    _assert_no_secrets_in(text, values)


def test_the_fake_google_stays_inside_the_two_fixed_urls(env):
    sign_in(env.client, env.google)
    assert env.google.unexpected == [], env.google.unexpected
    assert env.google.token_requests, "the exchange should have happened"
