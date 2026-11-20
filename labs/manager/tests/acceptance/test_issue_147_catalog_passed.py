"""Acceptance tests for issue #147 (manager nit from the review of #146): `passed` in the catalog
counts only task ids that are still in lab.yaml, so progress never reads "n/m" with n > m.

Spec sections 6, 9. Same harness as test_issue_129_catalog.py (CI Postgres, fake runtime, fake
students, fake content). Database tests skip locally when LF_TEST_DATABASE_URL is absent and run
in CI. Student ids are fake.

Scenario: a task id is removed from lab.yaml after a student passed it (the task_results row
stays). GET /api/labs must show progress from the tasks that still exist: "<n>/<tasks>" with n
counting only current task ids, and never n greater than tasks.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import os
import stat
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

# lab id -> (status, module, lesson, tasks); lessons inside the module ranges of content.py
LABS: dict[str, tuple[str, int, int, list[tuple[str, str]]]] = {
    "cat-a": ("published", 1, 6, [(QUIZ, "quiz"), (PLAIN, "hands-on"), (SHORT, "hands-on")]),
    "cat-b": ("published", 1, 5, [(PLAIN, "hands-on")]),
    "cat-c": ("published", 2, 13, [(PLAIN, "hands-on"), (SHORT, "hands-on")]),
    "cat-draft": ("draft", 1, 7, [(PLAIN, "hands-on")]),
    "cat-internal": ("internal", 1, 8, [(PLAIN, "hands-on")]),
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
    return (f"  - id: {tid}\n    kind: hands-on\n    checklist:\n"
            "      - en: Do it\n        he: לעשות\n")


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
        resp = client.post("/api/auth/admin/login",
                           json={"username": name, "password": ADMIN_PASSWORD})
        assert resp.status_code == 200, resp.text
        return client


@pytest.fixture
def env(_db, monkeypatch, tmp_path, no_janitor):
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


# ================================================================ #147

STALE = ["90-removed-a", "91-removed-b", "92-removed-c", "93-removed-d"]


def seed_stale_passes(env, sid: str, ids: list[str]) -> None:
    """Task results for ids that lab.yaml no longer lists, all passed (a lab edited after use)."""

    async def seed(conn):
        row = await conn.fetchrow(
            f"select student, lab_id from {SCHEMA}.lab_sessions where id = $1::uuid", sid
        )
        for tid in ids:
            await conn.execute(
                f"insert into {SCHEMA}.task_results (student, lab_id, session_id, task_id, "
                "shown_at, passed_at, attempts) values ($1, $2, $3::uuid, $4, now(), now(), 1)",
                row["student"], row["lab_id"], sid, tid,
            )

    env.sql(seed)


def test_passed_ignores_task_ids_that_are_no_longer_in_lab_yaml(env):
    client = env.student()
    sid = open_lab(client, "cat-a")
    pass_first(client, sid, "cat-a", 1)
    seed_stale_passes(env, sid, STALE[:2])
    x = entry(client, "cat-a")
    assert x["progress"] == "1/3", x
    assert x["tasks"] == 3


def test_a_session_with_only_removed_tasks_passed_is_0_of_m(env):
    client = env.student()
    sid = open_lab(client, "cat-a")
    seed_stale_passes(env, sid, STALE[:1])
    assert entry(client, "cat-a")["progress"] == "0/3"


def test_progress_never_exceeds_the_task_count(env):
    client = env.student()
    sid = open_lab(client, "cat-a")
    pass_first(client, sid, "cat-a", 2)
    seed_stale_passes(env, sid, STALE)  # 2 real + 4 removed = 6 passed rows for 3 tasks
    for x in catalog(client):
        if "/" in x["progress"]:
            n, m = (int(v) for v in x["progress"].split("/"))
            assert n <= m == x["tasks"], x
    assert entry(client, "cat-a")["progress"] == "2/3"


def test_removed_task_ids_do_not_count_in_an_ended_latest_session_either(env):
    client = env.student()
    sid = open_lab(client, "cat-a")
    pass_first(client, sid, "cat-a", 1)
    seed_stale_passes(env, sid, STALE[:3])
    end_lab(client, sid)
    x = entry(client, "cat-a")
    assert x["progress"] == "1/3" and x["session_id"] is None
