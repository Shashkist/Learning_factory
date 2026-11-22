"""Acceptance tests for issue #154: a task solution is revealed after the first graded attempt.

Decisions D128 (the contract), D57 (b, the tab that this replaces) and D127. Same harness as
test_issue_88_check_results.py (CI Postgres, fake runtime, fake students).

CONTRACT (D128):
  POST /api/sessions/{sid}/tasks/{task_id}/solution
     -> 200 {"solution": {"en": str, "he": str}} for a hands-on task once
        task_results.attempts > 0 in this session (a failed or a passing Check);
        before that 403 {"detail": "solution_locked"} and no solution text anywhere in the body.
     -> 404 no_solution for a quiz, 404 unknown_task for an unknown task (the router's code).
     Both solution files exist for every hands-on task (the content validator requires them), so
     there is no English fallback: en and he come back as their own texts.
     Header Cache-Control: no-store. An ended session answers 410 session_ended.
  Only graded attempts of THIS session count: attempts of an earlier, ended session of the same
  student do not unlock the task in a new one. A Check that timed out counts an attempt (#88) and
  unlocks; a Check with reason "error" counts none and stays locked.
  The first successful call stamps task_results.solution_viewed_at (new nullable column), later
  calls never move it. Another student's session answers 403 not_your_session.
  Every task detail (show, current, navigation) gains `solution_available: bool`: false before
  the first attempt, true after. No other task API response carries solution text.

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and skip
locally when it is absent (CI sets it and fails on any skip). Student ids are fake. Docker is
never used here.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import json
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
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)

_n = itertools.count(1)

LAB = "solver"
QUIZ = "01-quiz"
BOTH = "02-both"  # hands-on, both solution files
SECOND = "03-second"  # hands-on, both solution files, its own texts
OPTIONS = ["OPT-alpha", "OPT-bravo"]
CORRECT = 1
SOLUTION = {
    BOTH: {"en": "SOLUTIONTEXT-both-en-zq81", "he": "SOLUTIONTEXT-both-he-zq81"},
    SECOND: {"en": "SOLUTIONTEXT-second-en-zq81", "he": "SOLUTIONTEXT-second-he-zq81"},
}
MARKER = "SOLUTIONTEXT"

LAB_YAML = (
    """id: solver
title:
  en: Solver lab
  he: מעבדת פתרון
description:
  en: Fake lab for the solution exam.
  he: מעבדה מזויפת למבחן הפתרון.
image: term
time_limit_min: 15
internet: false
allow_domains: []
sudo: false
status: published
module: 1
lesson: 5
tasks:
  - id: 01-quiz
    kind: quiz
    question:
      en: Which answer is right?
      he: איזו תשובה נכונה?
    options:
"""
    + "".join(f"      - en: {o}\n        he: {o}-he\n" for o in OPTIONS)
    + f"    correct: {CORRECT}\n"
    + "".join(
        f"  - id: {tid}\n    kind: hands-on\n    checklist:\n      - en: Do it\n        he: לעשות\n"
        for tid in (BOTH, SECOND)
    )
)


def write_content(root: Path) -> Path:
    d = root / "test" / LAB
    d.mkdir(parents=True)
    (d / "lab.yaml").write_text(LAB_YAML, encoding="utf-8")
    quiz = d / "tasks" / QUIZ
    quiz.mkdir(parents=True)
    # a solution file next to a quiz must never be served
    (quiz / "solution.en.md").write_text(f"{MARKER}-quiz-en\n", encoding="utf-8")
    for tid in (BOTH, SECOND):
        t = d / "tasks" / tid
        t.mkdir(parents=True)
        (t / "task.en.md").write_text(f"# Task {tid}\n\nBODY-{tid}-en\n", encoding="utf-8")
        (t / "task.he.md").write_text(f"# משימה {tid}\n\nBODY-{tid}-he\n", encoding="utf-8")
        (t / "solution.en.md").write_text(SOLUTION[tid]["en"] + "\n", encoding="utf-8")
        (t / "solution.he.md").write_text(SOLUTION[tid]["he"] + "\n", encoding="utf-8")
        (t / "setup.sh").write_text("#!/bin/sh\n# SETUP\nexit 0\n", encoding="utf-8")
        (t / "check.sh").write_text(f"#!/bin/sh\n# CHECK-{tid}\nexit 0\n", encoding="utf-8")
        for name in ("setup.sh", "check.sh"):
            mode = (t / name).stat().st_mode
            (t / name).chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return root


# ---------------------------------------------------------------- fake runtime


class FakeRuntime:
    """Starts and removes nothing for real. Setups succeed; a check prints `PASS 1` (exit 0)
    unless `passing` is switched off (then `FAIL 1 not yet`, exit 1). `outcome` overrides that:
    ExecResult(None, "", True) is a timed-out check, an exception is a runtime error."""

    def __init__(self) -> None:
        self.passing = True
        self.outcome = None  # an ExecResult to return, or an Exception to raise, for a check

    async def start(self, spec):
        return str(spec["name"])

    async def remove(self, name):
        return None

    async def list_lab_containers(self):
        return []

    async def exec_script(self, name, script, timeout=None):
        from app.runtime import ExecResult

        text = script.decode() if isinstance(script, bytes) else script
        if "CHECK-" not in text:
            return ExecResult(0, "", False)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if self.outcome is not None:
            return self.outcome
        if self.passing:
            return ExecResult(0, "PASS 1\n", False)
        return ExecResult(1, "FAIL 1 not yet\n", False)


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
    return f"10.154.{n // 250}.{n % 250 + 1}"


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
    def __init__(self, admin_dsn, client, runtime: FakeRuntime):
        self.admin_dsn = admin_dsn
        self.client = client
        self.app = client.app
        self.runtime = runtime
        self.cohort_id: int | None = None

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def new_client(self):
        from fastapi.testclient import TestClient

        c = TestClient(self.app, base_url="https://testserver", client=(fresh_ip(), 50000))
        c.portal = self.client.portal  # the app's pool belongs to the lifespan client's loop
        return c

    def student(self):
        """Seed a fake student and log in; returns (client, id hash)."""
        from argon2 import PasswordHasher

        # Own id range (D47): this exam 915400000+.
        raw = str(915400000 + next(_n))
        pw = PasswordHasher().hash(COHORT_PASSWORD)

        async def seed(conn):
            if self.cohort_id is None:
                self.cohort_id = await conn.fetchval(
                    f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                    "values ($1, $2, $3) returning id",
                    f"Fake cohort {next(_n)}", pw, TODAY + 30 * DAY,
                )
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id, access_until) "
                "values ($1, $2, $3, $4)",
                id_hash(raw), f"Fake Student {raw}", self.cohort_id, None,
            )

        self.sql(seed)
        client = self.new_client()
        resp = client.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return client, id_hash(raw)

    def owner(self):
        client, _h = self.student()
        resp = client.post("/api/sessions", json={"lab_id": LAB})
        assert resp.status_code in (200, 201), resp.text
        return client, resp.json()["id"]

    def result(self, sid: str, tid: str) -> dict | None:
        async def go(conn):
            r = await conn.fetchrow(
                f"select * from {SCHEMA}.task_results where session_id = $1 and task_id = $2",
                uuid.UUID(sid), tid,
            )
            return dict(r) if r else None

        return self.sql(go)

    def backdate_view(self, sid: str, tid: str) -> dt.datetime:
        old = dt.datetime.now(dt.UTC) - dt.timedelta(hours=3)

        async def go(conn):
            await conn.execute(
                f"update {SCHEMA}.task_results set solution_viewed_at = $3 "
                "where session_id = $1 and task_id = $2",
                uuid.UUID(sid), tid, old,
            )

        self.sql(go)
        return old


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


def tasks_url(sid: str) -> str:
    return f"/api/sessions/{sid}/tasks"


def show(client, sid: str, tid: str):
    resp = client.post(f"{tasks_url(sid)}/{tid}/show")
    assert resp.status_code == 200, resp.text
    return resp


def check(client, sid: str, tid: str):
    resp = client.post(f"{tasks_url(sid)}/{tid}/check")
    assert resp.status_code == 200, resp.text
    return resp


def solution(client, sid: str, tid: str):
    return client.post(f"{tasks_url(sid)}/{tid}/solution")


def shown(env, tid: str = BOTH):
    client, sid = env.owner()
    show(client, sid, tid)
    return client, sid


def json_keys(body: str) -> list[str]:
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                found.append(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        walk(json.loads(body))
    except ValueError:
        pass
    return found


# ---------------------------------------------------------------- locked before an attempt


def test_before_any_attempt_the_solution_is_403_solution_locked_without_text(env):
    client, sid = shown(env)
    resp = solution(client, sid, BOTH)
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "solution_locked"}
    assert MARKER not in resp.text
    row = env.result(sid, BOTH)
    assert row["attempts"] == 0
    assert row["solution_viewed_at"] is None


def test_a_task_never_shown_is_also_locked(env):
    client, sid = env.owner()
    resp = solution(client, sid, SECOND)
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "solution_locked"}
    assert MARKER not in resp.text


# ---------------------------------------------------------------- unlocked by a graded attempt


def test_after_a_failed_check_the_solution_is_200_with_both_languages(env):
    client, sid = shown(env)
    env.runtime.passing = False
    assert check(client, sid, BOTH).json()["passed"] is False
    resp = solution(client, sid, BOTH)
    assert resp.status_code == 200, resp.text
    sol = resp.json()["solution"]
    assert set(sol) == {"en", "he"}
    assert SOLUTION[BOTH]["en"] in sol["en"]
    assert SOLUTION[BOTH]["he"] in sol["he"]


def test_after_a_passing_check_the_solution_is_200(env):
    client, sid = shown(env)
    env.runtime.passing = True
    assert check(client, sid, BOTH).json()["passed"] is True
    resp = solution(client, sid, BOTH)
    assert resp.status_code == 200, resp.text
    assert SOLUTION[BOTH]["en"] in resp.json()["solution"]["en"]


def test_the_unlock_is_per_task_not_per_session(env):
    client, sid = shown(env)
    show(client, sid, SECOND)
    env.runtime.passing = False
    check(client, sid, BOTH)
    assert solution(client, sid, BOTH).status_code == 200
    other = solution(client, sid, SECOND)
    assert other.status_code == 403
    assert other.json() == {"detail": "solution_locked"}


def test_each_language_comes_back_as_its_own_text_for_every_task(env):
    client, sid = shown(env)
    show(client, sid, SECOND)
    env.runtime.passing = False
    for tid in (BOTH, SECOND):
        check(client, sid, tid)
        resp = solution(client, sid, tid)
        assert resp.status_code == 200, resp.text
        sol = resp.json()["solution"]
        assert SOLUTION[tid]["en"] in sol["en"] and SOLUTION[tid]["he"] not in sol["en"]
        assert SOLUTION[tid]["he"] in sol["he"] and SOLUTION[tid]["en"] not in sol["he"]


def test_the_solution_response_is_cache_control_no_store(env):
    client, sid = shown(env)
    check(client, sid, BOTH)
    resp = solution(client, sid, BOTH)
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("cache-control") == "no-store"


def test_reading_the_solution_does_not_count_as_an_attempt(env):
    client, sid = shown(env)
    check(client, sid, BOTH)
    before = env.result(sid, BOTH)["attempts"]
    assert solution(client, sid, BOTH).status_code == 200
    assert env.result(sid, BOTH)["attempts"] == before


# ---------------------------------------------------------------- timeout counts, error does not


def test_a_check_that_timed_out_counts_an_attempt_and_unlocks_the_solution(env):
    from app.runtime import ExecResult

    client, sid = shown(env)
    env.runtime.outcome = ExecResult(None, "", True)
    body = check(client, sid, BOTH).json()
    assert body["reason"] == "timeout" and body["passed"] is False
    assert body["attempts"] == 1
    assert env.result(sid, BOTH)["attempts"] == 1
    resp = solution(client, sid, BOTH)
    assert resp.status_code == 200, resp.text
    assert SOLUTION[BOTH]["en"] in resp.json()["solution"]["en"]
    assert client.get(f"{tasks_url(sid)}/current").json()["solution_available"] is True


def test_a_check_that_ended_with_reason_error_counts_nothing_and_stays_locked(env):
    client, sid = shown(env)
    env.runtime.outcome = RuntimeError("docker is gone")
    body = check(client, sid, BOTH).json()
    assert body["reason"] == "error" and body["passed"] is False
    assert body["attempts"] == 0
    assert env.result(sid, BOTH)["attempts"] == 0
    resp = solution(client, sid, BOTH)
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "solution_locked"}
    assert MARKER not in resp.text
    assert env.result(sid, BOTH)["solution_viewed_at"] is None
    assert client.get(f"{tasks_url(sid)}/current").json()["solution_available"] is False


# ---------------------------------------------------------------- session scope


def test_an_ended_session_answers_410_with_no_text_and_stamps_nothing(env):
    client, sid = shown(env)
    check(client, sid, BOTH)
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    resp = solution(client, sid, BOTH)
    assert resp.status_code == 410, resp.text
    assert resp.json() == {"detail": "session_ended"}
    assert MARKER not in resp.text
    assert env.result(sid, BOTH)["solution_viewed_at"] is None


def test_attempts_in_an_earlier_ended_session_do_not_unlock_a_new_session(env):
    client, sid = shown(env)
    env.runtime.passing = False
    check(client, sid, BOTH)
    assert solution(client, sid, BOTH).status_code == 200
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    resp = client.post("/api/sessions", json={"lab_id": LAB})
    assert resp.status_code in (200, 201), resp.text
    new_sid = resp.json()["id"]
    assert new_sid != sid
    assert show(client, new_sid, BOTH).json()["solution_available"] is False
    locked = solution(client, new_sid, BOTH)
    assert locked.status_code == 403, locked.text
    assert locked.json() == {"detail": "solution_locked"}
    assert MARKER not in locked.text
    assert env.result(new_sid, BOTH)["solution_viewed_at"] is None


# ---------------------------------------------------------------- recording the view


def test_the_first_view_stamps_solution_viewed_at_and_later_views_do_not_move_it(env):
    client, sid = shown(env)
    check(client, sid, BOTH)
    assert env.result(sid, BOTH)["solution_viewed_at"] is None  # a check alone is not a view
    assert solution(client, sid, BOTH).status_code == 200
    first = env.result(sid, BOTH)["solution_viewed_at"]
    assert first is not None
    assert abs((dt.datetime.now(dt.UTC) - first).total_seconds()) < 120
    old = env.backdate_view(sid, BOTH)
    assert solution(client, sid, BOTH).status_code == 200
    assert env.result(sid, BOTH)["solution_viewed_at"] == old


# ---------------------------------------------------------------- kinds, unknown tasks, owners


def test_a_quiz_is_404_no_solution_even_after_an_answer(env):
    client, sid = env.owner()
    show(client, sid, QUIZ)
    resp = solution(client, sid, QUIZ)
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "no_solution"}
    client.post(f"{tasks_url(sid)}/{QUIZ}/answer", json={"option": 0})
    resp = solution(client, sid, QUIZ)
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "no_solution"}
    assert MARKER not in resp.text


@pytest.mark.parametrize("tid", ["no-such-task", "99-nothing"])
def test_an_unknown_task_is_404_unknown_task(env, tid):
    client, sid = env.owner()
    resp = solution(client, sid, tid)
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "unknown_task"}


def test_another_students_session_is_403_not_your_session_and_stamps_nothing(env):
    client, sid = shown(env)
    check(client, sid, BOTH)
    stranger, _h = env.student()
    resp = solution(stranger, sid, BOTH)
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "not_your_session"}
    assert MARKER not in resp.text
    assert env.result(sid, BOTH)["solution_viewed_at"] is None


def test_a_request_without_a_login_is_401(env):
    client, sid = shown(env)
    check(client, sid, BOTH)
    anon = env.new_client()
    assert solution(anon, sid, BOTH).status_code == 401


# ---------------------------------------------------------------- solution_available


def test_solution_available_is_false_before_and_true_after_the_first_attempt(env):
    client, sid = env.owner()
    assert show(client, sid, BOTH).json()["solution_available"] is False
    assert client.get(f"{tasks_url(sid)}/current").json()["solution_available"] is False
    env.runtime.passing = False
    check(client, sid, BOTH)  # a failed attempt is enough
    assert client.get(f"{tasks_url(sid)}/current").json()["solution_available"] is True
    assert show(client, sid, BOTH).json()["solution_available"] is True


def test_solution_available_follows_the_task_through_navigation(env):
    client, sid = shown(env, BOTH)
    check(client, sid, BOTH)
    nxt = client.post(f"{tasks_url(sid)}/current/next")
    assert nxt.status_code == 200, nxt.text
    assert nxt.json()["id"] == SECOND
    assert nxt.json()["solution_available"] is False
    back = client.post(f"{tasks_url(sid)}/current/previous")
    assert back.status_code == 200, back.text
    assert back.json()["id"] == BOTH
    assert back.json()["solution_available"] is True


def test_a_quiz_detail_is_never_solution_available(env):
    client, sid = env.owner()
    assert show(client, sid, QUIZ).json()["solution_available"] is False
    client.post(f"{tasks_url(sid)}/{QUIZ}/answer", json={"option": 0})
    assert client.get(f"{tasks_url(sid)}/current").json()["solution_available"] is False


# ---------------------------------------------------------------- no other response leaks it


def test_no_other_task_response_carries_solution_text_before_or_after_an_attempt(env):
    client, sid = env.owner()
    bodies: list[str] = []

    def sweep():
        bodies.append(show(client, sid, BOTH).text)
        bodies.append(show(client, sid, SECOND).text)
        bodies.append(client.get(f"{tasks_url(sid)}/current").text)
        bodies.append(client.get(tasks_url(sid)).text)
        bodies.append(client.post(f"{tasks_url(sid)}/current/previous").text)
        bodies.append(client.post(f"{tasks_url(sid)}/current/next").text)

    show(client, sid, QUIZ)
    sweep()
    env.runtime.passing = False
    bodies.append(check(client, sid, BOTH).text)
    bodies.append(check(client, sid, SECOND).text)
    env.runtime.passing = True
    bodies.append(check(client, sid, BOTH).text)
    bodies.append(client.post(f"{tasks_url(sid)}/{QUIZ}/answer", json={"option": 0}).text)
    sweep()
    assert len(bodies) == 16
    for body in bodies:
        assert MARKER not in body, body
        assert [k for k in json_keys(body) if "solution" in k.lower()] in (
            [],
            ["solution_available"],
        ), body
