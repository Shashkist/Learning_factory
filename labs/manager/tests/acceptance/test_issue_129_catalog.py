"""Acceptance tests for issue #129: the student catalog API (slice 2 of #10, D53, D54).

Spec sections 6, 7, 9. Same harness as test_issue_89_completion.py (CI Postgres, fake runtime,
fake students, fake content). Database tests skip locally when LF_TEST_DATABASE_URL is absent
and run in CI. Student ids are fake.

CONTRACT chosen by this exam: GET /api/labs

Access. Students only, with the existing auth convention: signed out -> 401 `not_authenticated`;
a signed-in admin -> 403 `student_only`.

Response: 200, a JSON ARRAY (no wrapper object), one element per published lab, ordered by
`module`, then `lesson`, then `id`. Every element has exactly these keys:

  {
    "id":          "cat-a",                         lab id (the lab.yaml id)
    "title":       {"en": "...", "he": "..."},      from lab.yaml
    "description": {"en": "...", "he": "..."},      from lab.yaml
    "module":      1,                               integer, from lab.yaml
    "lesson":      2,                               integer, from lab.yaml
    "tasks":       3,                               integer, number of tasks in the lab
    "progress":    "done" | "2/3" | "not_started",  string, rules below
    "session_id":  "<uuid>" | null                  the student's LIVE session of THIS lab
  }

progress (for the signed-in student only):
  "done"         a lab_completions row exists for (student, lab), whatever the sessions say;
  "n/m"          otherwise, when the student has at least one session of the lab: n = tasks with a
                 passed_at in the live session, or when there is no live one in the most recently
                 started session (a started lab with nothing passed yet is "0/3"); m = `tasks`;
  "not_started"  otherwise (no session of that lab, no completion).
session_id is the id of the student's live session (status starting or running) when that
session belongs to this lab, otherwise null (also for "done" labs without a live session; a
live "Redo" session of a done lab gives progress "done" and its session_id).

Which labs: only `status: published`. `draft` and `internal` labs are never listed, not even
for a student of the `lf-smoke` cohort (D54). Tasks passed in older sessions do not count
for a newer session.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import os
import stat
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

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
CORRECT = 1
QUIZ, PLAIN, SHORT = "01-quiz", "02-plain", "03-short"

_n = itertools.count(1)

# lab id -> (status, module, lesson, tasks)
LABS: dict[str, tuple[str, int, int, list[tuple[str, str]]]] = {
    "cat-a": ("published", 1, 2, [(QUIZ, "quiz"), (PLAIN, "hands-on"), (SHORT, "hands-on")]),
    "cat-b": ("published", 1, 1, [(PLAIN, "hands-on")]),
    "cat-c": ("published", 2, 1, [(PLAIN, "hands-on"), (SHORT, "hands-on")]),
    "cat-draft": ("draft", 1, 3, [(PLAIN, "hands-on")]),
    "cat-internal": ("internal", 1, 4, [(PLAIN, "hands-on")]),
}
PUBLISHED = ["cat-b", "cat-a", "cat-c"]  # the expected order: module, lesson


def _task_yaml(tid: str, kind: str) -> str:
    if kind == "quiz":
        return (
            f"  - id: {tid}\n    kind: quiz\n    question:\n      en: Q {tid}?\n"
            "      he: מה?\n    options:\n"
            "      - en: A\n        he: א\n      - en: B\n        he: ב\n"
            f"    correct: {CORRECT}\n"
        )
    return f"  - id: {tid}\n    kind: hands-on\n    checklist:\n      - en: Do it\n        he: לעשות\n"


def write_content(root: Path) -> Path:
    for lab_id, (status, module, lesson, tasks) in LABS.items():
        d = root / "test" / lab_id
        d.mkdir(parents=True)
        (d / "lab.yaml").write_text(
            f"id: {lab_id}\ntitle:\n  en: Title {lab_id}\n  he: כותרת {lab_id}\n"
            f"description:\n  en: About {lab_id}.\n  he: על {lab_id}.\n"
            "image: term\ntime_limit_min: 15\ninternet: false\nallow_domains: []\nsudo: false\n"
            f"status: {status}\nmodule: {module}\nlesson: {lesson}\ntasks:\n"
            + "".join(_task_yaml(*t) for t in tasks),
            encoding="utf-8",
        )
        for tid, _kind in tasks:
            td = d / "tasks" / tid
            td.mkdir(parents=True)
            for lang in ("en", "he"):
                (td / f"task.{lang}.md").write_text(f"# Task {tid}\n\nBODY\n", encoding="utf-8")
                (td / f"solution.{lang}.md").write_text("SOLUTION-x\n", encoding="utf-8")
            (td / "setup.sh").write_text("#!/bin/sh\n# SETUP\nexit 0\n", encoding="utf-8")
            (td / "check.sh").write_text(f"#!/bin/sh\n# CHECK-{tid}\nexit 0\n", encoding="utf-8")
            for name in ("setup.sh", "check.sh"):
                mode = (td / name).stat().st_mode
                (td / name).chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return root


class FakeRuntime:
    """Starts and removes nothing for real; every setup and every check passes."""

    async def start(self, spec):
        return str(spec["name"])

    async def remove(self, name):
        return None

    async def list_lab_containers(self):
        return []

    async def exec_script(self, name, script, timeout=None):
        from app.runtime import ExecResult

        text = script.decode() if isinstance(script, bytes) else script
        return ExecResult(0, "PASS 1\n" if "CHECK-" in text else "", False)


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
    return f"10.129.{n // 250}.{n % 250 + 1}"


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
    def __init__(self, admin_dsn, client, runtime):
        self.admin_dsn = admin_dsn
        self.client = client
        self.app = client.app
        self.runtime = runtime
        self.cohorts: dict[str, int] = {}

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def new_client(self):
        from fastapi.testclient import TestClient

        c = TestClient(self.app, base_url="https://testserver", client=(fresh_ip(), 50000))
        c.portal = self.client.portal  # the app's pool belongs to the lifespan client's loop
        return c

    def student(self, cohort: str = "Fake cohort"):
        """Seed a fake student in `cohort` and log in; returns the client."""
        from argon2 import PasswordHasher

        raw = str(1290000000 + next(_n))  # own id range (D47)
        pw = PasswordHasher().hash(COHORT_PASSWORD)

        async def seed(conn):
            if cohort not in self.cohorts:
                self.cohorts[cohort] = await conn.fetchval(
                    f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                    "values ($1, $2, $3) returning id",
                    cohort, pw, TODAY + 30 * DAY,
                )
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id, access_until) "
                "values ($1, $2, $3, $4)",
                id_hash(raw), f"Fake Student {raw}", self.cohorts[cohort], None,
            )

        self.sql(seed)
        client = self.new_client()
        resp = client.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return client

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
        resp = client.post("/api/auth/admin/login", json={"username": name, "password": ADMIN_PASSWORD})
        assert resp.status_code == 200, resp.text
        return client


@pytest.fixture
def env(_db, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

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
        "LF_TRUSTED_PROXY", "LF_MAX_CODE", "LF_MEM_RESERVE_MB", "LF_HEARTBEAT_TIMEOUT_S",
        "LF_DISK_PATH", "LF_SETUP_TIMEOUT_S", "LF_CHECK_TIMEOUT_S",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.main import app

    async def end_all(conn):
        await conn.execute(
            f"update {SCHEMA}.lab_sessions set status = 'ended', ended_at = now(), "
            "end_reason = 'orphan' where status in ('starting', 'running')"
        )

    runtime = FakeRuntime()
    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        app.state.runtime = runtime
        e = Env(_db, c, runtime)
        e.sql(end_all)
        yield e
        e.sql(end_all)


# ---------------------------------------------------------------- helpers


def catalog(client) -> list[dict]:
    resp = client.get("/api/labs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list), body
    return body


def entry(client, lab_id: str) -> dict:
    found = [x for x in catalog(client) if x["id"] == lab_id]
    assert len(found) == 1, f"{lab_id} not listed exactly once"
    return found[0]


def open_lab(client, lab_id: str) -> str:
    resp = client.post("/api/sessions", json={"lab_id": lab_id})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def end_lab(client, sid: str) -> None:
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204


def pass_task(client, sid: str, lab: str, tid: str) -> None:
    base = f"/api/sessions/{sid}/tasks/{tid}"
    assert client.post(f"{base}/show").status_code == 200
    kind = next(k for t, k in LABS[lab][3] if t == tid)
    if kind == "quiz":
        resp = client.post(f"{base}/answer", json={"option": CORRECT})
        assert resp.status_code == 200 and resp.json()["correct"] is True, resp.text
    else:
        resp = client.post(f"{base}/check")
        assert resp.status_code == 200 and resp.json()["passed"] is True, resp.text


def pass_first(client, sid: str, lab: str, count: int) -> None:
    for tid, _k in LABS[lab][3][:count]:
        pass_task(client, sid, lab, tid)


# ================================================================ no database needed


def test_the_catalog_route_exists_and_is_get_only():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/labs" in paths, "GET /api/labs is missing"
    assert "get" in paths["/api/labs"]


def test_signed_out_gets_401_without_touching_the_database():
    from fastapi.testclient import TestClient

    from app.main import app

    resp = TestClient(app, base_url="https://testserver").get("/api/labs")  # no lifespan
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "not_authenticated"


# ================================================================ access


def test_signed_out_is_401(env):
    resp = env.new_client().get("/api/labs")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "not_authenticated"


def test_an_admin_is_refused_with_403_student_only(env):
    resp = env.admin().get("/api/labs")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "student_only"


# ================================================================ which labs and which fields


def test_only_published_labs_are_listed_in_module_then_lesson_order(env):
    client = env.student()
    assert [x["id"] for x in catalog(client)] == PUBLISHED


def test_draft_and_internal_labs_are_never_listed(env):
    ids = {x["id"] for x in catalog(env.student())}
    assert "cat-draft" not in ids and "cat-internal" not in ids


def test_an_lf_smoke_student_still_does_not_see_internal_or_draft_labs(env):
    client = env.student(cohort="lf-smoke")
    ids = [x["id"] for x in catalog(client)]
    assert "cat-internal" not in ids and "cat-draft" not in ids
    assert ids == PUBLISHED


def test_every_entry_has_exactly_the_contract_keys(env):
    for x in catalog(env.student()):
        assert set(x) == {
            "id", "title", "description", "module", "lesson", "tasks", "progress", "session_id",
        }


def test_entries_carry_titles_descriptions_module_lesson_and_task_count(env):
    x = entry(env.student(), "cat-a")
    assert x["title"] == {"en": "Title cat-a", "he": "כותרת cat-a"}
    assert x["description"] == {"en": "About cat-a.", "he": "על cat-a."}
    assert x["module"] == 1 and x["lesson"] == 2
    assert isinstance(x["module"], int) and isinstance(x["lesson"], int)
    assert x["tasks"] == 3
    assert entry(env.student(), "cat-c")["tasks"] == 2
    assert entry(env.student(), "cat-c")["module"] == 2


# ================================================================ progress


def test_a_lab_never_opened_is_not_started_with_no_session(env):
    for x in catalog(env.student()):
        assert x["progress"] == "not_started"
        assert x["session_id"] is None


def test_a_just_opened_lab_is_0_of_m_and_carries_the_live_session_id(env):
    client = env.student()
    sid = open_lab(client, "cat-a")
    x = entry(client, "cat-a")
    assert x["progress"] == "0/3"
    assert x["session_id"] == sid
    assert entry(client, "cat-b")["session_id"] is None  # other labs: no live session
    assert entry(client, "cat-b")["progress"] == "not_started"


def test_passed_tasks_of_the_live_session_show_as_n_of_m(env):
    client = env.student()
    sid = open_lab(client, "cat-a")
    pass_first(client, sid, "cat-a", 1)
    assert entry(client, "cat-a")["progress"] == "1/3"
    pass_first(client, sid, "cat-a", 2)
    x = entry(client, "cat-a")
    assert x["progress"] == "2/3" and x["session_id"] == sid


def test_after_the_session_ends_the_latest_session_progress_stays_and_session_id_is_null(env):
    client = env.student()
    sid = open_lab(client, "cat-a")
    pass_first(client, sid, "cat-a", 2)
    end_lab(client, sid)
    x = entry(client, "cat-a")
    assert x["progress"] == "2/3"
    assert x["session_id"] is None


def test_a_newer_session_counts_only_its_own_tasks(env):
    client = env.student()
    first = open_lab(client, "cat-a")
    pass_first(client, first, "cat-a", 2)
    end_lab(client, first)
    second = open_lab(client, "cat-a")
    x = entry(client, "cat-a")
    assert x["progress"] == "0/3" and x["session_id"] == second
    pass_first(client, second, "cat-a", 1)
    assert entry(client, "cat-a")["progress"] == "1/3"


def test_a_completed_lab_is_done_and_has_no_session_id_once_ended(env):
    client = env.student()
    sid = open_lab(client, "cat-b")
    pass_first(client, sid, "cat-b", 1)
    end_lab(client, sid)
    x = entry(client, "cat-b")
    assert x["progress"] == "done"
    assert x["session_id"] is None


def test_done_stays_done_when_a_redo_session_is_open_and_shows_its_id(env):
    client = env.student()
    sid = open_lab(client, "cat-c")
    pass_first(client, sid, "cat-c", 2)
    end_lab(client, sid)
    redo = open_lab(client, "cat-c")
    x = entry(client, "cat-c")
    assert x["progress"] == "done"
    assert x["session_id"] == redo


def test_progress_is_per_student(env):
    a, b = env.student(), env.student()
    sid = open_lab(a, "cat-b")
    pass_first(a, sid, "cat-b", 1)
    assert entry(a, "cat-b")["progress"] == "done"
    other = entry(b, "cat-b")
    assert other["progress"] == "not_started" and other["session_id"] is None


def test_the_live_session_id_is_listed_on_its_own_lab_only(env):
    client = env.student()
    sid = open_lab(client, "cat-c")
    assert [x["id"] for x in catalog(client) if x["session_id"] == sid] == ["cat-c"]
