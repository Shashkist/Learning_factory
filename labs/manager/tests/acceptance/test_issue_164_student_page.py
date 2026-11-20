"""Acceptance tests for issue #164: the student page API (slice 3 of #11, D59, D63).

Spec section 9 (Student page), D55 (completion times, flags and reasons), D61 (the reserved
`lf-smoke` cohort), D63 (the page is addressed by an opaque random key). The web page is pinned in
labs/web/tests/acceptance/issue-164-student-page.test.tsx. Database tests need a superuser DSN to a
throwaway Postgres in LF_TEST_DATABASE_URL and skip locally when it is absent (CI sets it and
fails on any skip). Every id, name and password below is fake.

CONTRACT (the issue is silent on the details; the implementation follows this). Every route needs
an admin session: no session -> 401, a student session -> 403.

`<key>` is `students.key`, an opaque random uuid (D63). A key that is unknown, malformed (also an
id hash or a raw id), or belongs to a student of the `lf-smoke` cohort answers 404 on every route
below (never 422), for any method.

GET /api/admin/students/<key>
  200 {
    "key": "<uuid>", "name": str, "cohort": "<cohort name>", "note": str | null,
    "disabled": bool,
    "access_until": "YYYY-MM-DD" | null,         # the student's override date
    "cohort_access_until": "YYYY-MM-DD",
    "labs": [{                                    # every published lab, in class-table order
      "id": str, "title": {"en", "he"},
      "status": "done" | "<n>/<m>" | "none",      # same rule as the class table cell
      "tasks_passed": int, "tasks_total": int,    # n and m of that rule (tasks still in lab.yaml)
      "attempts": int,                            # sum of attempts over the student's task results
      "tasks": [{                                 # the tasks in lab.yaml, in lab.yaml order
        "id": str, "passed": bool, "attempts": int,        # attempts summed over sessions
        "seconds": number | null,                          # first completion: passed_at - shown_at
        "median_seconds": number | null                    # median of all first completions
      }],
      "flags": [{                                 # live and dismissed flags
        "id": str,                                # opaque, only used in the dismiss URL
        "scope": "task" | "lab", "task_id": str | null,
        "reason": str,                            # the stored fast_reason
        "dismissed_at": "<ISO 8601>" | null
      }]
    }]
  }
  A student's first completion of a task is their earliest passed result of it (several sessions
  count once). The median covers every student's first completion across all cohorts, except
  the `lf-smoke` cohort, whatever the number of students.
  No id hash and no raw id anywhere in the body.

PATCH /api/admin/students/<key>      any of {note, access_until, disabled}
  200 the same body as GET. note: string or null (blank or whitespace-only becomes null, otherwise
  stored trimmed); access_until: "YYYY-MM-DD" or null (null removes the override); disabled: bool.
  422 for a value of the wrong type or a malformed or impossible date. Fields not sent stay.

POST /api/admin/students/<key>/flags/<flag id>/dismiss
  200 the same body as GET; the flag's dismissed_at is set (the column flag_dismissed_at of the
  task_results row or the lab_runs row is stored). A flag id that is not one of this student's
  flags: 404. The class table no longer shows the flag on the cell once every flag of the cell is
  dismissed.

GET /api/admin/class rows and GET /api/admin/cohorts/<id>/students rows both carry `key` (D63).
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
NOW = dt.datetime.now(dt.UTC)
TODAY = NOW.date()
DAY = dt.timedelta(days=1)
BASE = NOW - 5 * DAY
KEY_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_n = itertools.count(1)

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"

T1, T2, T3 = "01-one", "02-two", "03-three"
LABS: dict[str, tuple[str, int, int, list[str]]] = {
    "stu-a": ("published", 1, 5, [T1, T2, T3]),
    "stu-b": ("published", 1, 6, [T1]),
    "stu-med": ("published", 2, 13, [T1]),  # only the median test touches this lab
    "stu-draft": ("draft", 1, 7, [T1]),
    "stu-internal": ("internal", 1, 8, [T1]),
}
PUBLISHED = ["stu-a", "stu-b", "stu-med"]


def write_content(root: Path) -> Path:
    for lab_id, (status, module, lesson, tasks) in LABS.items():
        d = root / "test" / lab_id
        d.mkdir(parents=True)
        body = "".join(
            f"  - id: {tid}\n    kind: hands-on\n    checklist:\n      - en: Do it\n"
            "        he: לעשות\n"
            for tid in tasks
        )
        (d / "lab.yaml").write_text(
            f"id: {lab_id}\ntitle:\n  en: Title {lab_id}\n  he: כותרת {lab_id}\n"
            f"description:\n  en: About {lab_id}.\n  he: על {lab_id}.\n"
            "image: term\ntime_limit_min: 15\ninternet: false\nallow_domains: []\nsudo: false\n"
            f"status: {status}\nmodule: {module}\nlesson: {lesson}\ntasks:\n" + body,
            encoding="utf-8",
        )
        for tid in tasks:
            td = d / "tasks" / tid
            td.mkdir(parents=True)
            for lang in ("en", "he"):
                (td / f"task.{lang}.md").write_text(f"# Task {tid}\n\nBODY\n", encoding="utf-8")
                (td / f"solution.{lang}.md").write_text("SOLUTION-x\n", encoding="utf-8")
            (td / "setup.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (td / "check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for name in ("setup.sh", "check.sh"):
                mode = (td / name).stat().st_mode
                (td / name).chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return root


class FakeRuntime:
    async def start(self, spec):
        return str(spec["name"])

    async def remove(self, name):
        return None

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
    return f"10.164.{n // 250}.{n % 250 + 1}"


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
    def __init__(self, raw: str, hashed: str, name: str, cohort: int):
        self.raw, self.hash, self.name, self.cohort = raw, hashed, name, cohort


class Env:
    def __init__(self, admin_dsn, client):
        self.admin_dsn = admin_dsn
        self.client = client  # lifespan client, no session
        self.app = client.app

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

    def cohort(self, name: str | None = None, until: dt.date | None = None) -> int:
        from argon2 import PasswordHasher

        pw = PasswordHasher().hash(COHORT_PASSWORD)

        async def seed(conn):
            return await conn.fetchval(
                f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                "values ($1, $2, $3) returning id",
                name or uniq("cohort"),
                pw,
                until or TODAY + 30 * DAY,
            )

        return self.sql(seed)

    def student(self, cohort_id: int | None = None, name: str | None = None) -> Student:
        raw = str(1640000000 + next(_n))  # own id range
        cid = cohort_id if cohort_id is not None else self.cohort()
        s = Student(raw, id_hash(raw), name or uniq("student"), cid)

        async def seed(conn):
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, $2, $3)",
                s.hash,
                s.name,
                cid,
            )

        self.sql(seed)
        return s

    def smoke_student(self) -> Student:
        return self.student(self.cohort("lf-smoke"))

    def key(self, s: Student) -> str:
        """The student's key as the class table gives it (the way the web page finds it)."""
        resp = self.admin_for_keys().get("/api/admin/class", params={"cohort": s.cohort})
        rows = [r for r in resp.json()["rows"] if r["name"] == s.name]
        assert len(rows) == 1, f"{s.name!r} is not in the class table exactly once"
        return rows[0]["key"]

    def admin_for_keys(self):
        if not hasattr(self, "_keys_admin"):
            self._keys_admin = self.admin()
        return self._keys_admin

    def session(self, s: Student, lab: str) -> str:
        async def seed(conn):
            return str(
                await conn.fetchval(
                    f"insert into {SCHEMA}.lab_sessions (student, lab_id, status, started_at, "
                    "ended_at) values ($1, $2, 'ended', $3, $3) returning id",
                    s.hash,
                    lab,
                    BASE,
                )
            )

        return self.sql(seed)

    def result(
        self,
        s: Student,
        lab: str,
        task: str,
        seconds: float | None = 30,
        attempts: int = 1,
        flag: bool = False,
        dismissed: bool = False,
        shown: dt.datetime = BASE,
    ) -> None:
        """One task_results row in its own session; seconds None means shown but never passed."""
        sid = self.session(s, lab)
        passed = None if seconds is None else shown + dt.timedelta(seconds=seconds)

        async def seed(conn):
            await conn.execute(
                f"insert into {SCHEMA}.task_results (student, lab_id, session_id, task_id, "
                "shown_at, passed_at, attempts, fast_flag, fast_reason, flag_dismissed_at) "
                "values ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9, $10)",
                s.hash,
                lab,
                sid,
                task,
                shown,
                passed,
                attempts,
                flag,
                f"task {task} passed 9 s after it appeared" if flag else None,
                NOW if dismissed else None,
            )

        self.sql(seed)

    def complete(self, s: Student, lab: str, flag: bool = False) -> None:
        sid = self.session(s, lab)

        async def seed(conn):
            await conn.execute(
                f"insert into {SCHEMA}.lab_completions (student, lab_id) values ($1, $2)",
                s.hash,
                lab,
            )
            await conn.execute(
                f"insert into {SCHEMA}.lab_runs (student, lab_id, session_id, first_completion, "
                "active_seconds, fast_flag, fast_reason) "
                "values ($1, $2, $3::uuid, true, 40, $4, $5)",
                s.hash,
                lab,
                sid,
                flag,
                "lab finished in 40 s of active time" if flag else None,
            )

        self.sql(seed)

    def login(self, s: Student):
        client = self.new_client()
        body = {"id": s.raw, "password": COHORT_PASSWORD}
        return client, client.post("/api/auth/login", json=body)


@pytest.fixture
def env(_db, monkeypatch, tmp_path, no_janitor):
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
        app.state.runtime = FakeRuntime()
        yield Env(_db, c)


@pytest.fixture
def admin(env):
    return env.admin()


# ---------------------------------------------------------------- reading the API


def page_url(key: str) -> str:
    return f"/api/admin/students/{key}"


def page(env, admin, s: Student) -> dict:
    resp = admin.get(page_url(env.key(s)))
    assert resp.status_code == 200, resp.text
    return resp.json()


def lab_of(body: dict, lab: str) -> dict:
    found = [x for x in body["labs"] if x["id"] == lab]
    assert len(found) == 1, f"lab {lab} is not on the page exactly once"
    return found[0]


def task_of(body: dict, lab: str, task: str) -> dict:
    return next(t for t in lab_of(body, lab)["tasks"] if t["id"] == task)


def class_cell(admin, s: Student, lab: str) -> dict:
    rows = admin.get("/api/admin/class", params={"cohort": s.cohort}).json()["rows"]
    return next(r for r in rows if r["name"] == s.name)["cells"][lab]


def patch(env, admin, s: Student, **body):
    return admin.patch(page_url(env.key(s)), json=body)


# ================================================================== access control: no database

SOME_KEY = "5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11"
ROUTES = [
    ("GET", f"/api/admin/students/{SOME_KEY}"),
    ("PATCH", f"/api/admin/students/{SOME_KEY}"),
    ("POST", f"/api/admin/students/{SOME_KEY}/flags/x/dismiss"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_the_student_routes_refuse_a_request_without_a_session_before_the_database(method, path):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, base_url="https://testserver")  # no lifespan: no pool needed
    resp = client.request(method, path, json={"note": "x"})
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


# ================================================================== access control


@pytest.mark.parametrize("method,path", ROUTES)
def test_anonymous_gets_401(env, method, path):
    assert env.client.request(method, path, json={"note": "x"}).status_code == 401


@pytest.mark.parametrize("method", ["GET", "PATCH", "POST"])
def test_a_student_session_is_refused_with_403_even_for_their_own_key(env, method):
    me = env.student()
    key = env.key(me)
    path = page_url(key) + ("/flags/x/dismiss" if method == "POST" else "")
    client, resp = env.login(me)
    assert resp.status_code == 200, resp.text
    assert client.request(method, path, json={"note": "x"}).status_code == 403


# ================================================================== the page: per lab


def test_the_page_names_the_student_and_lists_every_published_lab_in_class_table_order(env, admin):
    cname = uniq("cohort")
    s = env.student(env.cohort(cname))
    body = page(env, admin, s)
    assert body["name"] == s.name and body["cohort"] == cname
    assert KEY_RE.fullmatch(body["key"]) and body["key"] not in (s.hash, s.raw)
    assert body["note"] is None and body["disabled"] is False and body["access_until"] is None
    assert isinstance(body["cohort_access_until"], str)
    ids = [x["id"] for x in body["labs"] if x["id"].startswith("stu-")]
    assert ids == PUBLISHED  # not the draft, not the internal lab
    assert body["labs"][0]["title"] == {"en": "Title stu-a", "he": "כותרת stu-a"}


def test_tasks_passed_over_total_and_the_tasks_in_lab_yaml_order(env, admin):
    s = env.student()
    env.result(s, "stu-a", T2)
    env.result(s, "stu-a", T1)
    env.result(s, "stu-a", "90-removed")  # no longer in lab.yaml
    lab = lab_of(page(env, admin, s), "stu-a")
    assert (lab["status"], lab["tasks_passed"], lab["tasks_total"]) == ("2/3", 2, 3)
    assert [t["id"] for t in lab["tasks"]] == [T1, T2, T3]
    assert [t["passed"] for t in lab["tasks"]] == [True, True, False]


def test_an_untouched_lab_is_none_with_zero_passed_and_no_flags(env, admin):
    lab = lab_of(page(env, admin, env.student()), "stu-b")
    assert (lab["status"], lab["tasks_passed"], lab["tasks_total"]) == ("none", 0, 1)
    assert lab["attempts"] == 0 and lab["flags"] == []
    assert lab["tasks"][0]["seconds"] is None


def test_a_completed_lab_is_done(env, admin):
    s = env.student()
    env.result(s, "stu-b", T1)
    env.complete(s, "stu-b")
    assert lab_of(page(env, admin, s), "stu-b")["status"] == "done"


def test_attempts_are_summed_per_task_and_per_lab_over_sessions(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=None, attempts=4)  # tried, never passed
    env.result(s, "stu-a", T1, seconds=20, attempts=2)  # later session
    env.result(s, "stu-a", T2, seconds=None, attempts=3)
    body = page(env, admin, s)
    assert task_of(body, "stu-a", T1)["attempts"] == 6
    assert task_of(body, "stu-a", T2)["attempts"] == 3
    assert task_of(body, "stu-a", T2)["passed"] is False
    assert lab_of(body, "stu-a")["attempts"] == 9
    assert lab_of(body, "stu-a")["tasks_passed"] == 1


def test_another_students_work_does_not_leak_onto_the_page(env, admin):
    cid = env.cohort()
    me, other = env.student(cid), env.student(cid)
    env.result(other, "stu-a", T1, attempts=5)
    lab = lab_of(page(env, admin, me), "stu-a")
    assert lab["tasks_passed"] == 0 and lab["attempts"] == 0


# ================================================================== time per task and the median


def test_time_per_task_is_the_first_completion_and_sits_next_to_the_class_median(env, admin):
    me = env.student()
    env.result(me, "stu-med", T1, seconds=40)
    a, b, c = env.student(), env.student(), env.student()
    env.result(a, "stu-med", T1, seconds=10)
    env.result(a, "stu-med", T1, seconds=500)  # a later pass is not a first completion
    env.result(b, "stu-med", T1, seconds=20)
    env.result(c, "stu-med", T1, seconds=30)
    env.result(env.smoke_student(), "stu-med", T1, seconds=9999)  # lf-smoke is left out
    task = task_of(page(env, admin, me), "stu-med", T1)
    assert task["seconds"] == pytest.approx(40, abs=0.5)
    assert task["median_seconds"] == pytest.approx(25, abs=0.5)  # median of 10, 20, 30, 40


def test_a_student_who_passed_twice_shows_the_time_of_the_first_pass(env, admin):
    s = env.student()
    env.result(s, "stu-b", T1, seconds=15, shown=BASE)
    env.result(s, "stu-b", T1, seconds=600, shown=BASE + DAY)
    assert task_of(page(env, admin, s), "stu-b", T1)["seconds"] == pytest.approx(15, abs=0.5)


# ================================================================== flags with a reason


def test_flags_carry_their_reason_scope_and_task(env, admin):
    s = env.student()
    env.result(s, "stu-a", T2, seconds=9, flag=True)
    env.result(s, "stu-a", T1, seconds=50)
    env.complete(s, "stu-b", flag=True)
    body = page(env, admin, s)
    (task_flag,) = lab_of(body, "stu-a")["flags"]
    assert task_flag["scope"] == "task" and task_flag["task_id"] == T2
    assert task_flag["reason"] == f"task {T2} passed 9 s after it appeared"
    assert task_flag["dismissed_at"] is None and task_flag["id"]
    (lab_flag,) = lab_of(body, "stu-b")["flags"]
    assert lab_flag["scope"] == "lab" and lab_flag["task_id"] is None
    assert lab_flag["reason"] == "lab finished in 40 s of active time"


def test_a_dismissed_flag_is_still_listed_with_its_timestamp(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, flag=True, dismissed=True)
    (flag,) = lab_of(page(env, admin, s), "stu-a")["flags"]
    assert isinstance(flag["dismissed_at"], str) and flag["dismissed_at"]


# ================================================================== dismissing a flag


def _dismissed_at(env, s: Student, lab: str, task: str):
    async def read(conn):
        return await conn.fetchval(
            f"select flag_dismissed_at from {SCHEMA}.task_results "
            "where student = $1 and lab_id = $2 and task_id = $3",
            s.hash,
            lab,
            task,
        )

    return env.sql(read)


def test_dismissing_a_task_flag_stores_a_timestamp_and_clears_the_class_table_bolt(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=9, flag=True)
    env.result(s, "stu-a", T2, seconds=40)
    assert class_cell(admin, s, "stu-a")["flag"] is True
    key = env.key(s)
    (flag,) = lab_of(admin.get(page_url(key)).json(), "stu-a")["flags"]
    resp = admin.post(f"{page_url(key)}/flags/{flag['id']}/dismiss")
    assert resp.status_code == 200, resp.text
    (after,) = lab_of(resp.json(), "stu-a")["flags"]
    assert after["id"] == flag["id"] and after["dismissed_at"]
    stored = _dismissed_at(env, s, "stu-a", T1)
    assert stored is not None and abs(stored - dt.datetime.now(dt.UTC)) < dt.timedelta(minutes=5)
    assert _dismissed_at(env, s, "stu-a", T2) is None
    cell = class_cell(admin, s, "stu-a")
    assert cell == {"status": "2/3", "flag": False}


def test_dismissing_a_lab_flag_clears_the_bolt_and_keeps_the_lab_done(env, admin):
    s = env.student()
    env.complete(s, "stu-b", flag=True)
    key = env.key(s)
    (flag,) = lab_of(admin.get(page_url(key)).json(), "stu-b")["flags"]
    assert admin.post(f"{page_url(key)}/flags/{flag['id']}/dismiss").status_code == 200
    assert class_cell(admin, s, "stu-b") == {"status": "done", "flag": False}


def test_one_dismissal_leaves_the_students_other_flags_alone(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=9, flag=True)
    env.result(s, "stu-a", T2, seconds=8, flag=True)
    key = env.key(s)
    flags = {f["task_id"]: f for f in lab_of(admin.get(page_url(key)).json(), "stu-a")["flags"]}
    admin.post(f"{page_url(key)}/flags/{flags[T1]['id']}/dismiss")
    assert _dismissed_at(env, s, "stu-a", T2) is None
    assert class_cell(admin, s, "stu-a")["flag"] is True  # T2 still flags the cell


def test_a_flag_id_that_is_not_this_students_is_a_404(env, admin):
    cid = env.cohort()
    mine, theirs = env.student(cid), env.student(cid)
    env.result(theirs, "stu-a", T1, seconds=9, flag=True)
    their_flag = lab_of(page(env, admin, theirs), "stu-a")["flags"][0]["id"]
    my_key = env.key(mine)
    assert admin.post(f"{page_url(my_key)}/flags/{their_flag}/dismiss").status_code == 404
    assert admin.post(f"{page_url(my_key)}/flags/no-such-flag/dismiss").status_code == 404
    assert _dismissed_at(env, theirs, "stu-a", T1) is None


# ================================================================== the actions: patch


def test_editing_the_note_stores_it_and_the_cohort_list_shows_it(env, admin):
    s = env.student()
    resp = patch(env, admin, s, note="  sits at the front  ")
    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] == "sits at the front"
    assert page(env, admin, s)["note"] == "sits at the front"
    listed = admin.get(f"/api/admin/cohorts/{s.cohort}/students").json()
    assert [x["note"] for x in listed if x["name"] == s.name] == ["sits at the front"]
    assert patch(env, admin, s, note=None).json()["note"] is None
    patch(env, admin, s, note="again")
    assert patch(env, admin, s, note="   ").json()["note"] is None  # blank becomes null


def test_the_note_is_never_shown_to_the_student(env, admin):
    s = env.student()
    patch(env, admin, s, note="teacher-only remark xyzzy")
    client, resp = env.login(s)
    assert resp.status_code == 200
    assert "xyzzy" not in resp.text + client.get("/api/auth/me").text


def test_extending_access_lets_an_expired_cohort_student_in_and_clearing_removes_it(env, admin):
    s = env.student(env.cohort(until=TODAY - 10 * DAY))
    assert env.login(s)[1].status_code == 403  # access_expired
    until = (TODAY + 20 * DAY).isoformat()
    resp = patch(env, admin, s, access_until=until)
    assert resp.status_code == 200 and resp.json()["access_until"] == until
    assert resp.json()["cohort_access_until"] == (TODAY - 10 * DAY).isoformat()
    assert env.login(s)[1].status_code == 200
    assert patch(env, admin, s, access_until=None).json()["access_until"] is None
    assert env.login(s)[1].status_code == 403


def test_disabling_blocks_login_and_a_running_session_start_and_enabling_restores_it(env, admin):
    s = env.student()
    client, resp = env.login(s)
    assert resp.status_code == 200
    assert patch(env, admin, s, disabled=True).json()["disabled"] is True
    assert page(env, admin, s)["disabled"] is True
    refused = env.login(s)[1]
    assert refused.status_code == 403 and refused.json() == {"detail": "account_disabled"}
    assert client.post("/api/sessions", json={"lab_id": "stu-b"}).status_code == 401  # cannot start
    assert patch(env, admin, s, disabled=False).json()["disabled"] is False
    assert env.login(s)[1].status_code == 200


def test_a_patch_changes_only_the_fields_it_sends(env, admin):
    s = env.student()
    until = (TODAY + 9 * DAY).isoformat()
    patch(env, admin, s, note="keep me", access_until=until)
    body = patch(env, admin, s, disabled=True).json()
    assert body["note"] == "keep me" and body["access_until"] == until and body["disabled"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"access_until": "2027-13-40"},
        {"access_until": "tomorrow"},
        {"access_until": 20270101},
        {"disabled": "yes"},
        {"note": 5},
    ],
)
def test_a_wrong_type_or_a_bad_date_is_a_422_and_changes_nothing(env, admin, bad):
    s = env.student()
    patch(env, admin, s, note="stay")
    assert patch(env, admin, s, **bad).status_code == 422
    body = page(env, admin, s)
    assert body["note"] == "stay" and body["disabled"] is False and body["access_until"] is None


# ================================================================== 404 rules (D63)


def test_an_unknown_key_is_a_404_on_every_route(env, admin):
    key = str(uuid.uuid4())
    assert admin.get(page_url(key)).status_code == 404
    assert admin.patch(page_url(key), json={"note": "x"}).status_code == 404
    assert admin.post(f"{page_url(key)}/flags/x/dismiss").status_code == 404


@pytest.mark.parametrize("bad", ["abc", "12345", "not-a-uuid-at-all", "0" * 64, "%00"])
def test_a_malformed_key_is_a_404_not_a_422(env, admin, bad):
    assert admin.get(page_url(bad)).status_code == 404
    assert admin.patch(page_url(bad), json={"note": "x"}).status_code == 404


def test_the_id_hash_and_the_raw_id_are_not_keys(env, admin):
    s = env.student()
    assert admin.get(page_url(s.hash)).status_code == 404
    assert admin.get(page_url(s.raw)).status_code == 404


def test_a_smoke_cohort_student_is_a_404_on_every_route(env, admin):
    smoke = env.smoke_student()
    env.result(smoke, "stu-a", T1, seconds=9, flag=True)

    async def read(conn):
        return str(
            await conn.fetchval(f"select key from {SCHEMA}.students where id_hash = $1", smoke.hash)
        )

    key = env.sql(read)
    assert KEY_RE.fullmatch(key)
    assert admin.get(page_url(key)).status_code == 404
    assert admin.patch(page_url(key), json={"disabled": True}).status_code == 404
    assert admin.post(f"{page_url(key)}/flags/x/dismiss").status_code == 404

    async def still_enabled(conn):
        return await conn.fetchval(
            f"select disabled from {SCHEMA}.students where id_hash = $1", smoke.hash
        )

    assert env.sql(still_enabled) is False


# ================================================================== keys and privacy


def test_two_students_with_one_name_get_distinct_keys_in_the_table_and_the_cohort_list(env, admin):
    cid = env.cohort()
    twin = uniq("twin")
    one, two = env.student(cid, twin), env.student(cid, twin)

    async def keys(conn):
        rows = await conn.fetch(
            f"select id_hash, key::text as k from {SCHEMA}.students where cohort_id = $1", cid
        )
        return {r["id_hash"]: r["k"] for r in rows}

    stored = env.sql(keys)
    assert stored[one.hash] != stored[two.hash]
    table = admin.get("/api/admin/class", params={"cohort": cid}).json()
    table_keys = {r["key"] for r in table["rows"]}
    list_rows = admin.get(f"/api/admin/cohorts/{cid}/students").json()
    assert table_keys == {stored[one.hash], stored[two.hash]}
    assert {x["key"] for x in list_rows} == table_keys
    patch_resp = admin.patch(page_url(stored[two.hash]), json={"note": "the second twin"})
    assert patch_resp.status_code == 200
    assert admin.get(page_url(stored[one.hash])).json()["note"] is None
    assert admin.get(page_url(stored[two.hash])).json()["note"] == "the second twin"


def test_no_response_carries_an_id_hash_or_a_raw_id(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=9, flag=True)
    env.complete(s, "stu-b", flag=True)
    key = env.key(s)
    flag = lab_of(admin.get(page_url(key)).json(), "stu-a")["flags"][0]["id"]
    texts = [
        admin.get(page_url(key)).text,
        admin.patch(page_url(key), json={"note": "n"}).text,
        admin.post(f"{page_url(key)}/flags/{flag}/dismiss").text,
        admin.get(f"/api/admin/cohorts/{s.cohort}/students").text,
    ]
    for text in texts:
        assert s.hash not in text and s.raw not in text
        assert not re.search(r"[0-9a-f]{64}", text)
