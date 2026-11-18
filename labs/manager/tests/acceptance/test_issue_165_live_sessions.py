"""Acceptance tests for issue #165: the live sessions API (slice 4 of #11, D59, D61, D63).

Spec section 9 (Live sessions), D61 (the reserved `lf-smoke` cohort is left out of every teacher
query), D63 (a student is addressed by an opaque random key). The web page is pinned in
labs/web/tests/acceptance/issue-165-live-sessions.test.tsx. The "teacher Playwright flows"
criterion of the issue moved to its own issue (D65) and is not examined here. Database tests need a
superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and skip locally when it is absent
(CI sets it and fails on any skip). Every id, name and password below is fake.

CONTRACT (the issue is silent on the details; the implementation follows this). Both routes need
an admin session: no session -> 401, a student session -> 403 (also for an unknown session id).

GET /api/admin/live
  200 {"sessions": [{
        "id": "<uuid>",                 # the session id itself (random already), no other handle
        "key": "<uuid>",                # students.key (D63): the page links /admin/students/<key>
        "name": str,                    # the student's name
        "cohort": str,                  # the cohort's name
        "lab_id": str,
        "lab_title": {"en": str, "he": str} | null,   # from the lab definition; null if unknown
        "started_at": "<ISO 8601>",
        "last_heartbeat": "<ISO 8601>" | null          # null when no heartbeat arrived yet
     }]}
  Live sessions only, i.e. status 'starting' or 'running'; ended and expired ones are not listed.
  Sessions of the `lf-smoke` cohort (D61: any case, any padding) are left out. Newest started
  first. No student id, no id hash, anywhere in the body. An empty system answers
  {"sessions": []}.

POST /api/admin/live/<session id>/end
  204 and the session ends the way the student's End lab (POST /api/sessions/<id>/end) ends it:
  the row has status 'ended', ended_at set and a non-null end_reason (which one is not pinned: the
  column is constrained to the D24 set, and a new reason such as 'teacher' needs a migration, so
  reusing 'student' is fine), the container removal is requested from the runtime
  (`lab-<session id>`), and the session is gone from GET /api/admin/live. Other sessions are
  untouched.
  404 for a session id that is unknown, malformed (also a student's id hash), already ended or
  expired (and then no runtime call), or that belongs to the `lf-smoke` cohort (then it stays
  live, untouched). Never 422.
"""

import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import os
import re
import stat
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
ADMIN_PASSWORD = "admin-" + "password-1"  # throwaway
NOW = dt.datetime.now(dt.UTC).replace(microsecond=0)
TODAY = NOW.date()
DAY = dt.timedelta(days=1)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_n = itertools.count(1)

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"

LABS = {"live-a": 1, "live-b": 2}


def write_content(root: Path) -> Path:
    for lab_id, lesson in LABS.items():
        d = root / "test" / lab_id
        d.mkdir(parents=True)
        (d / "lab.yaml").write_text(
            f"id: {lab_id}\ntitle:\n  en: Title {lab_id}\n  he: כותרת {lab_id}\n"
            f"description:\n  en: About {lab_id}.\n  he: על {lab_id}.\n"
            "image: term\ntime_limit_min: 15\ninternet: false\nallow_domains: []\nsudo: false\n"
            f"status: published\nmodule: 1\nlesson: {lesson}\ntasks:\n"
            "  - id: 01-one\n    kind: hands-on\n    checklist:\n      - en: Do it\n"
            "        he: לעשות\n",
            encoding="utf-8",
        )
        td = d / "tasks" / "01-one"
        td.mkdir(parents=True)
        for lang in ("en", "he"):
            (td / f"task.{lang}.md").write_text("# Task\n\nBODY\n", encoding="utf-8")
            (td / f"solution.{lang}.md").write_text("SOLUTION-x\n", encoding="utf-8")
        (td / "setup.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (td / "check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for name in ("setup.sh", "check.sh"):
            mode = (td / name).stat().st_mode
            (td / name).chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return root


class FakeRuntime:
    def __init__(self):
        self.removed: list[str] = []

    async def start(self, spec):
        return str(spec["name"])

    async def remove(self, name):
        self.removed.append(name)

    async def list_lab_containers(self):
        return []

    async def exec_script(self, name, script, timeout=None):
        from app.runtime import ExecResult

        return ExecResult(0, "", False)


# ---------------------------------------------------------------- database plumbing


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


def id_hash(raw: str) -> str:
    return hmac.new(TEST_SECRET.encode(), raw.lstrip("0").encode(), hashlib.sha256).hexdigest()


def fresh_ip() -> str:
    n = next(_n)
    return f"10.165.{n // 250}.{n % 250 + 1}"


def uniq(label: str) -> str:
    return f"Fake {label} {next(_n)}-{time.monotonic_ns()}"


def _client(app, ip: str):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="https://testserver", client=(ip, 50000))


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


class Student:
    def __init__(self, raw: str, hashed: str, name: str, cohort: int, cohort_name: str):
        self.raw, self.hash, self.name = raw, hashed, name
        self.cohort, self.cohort_name = cohort, cohort_name


class Env:
    def __init__(self, admin_dsn, client, runtime):
        self.admin_dsn = admin_dsn
        self.client = client  # lifespan client, no session
        self.app = client.app
        self.runtime = runtime

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def new_client(self):
        c = _client(self.app, fresh_ip())
        c.portal = self.client.portal  # the app's pool belongs to the lifespan client's loop
        return c

    def admin(self):
        from argon2 import PasswordHasher

        name = f"fake-admin-{next(_n)}"
        pw = PasswordHasher().hash(ADMIN_PASSWORD)

        async def seed(conn):
            await conn.execute(
                f"insert into {SCHEMA}.admins (username, password_hash) values ($1, $2)", name, pw
            )

        self.sql(seed)
        client = self.new_client()
        body = {"username": name, "password": ADMIN_PASSWORD}
        resp = client.post("/api/auth/admin/login", json=body)
        assert resp.status_code == 200, resp.text
        return client

    def cohort(self, name: str | None = None) -> tuple[int, str]:
        from argon2 import PasswordHasher

        pw = PasswordHasher().hash(COHORT_PASSWORD)
        cname = name or uniq("cohort")

        async def seed(conn):
            return await conn.fetchval(
                f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                "values ($1, $2, $3) returning id",
                cname,
                pw,
                TODAY + 30 * DAY,
            )

        return self.sql(seed), cname

    def student(self, cohort_name: str | None = None, name: str | None = None) -> Student:
        raw = str(1650000000 + next(_n))  # own id range
        cid, cname = self.cohort(cohort_name)
        s = Student(raw, id_hash(raw), name or uniq("student"), cid, cname)

        async def seed(conn):
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, $2, $3)",
                s.hash,
                s.name,
                cid,
            )

        self.sql(seed)
        return s

    def session(
        self,
        s: Student,
        lab: str = "live-a",
        status: str = "running",
        started: dt.datetime | None = None,
        heartbeat: dt.datetime | None = None,
    ) -> str:
        started = started or NOW - dt.timedelta(minutes=10)
        ended = None if status in ("starting", "running") else started

        async def seed(conn):
            return str(
                await conn.fetchval(
                    f"insert into {SCHEMA}.lab_sessions (student, lab_id, status, started_at, "
                    "last_heartbeat, ended_at) values ($1, $2, $3, $4, $5, $6) returning id",
                    s.hash,
                    lab,
                    status,
                    started,
                    heartbeat,
                    ended,
                )
            )

        return self.sql(seed)

    def row(self, session_id: str):
        async def get(conn):
            return await conn.fetchrow(
                f"select status, ended_at, end_reason from {SCHEMA}.lab_sessions "
                "where id = $1::uuid",
                session_id,
            )

        return self.sql(get)

    def key(self, s: Student) -> str:
        """The student's key as the class table gives it."""
        if not hasattr(self, "_keys_admin"):
            self._keys_admin = self.admin()
        resp = self._keys_admin.get("/api/admin/class", params={"cohort": s.cohort})
        rows = [r for r in resp.json()["rows"] if r["name"] == s.name]
        assert len(rows) == 1, f"{s.name!r} is not in the class table exactly once"
        return rows[0]["key"]

    def login(self, s: Student):
        client = self.new_client()
        body = {"id": s.raw, "password": COHORT_PASSWORD}
        return client, client.post("/api/auth/login", json=body)


@pytest.fixture
def env(_db, monkeypatch, tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 32768000 kB\nMemFree: 8192000 kB\nMemAvailable: 16384000 kB\nSwapTotal: 0 kB\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.setenv("LF_CONTENT_DIR", str(write_content(tmp_path / "content")))
    monkeypatch.setenv("LF_MEMINFO_PATH", str(meminfo))
    monkeypatch.setenv("LF_DOCKER_HOST", f"unix://{tmp_path / 'no-docker.sock'}")
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "0")
    monkeypatch.setenv("LF_JANITOR_INTERVAL_S", "3600")
    monkeypatch.setenv("LF_MAX_TERM", "1000")
    for var in (
        "LF_TRUSTED_PROXY",
        "LF_MAX_CODE",
        "LF_MEM_RESERVE_MB",
        "LF_HEARTBEAT_TIMEOUT_S",
        "LF_DISK_PATH",
        "LF_SETUP_TIMEOUT_S",
        "LF_CHECK_TIMEOUT_S",
        "LF_GOOGLE_CLIENT_ID",
        "LF_GOOGLE_CLIENT_SECRET",
        "LF_ADMIN_EMAILS",
        "LF_GOOGLE_REDIRECT_URI",
        "LF_ADMIN_PASSWORD_LOGIN",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.main import app

    with _client(app, fresh_ip()) as c:
        runtime = FakeRuntime()
        app.state.runtime = runtime
        yield Env(_db, c, runtime)


@pytest.fixture
def admin(env):
    return env.admin()


def _end_all_live(env: Env) -> None:
    """Each test starts from an empty live list: other tests' sessions are ended in SQL."""

    async def go(conn):
        await conn.execute(
            f"update {SCHEMA}.lab_sessions set status = 'ended', ended_at = now(), "
            "end_reason = 'orphan' where status in ('starting', 'running')"
        )

    env.sql(go)


@pytest.fixture
def clean(env):
    _end_all_live(env)
    return env


def live(client) -> list[dict]:
    resp = client.get("/api/admin/live")
    assert resp.status_code == 200, resp.text
    return resp.json()["sessions"]


# ---------------------------------------------------------------- access


def test_live_list_needs_an_admin_session_anonymous_is_401(env):
    assert env.new_client().get("/api/admin/live").status_code == 401


def test_live_list_refuses_a_student_with_403(env):
    s = env.student()
    client, resp = env.login(s)
    assert resp.status_code == 200, resp.text
    assert client.get("/api/admin/live").status_code == 403


def test_end_needs_an_admin_session_anonymous_is_401(env):
    s = env.student()
    sid = env.session(s)
    assert env.new_client().post(f"/api/admin/live/{sid}/end").status_code == 401
    assert env.row(sid)["status"] == "running"
    assert f"lab-{sid}" not in env.runtime.removed


def test_end_refuses_a_student_with_403_and_changes_nothing(env):
    s = env.student()
    sid = env.session(s)
    client, resp = env.login(s)
    assert resp.status_code == 200, resp.text
    assert client.post(f"/api/admin/live/{sid}/end").status_code == 403
    assert env.row(sid)["status"] == "running"
    assert f"lab-{sid}" not in env.runtime.removed


def test_end_answers_401_before_404_for_an_unknown_id(env):
    assert env.new_client().post(f"/api/admin/live/{uuid.uuid4()}/end").status_code == 401
    s = env.student()
    client, _ = env.login(s)
    assert client.post(f"/api/admin/live/{uuid.uuid4()}/end").status_code == 403


# ---------------------------------------------------------------- the list


def test_an_empty_system_lists_no_sessions(clean, admin):
    resp = admin.get("/api/admin/live")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": []}


def test_a_live_session_lists_name_cohort_lab_started_and_heartbeat(clean, admin):
    s = clean.student()
    started = NOW - dt.timedelta(minutes=12)
    beat = NOW - dt.timedelta(seconds=20)
    sid = clean.session(s, "live-a", "running", started, beat)
    rows = live(admin)
    assert len(rows) == 1
    r = rows[0]
    assert set(r) == {
        "id",
        "key",
        "name",
        "cohort",
        "lab_id",
        "lab_title",
        "started_at",
        "last_heartbeat",
    }
    assert r["id"] == sid
    assert r["name"] == s.name
    assert r["cohort"] == s.cohort_name
    assert r["lab_id"] == "live-a"
    assert r["lab_title"] == {"en": "Title live-a", "he": "כותרת live-a"}
    assert dt.datetime.fromisoformat(r["started_at"]) == started
    assert dt.datetime.fromisoformat(r["last_heartbeat"]) == beat


def test_a_starting_session_is_live_with_a_null_heartbeat(clean, admin):
    s = clean.student()
    sid = clean.session(s, "live-b", "starting")
    rows = live(admin)
    assert [r["id"] for r in rows] == [sid]
    assert rows[0]["last_heartbeat"] is None
    assert rows[0]["lab_id"] == "live-b"


def test_a_lab_missing_from_the_content_lists_with_a_null_title(clean, admin):
    s = clean.student()
    sid = clean.session(s, "gone-lab")
    rows = live(admin)
    assert [r["id"] for r in rows] == [sid]
    assert rows[0]["lab_id"] == "gone-lab"
    assert rows[0]["lab_title"] is None


def test_ended_and_expired_sessions_are_not_listed(clean, admin):
    a, b, c = clean.student(), clean.student(), clean.student()
    clean.session(a, status="ended")
    clean.session(b, status="expired")
    kept = clean.session(c, status="running")
    assert [r["id"] for r in live(admin)] == [kept]


def test_the_smoke_cohort_is_left_out_whatever_its_case_or_padding(clean, admin):
    visible = clean.student()
    shown = clean.session(visible)
    for name in ("lf-smoke", "LF-Smoke", "  lf-smoke "):
        clean.session(clean.student(name))
    # a look-alike is an ordinary cohort (D61 compares the whole trimmed name)
    lookalike = clean.student("lf-smoke-2")
    look = clean.session(lookalike)
    ids = {r["id"] for r in live(admin)}
    assert ids == {shown, look}


def test_newest_started_comes_first(clean, admin):
    old, mid, new = clean.student(), clean.student(), clean.student()
    s_mid = clean.session(mid, started=NOW - dt.timedelta(minutes=30))
    s_new = clean.session(new, started=NOW - dt.timedelta(minutes=5))
    s_old = clean.session(old, started=NOW - dt.timedelta(minutes=90))
    assert [r["id"] for r in live(admin)] == [s_new, s_mid, s_old]


def test_the_row_key_is_the_students_key_and_the_page_link_target(clean, admin):
    s = clean.student()
    clean.session(s)
    (row,) = live(admin)
    assert UUID_RE.fullmatch(row["key"])
    assert row["key"] == clean.key(s)
    page = admin.get(f"/api/admin/students/{row['key']}")
    assert page.status_code == 200
    assert page.json()["name"] == s.name


def test_the_list_never_carries_a_raw_id_or_an_id_hash(clean, admin):
    s = clean.student()
    clean.session(s)
    resp = admin.get("/api/admin/live")
    assert s.raw not in resp.text
    assert s.hash not in resp.text
    assert not re.search(r"[0-9a-f]{64}", resp.text)
    assert "student" not in resp.json()["sessions"][0]


def test_one_session_per_student_so_two_students_give_two_rows(clean, admin):
    a, b = clean.student(), clean.student()
    sa, sb = clean.session(a), clean.session(b, "live-b")
    rows = {r["id"]: r for r in live(admin)}
    assert set(rows) == {sa, sb}
    assert rows[sa]["name"] == a.name and rows[sb]["name"] == b.name


# ---------------------------------------------------------------- ending


def test_ending_a_session_answers_204_and_ends_it_with_a_reason(clean, admin):
    s = clean.student()
    sid = clean.session(s, heartbeat=NOW)
    resp = admin.post(f"/api/admin/live/{sid}/end")
    assert resp.status_code == 204, resp.text
    row = clean.row(sid)
    assert row["status"] == "ended"
    assert row["ended_at"] is not None
    assert row["end_reason"]  # the same column the student's End lab fills


def test_ending_requests_the_container_removal_from_the_runtime(clean, admin):
    s = clean.student()
    sid = clean.session(s)
    assert admin.post(f"/api/admin/live/{sid}/end").status_code == 204
    assert f"lab-{sid}" in clean.runtime.removed


def test_an_ended_session_disappears_from_the_live_list(clean, admin):
    a, b = clean.student(), clean.student()
    sa, sb = clean.session(a), clean.session(b)
    assert {r["id"] for r in live(admin)} == {sa, sb}
    assert admin.post(f"/api/admin/live/{sa}/end").status_code == 204
    assert [r["id"] for r in live(admin)] == [sb]


def test_ending_one_session_leaves_the_others_untouched(clean, admin):
    a, b = clean.student(), clean.student()
    sa, sb = clean.session(a), clean.session(b)
    assert admin.post(f"/api/admin/live/{sa}/end").status_code == 204
    assert clean.row(sb)["status"] == "running"
    assert clean.row(sb)["ended_at"] is None
    assert f"lab-{sb}" not in clean.runtime.removed


def test_a_starting_session_can_be_ended_too(clean, admin):
    s = clean.student()
    sid = clean.session(s, status="starting")
    assert admin.post(f"/api/admin/live/{sid}/end").status_code == 204
    assert clean.row(sid)["status"] == "ended"
    assert f"lab-{sid}" in clean.runtime.removed


def test_ending_the_same_session_twice_is_404_the_second_time(clean, admin):
    s = clean.student()
    sid = clean.session(s)
    assert admin.post(f"/api/admin/live/{sid}/end").status_code == 204
    clean.runtime.removed.clear()
    assert admin.post(f"/api/admin/live/{sid}/end").status_code == 404
    assert clean.runtime.removed == []


def test_an_already_ended_or_expired_session_is_404_with_no_runtime_call(clean, admin):
    a, b = clean.student(), clean.student()
    done = clean.session(a, status="ended")
    expired = clean.session(b, status="expired")
    for sid in (done, expired):
        assert admin.post(f"/api/admin/live/{sid}/end").status_code == 404
    assert clean.runtime.removed == []
    assert clean.row(done)["status"] == "ended"
    assert clean.row(expired)["status"] == "expired"


def test_an_unknown_session_id_is_404(clean, admin):
    assert admin.post(f"/api/admin/live/{uuid.uuid4()}/end").status_code == 404
    assert clean.runtime.removed == []


@pytest.mark.parametrize("bad", ["not-a-uuid", "123", "0", "%20", "x" * 80, "1;drop table x"])
def test_a_malformed_session_id_is_404_never_422(clean, admin, bad):
    assert admin.post(f"/api/admin/live/{bad}/end").status_code == 404


def test_a_students_id_hash_is_not_a_session_id(clean, admin):
    s = clean.student()
    sid = clean.session(s)
    assert admin.post(f"/api/admin/live/{s.hash}/end").status_code == 404
    assert admin.post(f"/api/admin/live/{s.raw}/end").status_code == 404
    assert clean.row(sid)["status"] == "running"


def test_a_smoke_cohort_session_is_404_and_stays_live(clean, admin):
    smoke = clean.student("lf-smoke")
    sid = clean.session(smoke)
    assert admin.post(f"/api/admin/live/{sid}/end").status_code == 404
    row = clean.row(sid)
    assert row["status"] == "running"
    assert row["ended_at"] is None
    assert f"lab-{sid}" not in clean.runtime.removed


def test_a_padded_smoke_cohort_session_is_404_too(clean, admin):
    smoke = clean.student(" LF-SMOKE ")
    sid = clean.session(smoke)
    assert admin.post(f"/api/admin/live/{sid}/end").status_code == 404
    assert clean.row(sid)["status"] == "running"


def test_ending_works_when_the_container_removal_fails(clean, admin):
    s = clean.student()
    sid = clean.session(s)

    async def boom(name):
        raise RuntimeError("docker is down")

    clean.runtime.remove = boom
    resp = admin.post(f"/api/admin/live/{sid}/end")
    assert resp.status_code == 204  # same as the student's End: the janitor sweeps the container
    assert clean.row(sid)["status"] == "ended"
