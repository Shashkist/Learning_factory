"""Acceptance tests for issue #86, part 1: `python -m app.cli smoke-student` (decision D36).

The command makes the synthetic smoke student the live terminal smoke logs in as: cohort
`lf-smoke`, student id 990000000000000000000000000001 (30 digits, never a real id), name
`Smoke Test`. The class password comes from stdin. Everything is checked through the database
and through a real login against the app, as the smoke does.

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and skip
locally when it is absent (CI sets it and fails on any skip). The tests about missing
configuration, a short password and secret-free output need no database and always run.
Passwords below are throwaway test values.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import os
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
PW1 = "smoke-run-" + "one-0001"  # throwaway
PW2 = "smoke-run-" + "two-0002"  # throwaway
SMOKE_ID = "990000000000000000000000000001"
SMOKE_COHORT = "lf-smoke"
SMOKE_NAME = "Smoke Test"
DAY = dt.timedelta(days=1)
DEAD_DSN_MARKER = "dsn-marker-" + "zq81"  # must never show up in any output
DEAD_DSN = f"postgresql://nobody:{DEAD_DSN_MARKER}@127.0.0.1:1/nodb"

_n = itertools.count(1)


def id_hash(raw: str) -> str:
    return hmac.new(TEST_SECRET.encode(), raw.lstrip("0").encode(), hashlib.sha256).hexdigest()


def tomorrows() -> set[dt.date]:
    """Tomorrow in UTC or in local time (the run may straddle midnight)."""
    return {dt.datetime.now(dt.UTC).date() + DAY, dt.date.today() + DAY}


def run_cli(
    *args: str, stdin: str | None, dsn: str | None, secret: str | None = TEST_SECRET
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("LF_DATABASE_URL", "LF_ID_SECRET")}
    if dsn is not None:
        env["LF_DATABASE_URL"] = dsn
    if secret is not None:
        env["LF_ID_SECRET"] = secret
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "smoke-student", *args],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=MANAGER_DIR,
        env=env,
        timeout=90,
    )


def one_line(text: str) -> bool:
    return len([ln for ln in text.splitlines() if ln.strip()]) == 1


# ---------------------------------------------------------------- no database needed


def test_without_a_database_url_it_fails_with_a_clear_one_line_error():
    res = run_cli(stdin=PW1 + "\n", dsn=None)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "LF_DATABASE_URL" in res.stderr
    assert one_line(res.stderr), res.stderr
    assert PW1 not in res.stdout + res.stderr


def test_without_an_id_secret_it_fails_with_a_clear_one_line_error():
    res = run_cli(stdin=PW1 + "\n", dsn=DEAD_DSN, secret=None)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "LF_ID_SECRET" in res.stderr
    assert one_line(res.stderr), res.stderr
    assert PW1 not in res.stdout + res.stderr


def test_disable_without_the_environment_also_fails_with_a_clear_error():
    res = run_cli("--disable", stdin=None, dsn=None)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "LF_DATABASE_URL" in res.stderr
    res = run_cli("--disable", stdin=None, dsn=DEAD_DSN, secret=None)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "LF_ID_SECRET" in res.stderr


def test_a_password_shorter_than_8_is_refused_and_not_echoed():
    short = "abc" + "defg"  # 7 characters
    res = run_cli(stdin=short + "\n", dsn=DEAD_DSN)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "8" in res.stderr
    assert short not in res.stdout + res.stderr
    assert DEAD_DSN_MARKER not in res.stdout + res.stderr


def test_an_empty_stdin_is_refused():
    res = run_cli(stdin="", dsn=DEAD_DSN)
    assert res.returncode == 1, res.stdout + res.stderr
    assert DEAD_DSN_MARKER not in res.stdout + res.stderr


def test_a_database_failure_never_prints_the_dsn_or_the_password():
    res = run_cli(stdin=PW1 + "\n", dsn=DEAD_DSN)
    assert res.returncode == 1, res.stdout + res.stderr
    out = res.stdout + res.stderr
    assert DEAD_DSN_MARKER not in out
    assert PW1 not in out
    assert "Traceback" not in out


# ---------------------------------------------------------------- database


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
    return f"10.86.{n // 250}.{n % 250 + 1}"


class Env:
    def __init__(self, admin_dsn: str, app_dsn: str, client):
        self.admin_dsn, self.app_dsn, self.client = admin_dsn, app_dsn, client

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def other_client(self):
        from fastapi.testclient import TestClient

        other = TestClient(
            self.client.app, base_url="https://testserver", client=(fresh_ip(), 50000)
        )
        other.portal = self.client.portal
        return other

    def smoke(self, password: str = PW1) -> subprocess.CompletedProcess:
        res = run_cli(stdin=password + "\n", dsn=self.app_dsn)
        assert res.returncode == 0, res.stdout + res.stderr
        assert password not in res.stdout + res.stderr
        assert self.app_dsn not in res.stdout + res.stderr
        return res

    def disable(self) -> subprocess.CompletedProcess:
        res = run_cli("--disable", stdin=None, dsn=self.app_dsn)
        assert res.returncode == 0, res.stdout + res.stderr
        return res

    def cohorts(self) -> list:
        async def go(conn):
            return await conn.fetch(
                f"select id, password_hash, access_until from {SCHEMA}.cohorts where name = $1",
                SMOKE_COHORT,
            )

        return self.sql(go)

    def student(self):
        async def go(conn):
            return await conn.fetch(
                f"select name, cohort_id, disabled from {SCHEMA}.students where id_hash = $1",
                id_hash(SMOKE_ID),
            )

        return self.sql(go)

    def auth_sessions(self, subject: str) -> int:
        async def go(conn):
            return await conn.fetchval(
                f"select count(*) from {SCHEMA}.auth_sessions where subject = $1", subject
            )

        return self.sql(go)

    def login(self, client=None, password: str = PW1):
        return (client or self.client).post(
            "/api/auth/login", json={"id": SMOKE_ID, "password": password}
        )


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
    from fastapi.testclient import TestClient

    app_dsn = _app_dsn(_db)
    monkeypatch.setenv("LF_DATABASE_URL", app_dsn)
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    from app.main import app

    # the lifespan migrates first; then every test starts with no smoke rows
    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        e = Env(_db, app_dsn, c)

        async def wipe(conn):
            await conn.execute(
                f"delete from {SCHEMA}.auth_sessions where subject = $1", id_hash(SMOKE_ID)
            )
            await conn.execute(
                f"delete from {SCHEMA}.students where id_hash = $1", id_hash(SMOKE_ID)
            )
            await conn.execute(f"delete from {SCHEMA}.cohorts where name = $1", SMOKE_COHORT)

        e.sql(wipe)
        yield e


def test_first_run_creates_the_cohort_and_the_student(env):
    env.smoke()
    cohorts = env.cohorts()
    assert len(cohorts) == 1
    assert cohorts[0]["access_until"] in tomorrows()
    students = env.student()
    assert len(students) == 1
    assert students[0]["name"] == SMOKE_NAME
    assert students[0]["cohort_id"] == cohorts[0]["id"]
    assert students[0]["disabled"] is False


def test_the_cohort_stores_an_argon2_hash_of_the_password_never_the_password(env):
    from argon2 import PasswordHasher

    env.smoke()
    stored = env.cohorts()[0]["password_hash"]
    assert PasswordHasher().verify(stored, PW1)
    assert PW1 not in stored


def test_after_a_run_a_real_login_with_the_smoke_id_and_password_succeeds(env):
    env.smoke()
    resp = env.login()
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"kind": "student", "name": SMOKE_NAME}


def test_a_wrong_password_still_fails_login(env):
    env.smoke()
    assert env.login(password=PW2).status_code == 401


def test_rerunning_never_duplicates_the_cohort_or_the_student(env):
    env.smoke()
    env.smoke()
    env.smoke()
    assert len(env.cohorts()) == 1
    assert len(env.student()) == 1


def test_a_rerun_with_a_new_password_replaces_the_old_one(env):
    env.smoke(PW1)
    env.smoke(PW2)
    assert len(env.cohorts()) == 1
    assert env.login(password=PW2).status_code == 200
    assert env.login(env.other_client(), password=PW1).status_code == 401


def test_a_rerun_moves_access_until_to_tomorrow(env):
    env.smoke()

    async def age(conn):
        await conn.execute(
            f"update {SCHEMA}.cohorts set access_until = $2 where name = $1",
            SMOKE_COHORT,
            dt.date(2020, 1, 1),
        )

    env.sql(age)
    assert env.login(env.other_client()).status_code == 403  # expired
    env.smoke()
    assert env.cohorts()[0]["access_until"] in tomorrows()
    assert env.login(env.other_client()).status_code == 200


def test_a_run_signs_the_smoke_student_out_everywhere(env):
    env.smoke()
    assert env.login().status_code == 200
    assert env.client.get("/api/auth/me").status_code == 200
    assert env.auth_sessions(id_hash(SMOKE_ID)) >= 1
    env.smoke()
    assert env.auth_sessions(id_hash(SMOKE_ID)) == 0
    assert env.client.get("/api/auth/me").status_code == 401


def test_a_run_leaves_other_students_sessions_alone(env):
    from argon2 import PasswordHasher

    env.smoke()
    other_raw = str(800000000 + next(_n))
    other_pw = "other-class-" + "pw-1"
    pw = PasswordHasher().hash(other_pw)

    async def seed(conn):
        cid = await conn.fetchval(
            f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
            "values ($1, $2, $3) returning id",
            f"Fake cohort {other_raw}",
            pw,
            dt.date.today() + 30 * DAY,
        )
        await conn.execute(
            f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, $2, $3)",
            id_hash(other_raw),
            f"Fake Student {other_raw}",
            cid,
        )

    env.sql(seed)
    other = env.other_client()
    resp = other.post("/api/auth/login", json={"id": other_raw, "password": other_pw})
    assert resp.status_code == 200
    env.smoke()
    assert other.get("/api/auth/me").status_code == 200


def test_disable_makes_login_403_account_disabled_and_signs_out(env):
    env.smoke()
    assert env.login().status_code == 200
    env.disable()
    assert env.student()[0]["disabled"] is True
    assert env.auth_sessions(id_hash(SMOKE_ID)) == 0
    assert env.client.get("/api/auth/me").status_code == 401
    resp = env.login(env.other_client())
    assert resp.status_code == 403
    assert resp.json() == {"detail": "account_disabled"}


def test_disable_reads_no_stdin(env):
    env.smoke()
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.cli", "smoke-student", "--disable"],
        stdin=subprocess.PIPE,  # open and never written: reading it would block
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=MANAGER_DIR,
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("LF_")},
            "LF_DATABASE_URL": env.app_dsn,
            "LF_ID_SECRET": TEST_SECRET,
        },
    )
    try:
        out, err = proc.communicate(timeout=60)
    finally:
        proc.kill()
    assert proc.returncode == 0, out + err
    assert env.student()[0]["disabled"] is True


def test_disable_never_prints_the_dsn(env):
    env.smoke()
    res = env.disable()
    assert env.app_dsn not in res.stdout + res.stderr
    assert APP_PASSWORD not in res.stdout + res.stderr


def test_a_run_after_disable_enables_the_student_again(env):
    env.smoke()
    env.disable()
    env.smoke()
    assert env.student()[0]["disabled"] is False
    assert env.login(env.other_client()).status_code == 200


def test_disable_with_no_smoke_student_yet_is_harmless(env):
    res = run_cli("--disable", stdin=None, dsn=env.app_dsn)
    assert res.returncode == 0, res.stdout + res.stderr
    assert env.student() == []


def test_a_short_password_changes_nothing_in_the_database(env):
    res = run_cli(stdin="short7\n", dsn=env.app_dsn)
    assert res.returncode == 1
    assert env.cohorts() == []
    assert env.student() == []
