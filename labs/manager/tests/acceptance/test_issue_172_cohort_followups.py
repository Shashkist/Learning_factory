"""Acceptance tests for issue #172: follow-ups of #11 slice 1 (D59), manager side.

Spec section 9 (cohorts); Google admin sign-in is D39. The web side is pinned in
labs/web/tests/acceptance/issue-172-cohort-edit.test.tsx. Database tests need a superuser DSN to a
throwaway Postgres in LF_TEST_DATABASE_URL and skip locally when it is absent (CI sets it and
fails on any skip). Every id, name and password below is fake. Every case creates its own cohorts
with unique names and never counts rows of a whole table: other rows may exist.

CONTRACT the implementation must honour on top of #11 (test_issue_11_cohorts.py stays valid):
- 422 answers never echo the request. The JSON body keeps `detail`, but no entry has an `input`
  or a `ctx` key, and nothing the client sent (a pasted id, a name, a stray field) appears
  anywhere in the body text.
- POST /api/admin/cohorts/{id}/import: at most 2000 lines per request (non-blank lines; exactly
  2000 is fine, 2001 is a 422 and nothing is saved). A line without a comma is split at its first
  tab ("ID<TAB>name", a spreadsheet paste); a line with a comma still splits at the first comma.
- A class password of only whitespace is a 422 on create and on edit (the old password keeps
  working after a refused edit).
- `access_until` must not be before 2020-01-01 (create and edit: 422; 2020-01-01 itself is fine).
- One definition of the reserved smoke cohort: `app/teacher.py` and `app/sessions.py` do not
  compare against the smoke name inline. Each imports its Python check (and teacher.py its SQL
  fragment) from an app module under a name containing "smoke" (other than the constant
  SMOKE_COHORT), and what sessions.py imports is also imported by teacher.py, from the same module.
  A callable among them answers True for the reserved name (any case or padding), False otherwise.
- A Google admin session (subject `google:...`, D39) reaches the /api/admin/cohorts routes.
"""
import ast
import asyncio
import base64
import datetime as dt
import itertools
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
import pytest

SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
NEW_PASSWORD = "class-" + "password-2"  # throwaway
ADMIN_PASSWORD = "admin-" + "password-1"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
FUTURE = (TODAY + dt.timedelta(days=30)).isoformat()

CLIENT_ID = "fake-client-172.example.test"
CLIENT_SECRET = "fake-client-" + "secret-172"  # throwaway
ADMIN_EMAIL = "admin.one@example.test"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
KID = "fake-kid-172"

_n = itertools.count(1)

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
APP_DIR = REPO_ROOT / "labs" / "manager" / "app"


# ---------------------------------------------------------------- helpers


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


def fresh_id() -> str:
    """A fake normalized id, unique per call (own range, 9 digits)."""
    return str(172000000 + next(_n))


def fresh_ip() -> str:
    n = next(_n)
    return f"10.172.{n // 250}.{n % 250 + 1}"


def uniq(label: str) -> str:
    return f"Fake {label} {next(_n)}-{time.monotonic_ns()}"


def _hash_pw(pw: str) -> str:
    from argon2 import PasswordHasher  # a missing argon2-cffi must fail, not skip

    return PasswordHasher().hash(pw)


def _client(app, ip: str):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="https://testserver", client=(ip, 50000))


class Env:
    def __init__(self, admin_dsn: str, client, google=None):
        self.admin_dsn = admin_dsn
        self.client = client  # lifespan client, no session
        self.app = client.app
        self.google = google

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def new_client(self):
        c = _client(self.app, fresh_ip())
        c.portal = self.client.portal  # the app's pool belongs to the lifespan client's loop
        return c

    def admin(self):
        name = f"fake-admin-{next(_n)}"
        pw = _hash_pw(ADMIN_PASSWORD)

        async def go(conn):
            await conn.execute(
                f"insert into {SCHEMA}.admins (username, password_hash) values ($1, $2)", name, pw
            )

        self.sql(go)
        client = self.new_client()
        body = {"username": name, "password": ADMIN_PASSWORD}
        resp = client.post("/api/auth/admin/login", json=body)
        assert resp.status_code == 200, resp.text
        return client


@pytest.fixture(scope="module")
def _db():
    admin_dsn = _admin_dsn()

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        assert OWNER_SQL.is_file(), f"{OWNER_SQL} is missing"
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)
    return admin_dsn


@pytest.fixture
def env(_db, monkeypatch):
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    for k in ("LF_GOOGLE_CLIENT_ID", "LF_GOOGLE_CLIENT_SECRET", "LF_ADMIN_EMAILS",
              "LF_GOOGLE_REDIRECT_URI", "LF_ADMIN_PASSWORD_LOGIN"):
        monkeypatch.delenv(k, raising=False)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        yield Env(_db, c)


@pytest.fixture
def admin(env):
    return env.admin()


# ---- the cohort API, as a teacher uses it


def new_cohort(client, name=None, password=COHORT_PASSWORD, until=FUTURE):
    body = {"name": name or uniq("cohort"), "password": password, "access_until": until}
    resp = client.post("/api/admin/cohorts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def do_import(client, cid, text):
    return client.post(f"/api/admin/cohorts/{cid}/import", json={"text": text})


def import_ok(client, cid, text):
    resp = do_import(client, cid, text)
    assert resp.status_code == 200, resp.text
    return resp.json()


def students(client, cid):
    resp = client.get(f"/api/admin/cohorts/{cid}/students")
    assert resp.status_code == 200, resp.text
    return resp.json()


def student_login(env, raw_id, password):
    return env.new_client().post("/api/auth/login", json={"id": raw_id, "password": password})


def walk(value):
    """Every dict key and value in a JSON document."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)
    else:
        yield value


def assert_422_without_echo(resp, *secrets):
    assert resp.status_code == 422, (resp.status_code, resp.text[:300])
    body = resp.json()
    keys = {k for k in walk(body) if isinstance(k, str)}
    assert "detail" in body
    assert "input" not in keys and "ctx" not in keys, resp.text[:500]
    for s in secrets:
        assert s not in resp.text, f"the 422 answer echoes the request ({s!r})"


# ================================================================== 422 never echoes the request


def test_a_refused_import_text_of_the_wrong_type_is_not_sent_back(env, admin):
    cid = new_cohort(admin)["id"]
    sentinel = "172987654321"
    resp = admin.post(f"/api/admin/cohorts/{cid}/import", json={"text": int(sentinel)})
    assert_422_without_echo(resp, sentinel)


def test_a_refused_import_without_text_does_not_echo_the_stray_fields(env, admin):
    cid = new_cohort(admin)["id"]
    resp = admin.post(f"/api/admin/cohorts/{cid}/import", json={"stray": "SENTINEL-stray-172"})
    assert_422_without_echo(resp, "SENTINEL-stray-172")


def test_a_refused_paste_of_too_many_lines_does_not_send_the_raw_ids_back(env, admin):
    cid = new_cohort(admin)["id"]
    ids = [fresh_id() for _ in range(2001)]
    resp = do_import(admin, cid, "\n".join(f"{i}, Fake Name" for i in ids))
    assert_422_without_echo(resp, ids[0], ids[1000], ids[-1])


def test_a_refused_create_does_not_echo_the_name_or_the_password(env, admin):
    name = "SENTINEL-name-172-" + "Z" * 200  # over the name length limit
    resp = admin.post(
        "/api/admin/cohorts",
        json={"name": name, "password": "SENTINEL-pw-172", "access_until": FUTURE},
    )
    assert_422_without_echo(resp, "SENTINEL-name-172", "SENTINEL-pw-172", "ZZZZZZZZ")


def test_a_refused_edit_does_not_echo_the_values(env, admin):
    cid = new_cohort(admin)["id"]
    resp = admin.patch(
        f"/api/admin/cohorts/{cid}",
        json={"name": "lf-smoke", "access_until": "not-a-date-SENTINEL-172"},
    )
    assert_422_without_echo(resp, "not-a-date-SENTINEL-172", "lf-smoke")


# ================================================================== import limits and the tab split


def test_import_accepts_exactly_2000_lines(env, admin):
    cid = new_cohort(admin)["id"]
    text = "\n".join(f"{fresh_id()}, Fake Student {i}" for i in range(2000))
    out = import_ok(admin, cid, text)
    assert out["added"] == 2000
    assert len(out["rows"]) == 2000
    assert len(students(admin, cid)) == 2000


def test_import_of_2001_lines_is_a_422_and_saves_nothing(env, admin):
    cid = new_cohort(admin)["id"]
    text = "\n".join(f"{fresh_id()}, Fake Student {i}" for i in range(2001))
    assert do_import(admin, cid, text).status_code == 422
    assert students(admin, cid) == []


def test_a_line_without_a_comma_splits_at_the_first_tab(env, admin):
    cid = new_cohort(admin)["id"]
    raw = fresh_id()
    out = import_ok(admin, cid, f"{raw}\tFake Tab Student")
    assert out["added"] == 1
    assert out["rows"] == [{"line": 1, "status": "added"}]
    assert [s["name"] for s in students(admin, cid)] == ["Fake Tab Student"]
    assert student_login(env, raw, COHORT_PASSWORD).status_code == 200


def test_the_tab_split_trims_the_name(env, admin):
    cid = new_cohort(admin)["id"]
    out = import_ok(admin, cid, f"{fresh_id()}\t  Fake Spaced Name  ")
    assert out["added"] == 1
    assert [s["name"] for s in students(admin, cid)] == ["Fake Spaced Name"]


def test_a_line_with_a_comma_still_splits_at_the_comma(env, admin):
    cid = new_cohort(admin)["id"]
    out = import_ok(admin, cid, f"{fresh_id()}, Fake Comma Student")
    assert out["added"] == 1
    assert [s["name"] for s in students(admin, cid)] == ["Fake Comma Student"]


def test_a_tab_line_without_a_name_is_a_missing_name_row(env, admin):
    cid = new_cohort(admin)["id"]
    out = import_ok(admin, cid, f"{fresh_id()}\t   \n{fresh_id()}")
    assert out["added"] == 0
    assert [(r["line"], r["status"], r.get("code")) for r in out["rows"]] == [
        (1, "error", "missing_name"),
        (2, "error", "missing_name"),
    ]
    assert students(admin, cid) == []


def test_tab_lines_report_the_same_error_codes_as_comma_lines(env, admin):
    cid = new_cohort(admin)["id"]
    raw = fresh_id()
    out = import_ok(admin, cid, f"{raw}\tFake One\n{raw}\tFake Two\nabc\tFake Three")
    assert [(r["line"], r["status"], r.get("code")) for r in out["rows"]] == [
        (1, "added", None),
        (2, "error", "duplicate_in_paste"),
        (3, "error", "invalid_id"),
    ]


# ================================================================== password and date limits


@pytest.mark.parametrize("password", ["   ", "\t", " \t \n "])
def test_create_refuses_a_whitespace_only_class_password(env, admin, password):
    name = uniq("blank-pw")
    resp = admin.post(
        "/api/admin/cohorts", json={"name": name, "password": password, "access_until": FUTURE}
    )
    assert resp.status_code == 422, resp.text
    assert name not in [c["name"] for c in admin.get("/api/admin/cohorts").json()]


@pytest.mark.parametrize("password", ["   ", "\t", " \t \n "])
def test_edit_refuses_a_whitespace_only_class_password_and_keeps_the_old_one(env, admin, password):
    cohort = new_cohort(admin)
    raw = fresh_id()
    import_ok(admin, cohort["id"], f"{raw}, Fake Student")
    resp = admin.patch(f"/api/admin/cohorts/{cohort['id']}", json={"password": password})
    assert resp.status_code == 422, resp.text
    assert student_login(env, raw, COHORT_PASSWORD).status_code == 200
    assert student_login(env, raw, password).status_code != 200


def test_a_password_with_spaces_and_other_text_is_still_accepted(env, admin):
    cohort = new_cohort(admin)
    resp = admin.patch(f"/api/admin/cohorts/{cohort['id']}", json={"password": "  " + NEW_PASSWORD})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("day", ["2019-12-31", "1999-01-01", "0001-01-01"])
def test_create_refuses_an_access_date_before_2020(env, admin, day):
    name = uniq("old-date")
    resp = admin.post(
        "/api/admin/cohorts",
        json={"name": name, "password": COHORT_PASSWORD, "access_until": day},
    )
    assert resp.status_code == 422, resp.text
    assert name not in [c["name"] for c in admin.get("/api/admin/cohorts").json()]


@pytest.mark.parametrize("day", ["2019-12-31", "1999-01-01", "0001-01-01"])
def test_edit_refuses_an_access_date_before_2020_and_leaves_the_date(env, admin, day):
    cohort = new_cohort(admin)
    resp = admin.patch(f"/api/admin/cohorts/{cohort['id']}", json={"access_until": day})
    assert resp.status_code == 422, resp.text
    mine = [c for c in admin.get("/api/admin/cohorts").json() if c["id"] == cohort["id"]]
    assert mine[0]["access_until"] == FUTURE


def test_the_floor_itself_2020_01_01_is_accepted(env, admin):
    made = new_cohort(admin, until="2020-01-01")
    assert made["access_until"] == "2020-01-01"
    other = new_cohort(admin)
    resp = admin.patch(f"/api/admin/cohorts/{other['id']}", json={"access_until": "2020-01-01"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_until"] == "2020-01-01"


def test_an_access_date_in_the_recent_past_is_still_accepted(env, admin):
    day = (TODAY - dt.timedelta(days=2)).isoformat()
    cohort = new_cohort(admin)
    resp = admin.patch(f"/api/admin/cohorts/{cohort['id']}", json={"access_until": day})
    assert resp.status_code == 200, resp.text


# ================================================================== one smoke-cohort definition


def _imports(path: Path) -> dict[str, str]:
    """name -> module for every `from app... import name` in the file."""
    out: dict[str, str] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            for a in node.names:
                out[a.asname or a.name] = node.module
    return out


def _smoke_names(path: Path) -> dict[str, str]:
    return {n: m for n, m in _imports(path).items() if "smoke" in n.lower() and n != "SMOKE_COHORT"}


def test_teacher_and_sessions_share_one_smoke_cohort_definition():
    teacher, sessions = APP_DIR / "teacher.py", APP_DIR / "sessions.py"
    t_names, s_names = _smoke_names(teacher), _smoke_names(sessions)
    assert s_names, "sessions.py must import its smoke-cohort check from the shared definition"
    assert t_names, "teacher.py must import its smoke-cohort check from the shared definition"
    # One module holds the definition. The two files may use different checks from it: the
    # internal smoke lab admits the exact cohort only (#128 exam), while the teacher API refuses
    # every look-alike name (D61).
    modules = set(s_names.values()) | set(t_names.values())
    assert len(modules) == 1, f"one shared module, got {sorted(modules)}"
    for path in (teacher, sessions):
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"==\s*SMOKE_COHORT|SMOKE_COHORT\s*==", src), path.name
        assert not re.search(r"<>\s*'\{SMOKE_COHORT\}'|['\"]lf-smoke['\"]", src), path.name


def _shared_callables(path: Path) -> list:
    import importlib

    names = _smoke_names(path)
    assert names, f"{path.name} must import its smoke-cohort check from the shared definition"
    checks = [getattr(importlib.import_module(module), name) for name, module in names.items()]
    callables = [c for c in checks if callable(c)]
    assert callables, "the shared definition must include a Python check (a callable)"
    return callables


def test_the_shared_smoke_check_knows_the_reserved_name():
    # The teacher API's reserved-name check: any case, any padding.
    for check in _shared_callables(APP_DIR / "teacher.py"):
        assert check("lf-smoke") is True
        assert check("  LF-Smoke ") is True
        assert check("Fake Spring cohort") is False
    # The sessions check: the exact cohort (look-alikes are refused by the #128 exam).
    for check in _shared_callables(APP_DIR / "sessions.py"):
        assert check("lf-smoke") is True
        assert check("Fake Spring cohort") is False


# ================================================================== a Google admin session (D39)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_b64u(n: int) -> str:
    return _b64u(n.to_bytes((n.bit_length() + 7) // 8, "big"))


class FakeGoogle:
    """Serves the token endpoint and the JWKS; signs the ID token with a key made here."""

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
        self.jwk = {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": KID,
                    "n": _int_b64u(nums.n), "e": _int_b64u(nums.e)}  # fmt: skip
        self.sub = f"fake-google-sub-172-{next(_n)}-{time.monotonic_ns()}"
        self.nonce = "unset"
        self.codes: list[str] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        import jwt

        url = str(request.url).split("?")[0]
        if url == TOKEN_URL and request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form.get("code") not in self.codes:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": self.sub,
                      "email": ADMIN_EMAIL, "email_verified": True, "nonce": self.nonce,
                      "iat": now, "exp": now + 3600}  # fmt: skip
            token = jwt.encode(claims, self.pem, algorithm="RS256", headers={"kid": KID})
            return httpx.Response(200, json={"access_token": "fake-access-token",
                                             "token_type": "Bearer", "expires_in": 3599,
                                             "id_token": token})  # fmt: skip
        if url == JWKS_URL and request.method == "GET":
            return httpx.Response(200, json={"keys": [self.jwk]})
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def google_env(_db, monkeypatch):
    monkeypatch.setenv("LF_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("LF_GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("LF_ADMIN_EMAILS", ADMIN_EMAIL)
    monkeypatch.delenv("LF_ADMIN_PASSWORD_LOGIN", raising=False)
    monkeypatch.delenv("LF_GOOGLE_REDIRECT_URI", raising=False)
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    from app.main import app

    google = FakeGoogle()
    with _client(app, fresh_ip()) as c:
        app.state.google_transport = google.transport
        try:
            yield Env(_db, c, google)
        finally:
            app.state.google_transport = None


def _google_sign_in(env: Env):
    client = env.client
    start = client.get("/auth/google/start", follow_redirects=False)
    assert start.status_code in (302, 303, 307), start.text
    query = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    cookie = next(
        c for c in start.headers.get_list("set-cookie") if c.startswith("lf_oauth=")
    ).split(";")[0]
    env.google.nonce = query["nonce"]
    code = f"fake-auth-code-172-{next(_n)}"
    env.google.codes.append(code)
    resp = client.get(
        f"/auth/google/callback?code={code}&state={query['state']}",
        headers={"cookie": cookie},
        follow_redirects=False,
    )
    assert resp.status_code == 303, (resp.status_code, resp.text[:300])
    assert resp.headers["location"] == "/admin"
    return client


def test_a_google_admin_session_uses_the_cohort_routes_end_to_end(google_env):
    client = _google_sign_in(google_env)
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["kind"] == "admin"

    name = uniq("google cohort")
    cid = new_cohort(client, name=name)["id"]
    assert name in [c["name"] for c in client.get("/api/admin/cohorts").json()]

    edited = client.patch(
        f"/api/admin/cohorts/{cid}", json={"name": name + " edited", "access_until": "2027-05-05"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == name + " edited"
    assert edited.json()["access_until"] == "2027-05-05"

    assert import_ok(client, cid, f"{fresh_id()}\tFake Google Student")["added"] == 1
    assert [s["name"] for s in students(client, cid)] == ["Fake Google Student"]

    refused = client.patch(f"/api/admin/cohorts/{cid}", json={"password": "   "})
    assert_422_without_echo(refused)
