"""Acceptance tests for issue #163: the class table API (slice 2 of #11, D59).

Spec sections 8 and 9 (Class table), D55 (flags and reasons live in task_results / lab_runs),
D61 (the reserved `lf-smoke` cohort, app/smoke.py). The web page is pinned in
labs/web/tests/acceptance/issue-163-class-table.test.tsx. Database tests need a superuser DSN to a
throwaway Postgres in LF_TEST_DATABASE_URL and skip locally when it is absent (CI sets it and
fails on any skip). Every id, name and password below is fake. Every case creates its own cohorts
(unique names) and reads them through `?cohort=<id>`, so other rows may exist.

CONTRACT (the issue is silent on the details; the implementation follows this):

GET /api/admin/class[?cohort=<id>][&flagged=1]   (admin only: no session 401, student 403)
  {
    "labs": [{"id": "cls-a", "title": {"en": "...", "he": "..."}}, ...],
    "rows": [
      {"name": "Fake Name", "cohort": "<cohort name>", "last_activity": "<ISO 8601>" | null,
       "cells": {"cls-a": {"status": "done" | "<n>/<m>" | "none", "flag": true | false}, ...}},
      ...
    ]
  }
- `labs`: the published labs only (not draft, not internal), ordered by module, then lesson.
  `cells` has one entry for every lab in `labs`, for every row.
- A row never carries an id, an id hash or any other key that identifies the student by number.
  Rows are ordered by cohort name, then student name.
- status: "done" when the student has a lab_completions row for the lab; else "<n>/<m>" with
  n = distinct task ids passed (task_results.passed_at set) that are still in lab.yaml (never
  more than m) and m = the number of tasks in lab.yaml, when n >= 1; else "none" (a student who
  opened the lab but passed nothing is "none").
- flag: true when a task_results row or a lab_runs row of that student and lab has
  fast_flag = true and flag_dismissed_at IS NULL. The column `flag_dismissed_at timestamptz`
  (nullable) is added to both tables by a new migration in this issue (D59 slice 3 reuses it).
  A flag never changes `status`.
- last_activity: the latest of lab_sessions.started_at, last_heartbeat and ended_at over the
  student's sessions; null when the student has no session.
- cohort=<id>: only that cohort's students (labs stay the same). Not an integer: 422. Unknown id
  or the smoke cohort's id: 404.
- flagged=1: only rows with at least one flagged cell (all their cells are kept). flagged=0 or
  absent: all rows.
- Students of the `lf-smoke` cohort (app.smoke.NOT_SMOKE_SQL, any case or padding) are in no row,
  in the filtered or flagged lists, or in the CSV.

GET /api/admin/class.csv[?cohort=<id>][&flagged=1]   (admin only, same filters, same rows)
- 200, Content-Type text/csv, Content-Disposition: attachment with a .csv file name, UTF-8
  (a BOM is tolerated). Standard CSV quoting.
- Header: name, cohort, last_activity, then the published lab ids in the order of `labs`.
- One line per row: name, cohort, last_activity (the same ISO string as the JSON, empty when
  null), then per lab the status text, with " ⚡" appended when the cell is flagged
  (e.g. "2/3 ⚡", "done ⚡", "none").
- No id hash and no raw id anywhere in the text.
"""

import asyncio
import csv
import datetime as dt
import hashlib
import hmac
import io
import itertools
import os
import re
import stat
import time
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
HOUR = dt.timedelta(hours=1)
FLAG = " ⚡"

_n = itertools.count(1)

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"

T1, T2, T3 = "01-one", "02-two", "03-three"
# lab id -> (status, module, lesson, task ids); lessons inside the module ranges of content.py
LABS: dict[str, tuple[str, int, int, list[str]]] = {
    "cls-a": ("published", 1, 6, [T1, T2, T3]),
    "cls-b": ("published", 1, 5, [T1]),
    "cls-c": ("published", 2, 13, [T1, T2]),
    "cls-draft": ("draft", 1, 7, [T1]),
    "cls-internal": ("internal", 1, 8, [T1]),
}
PUBLISHED = ["cls-b", "cls-a", "cls-c"]  # module, then lesson


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
    """Starts and removes nothing for real; the class table never needs a container."""

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
    return f"10.163.{n // 250}.{n % 250 + 1}"


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

    # ---- seeding, straight into the tables (the class table only reads them)

    def cohort(self, name: str | None = None) -> int:
        from argon2 import PasswordHasher

        pw = PasswordHasher().hash(COHORT_PASSWORD)

        async def seed(conn):
            return await conn.fetchval(
                f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                "values ($1, $2, $3) returning id",
                name or uniq("cohort"),
                pw,
                TODAY + 30 * DAY,
            )

        return self.sql(seed)

    def student(self, cohort_id: int, name: str | None = None) -> "Student":
        raw = str(1630000000 + next(_n))  # own id range
        s = Student(raw, id_hash(raw), name or uniq("student"))

        async def seed(conn):
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, $2, $3)",
                s.hash,
                s.name,
                cohort_id,
            )

        self.sql(seed)
        return s

    def student_client(self):
        s = self.student(self.cohort())
        client = self.new_client()
        resp = client.post("/api/auth/login", json={"id": s.raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return client

    def session(
        self,
        s: "Student",
        lab: str,
        heartbeat: dt.datetime | None = None,
        started: dt.datetime | None = None,
        ended: dt.datetime | None = None,
    ) -> str:
        async def seed(conn):
            return str(
                await conn.fetchval(
                    f"insert into {SCHEMA}.lab_sessions (student, lab_id, status, started_at, "
                    "ended_at, last_heartbeat) values ($1, $2, 'ended', $3, $4, $5) returning id",
                    s.hash,
                    lab,
                    started or NOW - 5 * DAY,
                    ended or started or NOW - 5 * DAY,
                    heartbeat,
                )
            )

        return self.sql(seed)

    def passed(
        self,
        s: "Student",
        lab: str,
        tasks: list[str],
        flag: bool = False,
        dismissed: bool = False,
        sid: str | None = None,
    ) -> str:
        """Passed task results in one session; `flag` sets fast_flag on every one of them."""
        sid = sid or self.session(s, lab)

        async def seed(conn):
            for tid in tasks:
                await conn.execute(
                    f"insert into {SCHEMA}.task_results (student, lab_id, session_id, task_id, "
                    "shown_at, passed_at, attempts, fast_flag, fast_reason, flag_dismissed_at) "
                    "values ($1, $2, $3::uuid, $4, now(), now(), 1, $5, $6, $7)",
                    s.hash,
                    lab,
                    sid,
                    tid,
                    flag,
                    f"task {tid} passed 3 s after it appeared" if flag else None,
                    NOW if dismissed else None,
                )

        self.sql(seed)
        return sid

    def attempted(self, s: "Student", lab: str, tasks: list[str]) -> None:
        """Tasks shown and tried but never passed."""
        sid = self.session(s, lab)

        async def seed(conn):
            for tid in tasks:
                await conn.execute(
                    f"insert into {SCHEMA}.task_results (student, lab_id, session_id, task_id, "
                    "shown_at, passed_at, attempts) values ($1, $2, $3::uuid, $4, now(), null, 4)",
                    s.hash,
                    lab,
                    sid,
                    tid,
                )

        self.sql(seed)

    def complete(self, s: "Student", lab: str, flag: bool = False, dismissed: bool = False):
        """A finished lab: lab_completions row and a first-completion lab_runs row."""
        sid = self.session(s, lab)

        async def seed(conn):
            await conn.execute(
                f"insert into {SCHEMA}.lab_completions (student, lab_id) values ($1, $2)",
                s.hash,
                lab,
            )
            await conn.execute(
                f"insert into {SCHEMA}.lab_runs (student, lab_id, session_id, first_completion, "
                "active_seconds, fast_flag, fast_reason, flag_dismissed_at) "
                "values ($1, $2, $3::uuid, true, 40, $4, $5, $6)",
                s.hash,
                lab,
                sid,
                flag,
                "lab finished in 40 s of active time" if flag else None,
                NOW if dismissed else None,
            )

        self.sql(seed)


class Student:
    def __init__(self, raw: str, hashed: str, name: str):
        self.raw, self.hash, self.name = raw, hashed, name


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
        app.state.runtime = FakeRuntime()
        yield Env(_db, c)


@pytest.fixture
def admin(env):
    return env.admin()


# ---------------------------------------------------------------- reading the API


def table(client, cohort: int | None = None, flagged: bool | None = None) -> dict:
    params: dict[str, object] = {}
    if cohort is not None:
        params["cohort"] = cohort
    if flagged is not None:
        params["flagged"] = 1 if flagged else 0
    resp = client.get("/api/admin/class", params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {"labs", "rows"}, body
    return body


def rows_of(client, cohort: int, flagged: bool | None = None) -> list[dict]:
    return table(client, cohort, flagged)["rows"]


def row_for(client, cohort: int, s: Student) -> dict:
    found = [r for r in rows_of(client, cohort) if r["name"] == s.name]
    assert len(found) == 1, f"{s.name!r} is not listed exactly once in cohort {cohort}"
    return found[0]


def cell(client, cohort: int, s: Student, lab: str) -> dict:
    return row_for(client, cohort, s)["cells"][lab]


def csv_get(client, cohort: int | None = None, flagged: bool | None = None):
    params: dict[str, object] = {}
    if cohort is not None:
        params["cohort"] = cohort
    if flagged is not None:
        params["flagged"] = 1 if flagged else 0
    resp = client.get("/api/admin/class.csv", params=params)
    assert resp.status_code == 200, resp.text
    return resp


def csv_rows(resp) -> list[list[str]]:
    text = resp.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))


# ================================================================== access control: no database

ROUTES = ["/api/admin/class", "/api/admin/class.csv"]


@pytest.mark.parametrize("path", ROUTES)
def test_the_class_routes_exist_and_refuse_a_request_without_a_session(path):
    """Runs without Postgres: no cookie means 401 before any database access."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, base_url="https://testserver")  # no lifespan: no pool needed
    resp = client.get(path, params={"cohort": 1, "flagged": 1})
    assert resp.status_code == 401, f"GET {path} -> {resp.status_code}"


# ================================================================== access control


@pytest.mark.parametrize("path", ROUTES)
def test_anonymous_gets_401(env, path):
    assert env.client.get(path).status_code == 401


@pytest.mark.parametrize("path", ROUTES)
def test_a_student_session_gets_403(env, path):
    assert env.student_client().get(path).status_code == 403


# ================================================================== labs across the top


def test_labs_are_the_published_ones_in_module_and_lesson_order(env, admin):
    labs = table(admin, env.cohort())["labs"]
    ids = [x["id"] for x in labs]
    assert [i for i in ids if i.startswith("cls-")] == PUBLISHED
    assert "cls-draft" not in ids and "cls-internal" not in ids


def test_every_lab_has_a_title_in_both_languages(env, admin):
    labs = {x["id"]: x for x in table(admin, env.cohort())["labs"]}
    assert labs["cls-a"]["title"] == {"en": "Title cls-a", "he": "כותרת cls-a"}


# ================================================================== students down the side


def test_a_row_has_name_cohort_last_activity_and_one_cell_per_published_lab(env, admin):
    cname = uniq("cohort")
    cid = env.cohort(cname)
    s = env.student(cid)
    row = row_for(admin, cid, s)
    assert row["name"] == s.name
    assert row["cohort"] == cname
    assert "last_activity" in row
    assert set(row["cells"]) == {x["id"] for x in table(admin, cid)["labs"]}
    assert set(PUBLISHED) <= set(row["cells"])


def test_every_cell_has_a_status_and_a_boolean_flag(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    for c in row_for(admin, cid, s)["cells"].values():
        assert set(c) >= {"status", "flag"}
        assert isinstance(c["status"], str)
        assert c["flag"] is False


def test_the_rows_carry_no_id_hash_and_no_raw_id(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.complete(s, "cls-b")
    resp = admin.get("/api/admin/class", params={"cohort": cid})
    assert s.hash not in resp.text
    assert s.raw not in resp.text
    assert not re.search(r"[0-9a-f]{64}", resp.text)
    assert set(row_for(admin, cid, s)) == {"name", "cohort", "last_activity", "cells"}


def test_rows_are_ordered_by_name_within_a_cohort(env, admin):
    cid = env.cohort()
    for nm in ("Fake Zed", "Fake Abe", "Fake Moe"):
        env.student(cid, f"{nm} {next(_n)}")
    names = [r["name"] for r in rows_of(admin, cid)]
    assert names == sorted(names)
    assert len(names) == 3


# ================================================================== cells: done / n/m / none


def test_a_student_with_no_activity_has_none_in_every_cell(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    row = row_for(admin, cid, s)
    assert {row["cells"][lab]["status"] for lab in PUBLISHED} == {"none"}


def test_a_completed_lab_is_done(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-b", [T1])
    env.complete(s, "cls-b")
    assert cell(admin, cid, s, "cls-b")["status"] == "done"
    assert cell(admin, cid, s, "cls-a")["status"] == "none"


def test_a_lab_in_progress_shows_passed_over_total(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1, T2])
    assert cell(admin, cid, s, "cls-a")["status"] == "2/3"


def test_total_is_the_number_of_tasks_in_the_lab(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-c", [T2])
    assert cell(admin, cid, s, "cls-c")["status"] == "1/2"


def test_a_task_passed_in_two_sessions_counts_once(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1])
    env.passed(s, "cls-a", [T1])
    assert cell(admin, cid, s, "cls-a")["status"] == "1/3"


def test_attempted_but_unpassed_tasks_are_none(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.attempted(s, "cls-a", [T1, T2])
    assert cell(admin, cid, s, "cls-a")["status"] == "none"


def test_a_task_removed_from_lab_yaml_does_not_count_towards_n(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1, "90-removed-a", "91-removed-b", "92-removed-c"])
    assert cell(admin, cid, s, "cls-a")["status"] == "1/3"


def test_cells_of_other_students_do_not_leak(env, admin):
    cid = env.cohort()
    one, two = env.student(cid), env.student(cid)
    env.passed(one, "cls-a", [T1])
    assert cell(admin, cid, one, "cls-a")["status"] == "1/3"
    assert cell(admin, cid, two, "cls-a")["status"] == "none"


# ================================================================== the fast flag


def test_a_fast_task_flag_marks_the_cell(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1, T2], flag=True)
    c = cell(admin, cid, s, "cls-a")
    assert c["flag"] is True
    assert c["status"] == "2/3"


def test_a_fast_lab_flag_marks_the_cell_and_keeps_it_done(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.complete(s, "cls-b", flag=True)
    c = cell(admin, cid, s, "cls-b")
    assert c == {"status": "done", "flag": True}


def test_the_flag_is_only_on_the_lab_it_belongs_to(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1], flag=True)
    env.passed(s, "cls-c", [T1])
    cells = row_for(admin, cid, s)["cells"]
    assert cells["cls-a"]["flag"] is True
    assert cells["cls-c"]["flag"] is False
    assert cells["cls-b"]["flag"] is False


def test_a_task_flag_that_is_not_set_leaves_the_cell_unflagged(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1, T2, T3])
    env.complete(s, "cls-a")
    assert cell(admin, cid, s, "cls-a") == {"status": "done", "flag": False}


def test_a_dismissed_task_flag_does_not_mark_the_cell(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1], flag=True, dismissed=True)
    assert cell(admin, cid, s, "cls-a") == {"status": "1/3", "flag": False}


def test_a_dismissed_lab_flag_does_not_mark_the_cell(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.complete(s, "cls-b", flag=True, dismissed=True)
    assert cell(admin, cid, s, "cls-b") == {"status": "done", "flag": False}


def test_a_live_flag_still_shows_next_to_a_dismissed_one(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1], flag=True, dismissed=True)
    env.passed(s, "cls-a", [T2], flag=True)
    assert cell(admin, cid, s, "cls-a")["flag"] is True


def test_a_dismissed_task_flag_does_not_hide_a_live_lab_flag(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-b", [T1], flag=True, dismissed=True)
    env.complete(s, "cls-b", flag=True)
    assert cell(admin, cid, s, "cls-b")["flag"] is True


# ================================================================== last activity


def test_last_activity_is_null_without_a_session(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    assert row_for(admin, cid, s)["last_activity"] is None


def _parse(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def test_last_activity_is_the_latest_time_over_all_sessions(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.session(s, "cls-a", started=NOW - 9 * DAY, ended=NOW - 9 * DAY + HOUR)
    env.session(s, "cls-b", started=NOW - 3 * DAY, heartbeat=NOW - 2 * HOUR)
    env.session(s, "cls-c", started=NOW - 6 * DAY)
    got = row_for(admin, cid, s)["last_activity"]
    assert isinstance(got, str)
    assert abs(_parse(got) - (NOW - 2 * HOUR)) < dt.timedelta(minutes=2), got


# ================================================================== cohort filter and flagged only


def test_the_cohort_filter_keeps_only_that_cohorts_students(env, admin):
    c1, c2 = env.cohort(), env.cohort()
    a, b = env.student(c1), env.student(c2)
    assert [r["name"] for r in rows_of(admin, c1)] == [a.name]
    assert [r["name"] for r in rows_of(admin, c2)] == [b.name]


def test_without_a_filter_every_cohort_is_listed(env, admin):
    c1, c2 = env.cohort(), env.cohort()
    a, b = env.student(c1), env.student(c2)
    names = [r["name"] for r in table(admin)["rows"]]
    assert a.name in names and b.name in names


def test_an_unknown_cohort_is_a_404(env, admin):
    resp = admin.get("/api/admin/class", params={"cohort": 2_000_000_000})
    assert resp.status_code == 404, resp.text
    assert admin.get("/api/admin/class.csv", params={"cohort": 2_000_000_000}).status_code == 404


def test_a_cohort_id_that_is_not_a_number_is_a_422(env, admin):
    assert admin.get("/api/admin/class", params={"cohort": "abc"}).status_code == 422
    assert admin.get("/api/admin/class.csv", params={"cohort": "abc"}).status_code == 422


def test_flagged_only_keeps_the_students_with_a_flagged_cell(env, admin):
    cid = env.cohort()
    fast, calm, dismissed = env.student(cid), env.student(cid), env.student(cid)
    env.passed(fast, "cls-a", [T1], flag=True)
    env.passed(calm, "cls-a", [T1])
    env.passed(dismissed, "cls-a", [T1], flag=True, dismissed=True)
    assert [r["name"] for r in rows_of(admin, cid, flagged=True)] == [fast.name]
    assert len(rows_of(admin, cid, flagged=False)) == 3
    assert len(rows_of(admin, cid)) == 3


def test_flagged_only_keeps_every_cell_of_the_flagged_students(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1], flag=True)
    env.passed(s, "cls-c", [T1, T2])
    body = table(admin, cid, flagged=True)
    assert [x["id"] for x in body["labs"] if x["id"] in PUBLISHED] == PUBLISHED
    cells = body["rows"][0]["cells"]
    assert cells["cls-a"] == {"status": "1/3", "flag": True}
    assert cells["cls-c"]["status"] == "2/2"
    assert cells["cls-c"]["flag"] is False


def test_flagged_with_a_lab_flag_and_the_cohort_filter_combine(env, admin):
    c1, c2 = env.cohort(), env.cohort()
    a, b = env.student(c1), env.student(c2)
    env.complete(a, "cls-b", flag=True)
    env.complete(b, "cls-b", flag=True)
    assert [r["name"] for r in rows_of(admin, c1, flagged=True)] == [a.name]
    assert [r["name"] for r in rows_of(admin, c2, flagged=True)] == [b.name]


# ================================================================== lf-smoke is left out


def smoke_student(env, cohort_name: str, flag: bool = True) -> Student:
    cid = env.cohort(cohort_name)
    s = env.student(cid)
    env.passed(s, "cls-a", [T1], flag=flag)
    env.complete(s, "cls-b", flag=flag)
    return s


@pytest.mark.parametrize("cohort_name", ["lf-smoke", "LF-Smoke", "  lf-smoke "])
def test_the_smoke_cohort_and_its_look_alikes_are_in_no_row(env, admin, cohort_name):
    s = smoke_student(env, cohort_name)
    real = env.student(env.cohort())
    body = table(admin)
    names = [r["name"] for r in body["rows"]]
    assert s.name not in names
    assert real.name in names
    assert not [r for r in body["rows"] if r["cohort"].strip().lower() == "lf-smoke"]
    assert s.name not in [r["name"] for r in table(admin, flagged=True)["rows"]]


def test_the_smoke_cohort_id_is_unknown_to_the_class_table(env, admin):
    name = "lf-smoke"
    cid = env.cohort(name)
    smoke_student(env, name)
    assert admin.get("/api/admin/class", params={"cohort": cid}).status_code == 404
    assert admin.get("/api/admin/class.csv", params={"cohort": cid}).status_code == 404


def test_the_smoke_student_is_not_in_the_csv(env, admin):
    s = smoke_student(env, "lf-smoke")
    text = csv_get(admin).content.decode("utf-8-sig")
    assert s.name not in text
    assert s.name not in csv_get(admin, flagged=True).content.decode("utf-8-sig")


def test_the_smoke_runs_do_not_change_a_real_students_row(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1])
    smoke_student(env, "lf-smoke")
    assert cell(admin, cid, s, "cls-a") == {"status": "1/3", "flag": False}


# ================================================================== CSV export


def test_the_csv_is_a_utf8_attachment(env, admin):
    resp = csv_get(admin, env.cohort())
    assert resp.headers["content-type"].split(";")[0].strip() == "text/csv"
    disposition = resp.headers["content-disposition"].lower()
    assert "attachment" in disposition and ".csv" in disposition
    resp.content.decode("utf-8")


def test_the_csv_header_is_name_cohort_last_activity_and_the_lab_ids(env, admin):
    cid = env.cohort()
    env.student(cid)
    header = csv_rows(csv_get(admin, cid))[0]
    assert header[:3] == ["name", "cohort", "last_activity"]
    assert [h for h in header[3:] if h.startswith("cls-")] == PUBLISHED
    assert header[3:] == [x["id"] for x in table(admin, cid)["labs"]]


def test_the_csv_has_the_same_rows_and_cells_as_the_json(env, admin):
    cid = env.cohort()
    a, b, c = env.student(cid), env.student(cid), env.student(cid)
    env.passed(a, "cls-a", [T1, T2], flag=True)
    env.complete(b, "cls-b")
    env.complete(c, "cls-c", flag=True)
    body = table(admin, cid)
    lines = csv_rows(csv_get(admin, cid))
    header, data = lines[0], lines[1:]
    assert len(data) == len(body["rows"]) == 3
    for line, row in zip(data, body["rows"], strict=True):
        assert len(line) == len(header)
        assert line[0] == row["name"]
        assert line[1] == row["cohort"]
        assert line[2] == (row["last_activity"] or "")
        for lab, text in zip(header[3:], line[3:], strict=True):
            c = row["cells"][lab]
            assert text == c["status"] + (FLAG if c["flag"] else "")


def test_the_csv_shows_a_flag_as_a_bolt_after_the_status(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1, T2], flag=True)
    env.complete(s, "cls-b", flag=True)
    env.passed(s, "cls-c", [T1])
    lines = csv_rows(csv_get(admin, cid))
    line = dict(zip(lines[0], lines[1], strict=True))
    assert line["cls-a"] == "2/3" + FLAG
    assert line["cls-b"] == "done" + FLAG
    assert line["cls-c"] == "1/2"


def test_the_csv_has_names_and_no_id_hashes_or_raw_ids(env, admin):
    cid = env.cohort()
    s = env.student(cid, 'Fake Név, "Quoted" שם')
    env.complete(s, "cls-b")
    resp = csv_get(admin, cid)
    text = resp.content.decode("utf-8-sig")
    assert s.hash not in text and s.raw not in text
    assert not re.search(r"[0-9a-f]{64}", text)
    assert csv_rows(resp)[1][0] == s.name  # quoting and unicode survive the round trip


def test_the_csv_takes_the_same_filters_as_the_json(env, admin):
    c1, c2 = env.cohort(), env.cohort()
    fast, calm = env.student(c1), env.student(c1)
    other = env.student(c2)
    env.passed(fast, "cls-a", [T1], flag=True)
    env.passed(calm, "cls-a", [T1])
    env.passed(other, "cls-a", [T1], flag=True)
    in_c1 = [r[0] for r in csv_rows(csv_get(admin, c1))[1:]]
    assert sorted(in_c1) == sorted([fast.name, calm.name])
    flagged = [r[0] for r in csv_rows(csv_get(admin, c1, flagged=True))[1:]]
    assert flagged == [fast.name]


def test_a_dismissed_flag_has_no_bolt_in_the_csv(env, admin):
    cid = env.cohort()
    s = env.student(cid)
    env.passed(s, "cls-a", [T1], flag=True, dismissed=True)
    lines = csv_rows(csv_get(admin, cid))
    assert FLAG.strip() not in "".join(lines[1])
