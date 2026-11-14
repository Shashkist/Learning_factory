"""Acceptance tests for issue #6: student and admin login, access expiry, rate limits.

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and
skip locally when it is absent (CI sets it and fails on any skip). All ids below are
obviously fake. Rate-limit counters live in the manager process, so every test uses its
own client IP and its own ids and the file is order-independent.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

MANAGER_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
ADMIN_PASSWORD = "admin-" + "password-1"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)

_n = itertools.count(1)


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


def normalize(raw: str) -> str:
    return "".join(raw.split()).lstrip("0")


def id_hash(raw: str) -> str:
    return hmac.new(TEST_SECRET.encode(), normalize(raw).encode(), hashlib.sha256).hexdigest()


def fresh_norm_id() -> str:
    """A fake normalized id, unique per call (9 digits, no leading zero)."""
    return str(700000000 + next(_n))


def fresh_ip() -> str:
    n = next(_n)
    return f"10.66.{n // 250}.{n % 250 + 1}"


def _hash_pw(pw: str) -> str:
    from argon2 import PasswordHasher  # a missing argon2-cffi must fail, not skip

    return PasswordHasher().hash(pw)


class Env:
    def __init__(self, admin_dsn: str, app_dsn: str, client):
        self.admin_dsn = admin_dsn
        self.app_dsn = app_dsn
        self.client = client  # lifespan client, with its own cookie jar
        self.app = client.app

    def other_client(self, ip: str):
        """A client with another source IP (no lifespan: shares the running app).

        It reuses the lifespan client's event-loop portal: the app's database pool
        belongs to that loop, as it would in a real server with one loop.
        """
        from fastapi.testclient import TestClient

        other = TestClient(self.app, base_url="https://testserver", client=(ip, 50000))
        other.portal = self.client.portal
        return other

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def cohort(self, access_until: dt.date) -> int:
        pw = _hash_pw(COHORT_PASSWORD)

        async def go(conn):
            return await conn.fetchval(
                f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                "values ($1, $2, $3) returning id",
                f"Fake cohort {next(_n)}",
                pw,
                access_until,
            )

        return self.sql(go)

    def student(
        self,
        cohort_id: int,
        *,
        raw: str | None = None,
        name: str | None = None,
        access_until: dt.date | None = None,
        disabled: bool = False,
    ) -> tuple[str, str]:
        """Seed a student; returns (login id, name)."""
        raw = raw or fresh_norm_id()
        name = name or f"Fake Student {next(_n)}"

        async def go(conn):
            await conn.execute(
                f"insert into {SCHEMA}.students "
                "(id_hash, name, cohort_id, access_until, disabled) values ($1,$2,$3,$4,$5)",
                id_hash(raw),
                name,
                cohort_id,
                access_until,
                disabled,
            )

        self.sql(go)
        return raw, name

    def admin(self, username: str, password: str = ADMIN_PASSWORD) -> None:
        pw = _hash_pw(password)

        async def go(conn):
            await conn.execute(
                f"insert into {SCHEMA}.admins (username, password_hash) values ($1, $2)",
                username,
                pw,
            )

        self.sql(go)


def login(client, raw_id: str, password: str = COHORT_PASSWORD):
    return client.post("/api/auth/login", json={"id": raw_id, "password": password})


def admin_login(client, username: str, password: str):
    return client.post("/api/auth/admin/login", json={"username": username, "password": password})


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
def env(_db, monkeypatch):
    from fastapi.testclient import TestClient

    app_dsn = _app_dsn(_db)
    monkeypatch.setenv("LF_DATABASE_URL", app_dsn)
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    from app.main import app

    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        yield Env(_db, app_dsn, c)


# ---------------------------------------------------------------- student login


def test_student_login_sets_httponly_secure_cookie_and_me_returns_name(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    raw, name = env.student(cohort)
    resp = login(env.client, raw)
    assert resp.status_code == 200
    assert resp.json() == {"kind": "student", "name": name}
    cookie = resp.headers.get("set-cookie", "")
    assert cookie.startswith("lf_session="), cookie
    assert re.search(r";\s*httponly", cookie, re.I), cookie
    assert re.search(r";\s*secure", cookie, re.I), cookie
    me = env.client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"kind": "student", "name": name}


def test_me_without_login_is_401(env):
    assert env.client.get("/api/auth/me").status_code == 401


def test_id_is_normalized_spaces_and_leading_zeros(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    norm = fresh_norm_id()
    _, name = env.student(cohort, raw=norm)
    messy = f" 0 00{norm[:3]} {norm[3:]} "
    resp = login(env.client, messy)
    assert resp.status_code == 200
    assert resp.json() == {"kind": "student", "name": name}


def test_unknown_id_is_401_invalid_credentials(env):
    resp = login(env.client, fresh_norm_id())
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid_credentials"}


def test_wrong_password_is_401_with_same_body_as_unknown_id(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    raw, _ = env.student(cohort)
    wrong = login(env.client, raw, "not-the-class-password")
    unknown = login(env.client, fresh_norm_id())
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "invalid_credentials"}
    assert wrong.json() == unknown.json()
    assert env.client.get("/api/auth/me").status_code == 401


def test_disabled_student_gets_403_account_disabled(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    raw, _ = env.student(cohort, disabled=True)
    resp = login(env.client, raw)
    assert resp.status_code == 403
    assert resp.json() == {"detail": "account_disabled"}
    assert env.client.get("/api/auth/me").status_code == 401


def test_expired_cohort_gets_403_access_expired(env):
    cohort = env.cohort(TODAY - DAY)
    raw, _ = env.student(cohort)
    resp = login(env.client, raw)
    assert resp.status_code == 403
    assert resp.json() == {"detail": "access_expired"}
    assert env.client.get("/api/auth/me").status_code == 401


def test_access_until_today_is_still_valid(env):
    cohort = env.cohort(TODAY)
    raw, _ = env.student(cohort)
    assert login(env.client, raw).status_code == 200


def test_student_override_extends_access_past_expired_cohort_date(env):
    cohort = env.cohort(TODAY - 10 * DAY)
    raw, name = env.student(cohort, access_until=TODAY + 10 * DAY)
    resp = login(env.client, raw)
    assert resp.status_code == 200
    assert resp.json() == {"kind": "student", "name": name}


def test_student_override_in_the_past_beats_future_cohort_date(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    raw, _ = env.student(cohort, access_until=TODAY - DAY)
    resp = login(env.client, raw)
    assert resp.status_code == 403
    assert resp.json() == {"detail": "access_expired"}


def test_logout_returns_204_and_ends_the_session(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    raw, _ = env.student(cohort)
    assert login(env.client, raw).status_code == 200
    assert env.client.get("/api/auth/me").status_code == 200
    out = env.client.post("/api/auth/logout")
    assert out.status_code == 204
    assert env.client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------- admin login


def test_admin_login_success_sets_cookie_and_me_says_admin(env):
    username = f"fake-admin-{next(_n)}"
    env.admin(username)
    resp = admin_login(env.client, username, ADMIN_PASSWORD)
    assert resp.status_code == 200
    assert resp.json()["kind"] == "admin"
    cookie = resp.headers.get("set-cookie", "")
    assert cookie.startswith("lf_session="), cookie
    assert re.search(r";\s*httponly", cookie, re.I) and re.search(r";\s*secure", cookie, re.I)
    me = env.client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["kind"] == "admin"


def test_admin_login_wrong_password_and_unknown_user_are_401(env):
    username = f"fake-admin-{next(_n)}"
    env.admin(username)
    wrong = admin_login(env.client, username, "nope-nope-nope")
    unknown = admin_login(env.client, f"fake-nobody-{next(_n)}", ADMIN_PASSWORD)
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "invalid_credentials"}
    assert unknown.status_code == 401
    assert unknown.json() == wrong.json()
    assert env.client.get("/api/auth/me").status_code == 401


def test_student_cohort_password_does_not_open_admin_login(env):
    username = f"fake-admin-{next(_n)}"
    env.admin(username)
    assert admin_login(env.client, username, COHORT_PASSWORD).status_code == 401


# ---------------------------------------------------------------- rate limits


def test_per_id_rate_limit_blocks_eleventh_attempt_even_with_correct_password(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    raw, _ = env.student(cohort)
    for i in range(10):
        r = login(env.client, raw, f"wrong-{i}")
        assert r.status_code == 401, (i, r.text)
    blocked = login(env.client, raw)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "too_many_attempts"}
    # From another source IP the id is still blocked: the limit is per id hash.
    elsewhere = env.other_client(fresh_ip())
    again = login(elsewhere, raw)
    assert again.status_code == 429
    assert again.json() == {"detail": "too_many_attempts"}


def test_per_ip_rate_limit_applies_across_different_ids(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    raw, _ = env.student(cohort)  # never failed itself
    for i in range(10):
        r = login(env.client, fresh_norm_id(), "whatever")
        assert r.status_code == 401, (i, r.text)
    blocked = login(env.client, raw)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "too_many_attempts"}
    # A different IP is not affected by this IP's failures.
    ok = login(env.other_client(fresh_ip()), raw)
    assert ok.status_code == 200


# ---------------------------------------------------------------- raw id stays out of logs, tables


def test_raw_id_never_appears_in_logs(env, caplog, capsys):
    cohort = env.cohort(TODAY + 30 * DAY)
    good, _ = env.student(cohort)
    bad = fresh_norm_id()
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    login(env.client, good)
    login(env.client, good, "wrong-password")
    login(env.client, bad)
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err
    for raw in (good, bad):
        assert raw not in text
        assert normalize(raw) not in text
    for r in caplog.records:
        assert good not in r.getMessage() and bad not in r.getMessage()
        assert good not in str(r.args) and bad not in str(r.args)


def test_raw_id_is_not_stored_in_any_text_column(env):
    cohort = env.cohort(TODAY + 30 * DAY)
    raw, _ = env.student(cohort)
    padded = f"00{raw}"
    assert login(env.client, padded).status_code == 200
    assert login(env.client, padded, "wrong-password").status_code == 401

    async def scan(conn):
        cols = await conn.fetch(
            """select c.table_name, c.column_name
               from information_schema.columns c
               join information_schema.tables t
                 on t.table_schema = c.table_schema and t.table_name = c.table_name
               where c.table_schema = $1 and t.table_type = 'BASE TABLE'
                 and c.data_type in ('text', 'character varying', 'character')""",
            SCHEMA,
        )
        assert cols, "no text columns found: schema missing?"
        hits = []
        for c in cols:
            n = await conn.fetchval(
                f'select count(*) from {SCHEMA}."{c["table_name"]}" '
                f'where "{c["column_name"]}" like $1',
                f"%{raw}%",
            )
            if n:
                hits.append((c["table_name"], c["column_name"]))
        return hits

    assert env.sql(scan) == []


# ---------------------------------------------------------------- degraded mode


def test_login_answers_503_when_id_secret_is_unset(_db, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.delenv("LF_ID_SECRET", raising=False)
    from app.main import app

    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        resp = login(c, fresh_norm_id())
        assert resp.status_code == 503


# ---------------------------------------------------------------- admin CLI


def test_cli_create_admin_reads_password_from_stdin_and_login_works(env):
    username = f"cli-admin-{next(_n)}"
    password = "cli-" + "admin-pw-1"  # throwaway
    child_env = {k: v for k, v in os.environ.items() if k != "LF_ID_SECRET"}
    child_env["LF_DATABASE_URL"] = env.app_dsn
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "create-admin", username],
        input=password + "\n",
        text=True,
        capture_output=True,
        cwd=MANAGER_DIR,
        env=child_env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert password not in proc.stdout + proc.stderr
    ok = admin_login(env.client, username, password)
    assert ok.status_code == 200
    assert ok.json()["kind"] == "admin"
    other = env.other_client(fresh_ip())
    assert admin_login(other, username, "wrong-" + password).status_code == 401
