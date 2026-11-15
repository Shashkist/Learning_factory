"""Acceptance tests for issue #9: task API, setup on show, navigation (slice 1 of D37).

Spec section 5 (setup contract) and section 7 (lab content), D45 (scripts run as root with
no extra capabilities), D24 (session API). The spec leaves the HTTP shapes open; this exam
fixes the following contract (the build must follow it or change the exam through the
manager). All routes need the student cookie of D23 and use the session id of D24:

  GET  /api/sessions/{sid}/tasks
       -> 200 {"lab_id", "current": <task id or null>,
               "tasks": [{"id", "index" (1-based), "kind" ("quiz"|"hands-on"),
                          "title": {"en", "he"}, "state"}]}
       `state` is "not_started" (no row, or shown_at null), "shown" (shown_at set, not
       passed) or "passed" (passed_at set), read from this session's `task_results` row.
       `title` of a hands-on task is the text of the first Markdown heading of its
       `task.<lang>.md` (without the `#`); of a quiz it is the question.
  GET  /api/sessions/{sid}/tasks/current
       -> 200 task detail, or 404 {"detail": "no_current_task"} while nothing was shown.
  POST /api/sessions/{sid}/tasks/{task_id}/show
       Show that task of the session's lab: run its setup.sh on the session's container
       (hands-on only; a quiz has none), make it the current task and record shown_at.
       Used for the first task and for "Continue from N" on a fresh session. -> 200 detail.
  POST /api/sessions/{sid}/tasks/current/next      (target = the task after the current one)
  POST /api/sessions/{sid}/tasks/current/skip      (same effect as next in this slice)
  POST /api/sessions/{sid}/tasks/current/previous  (target = the task before the current one)
  POST /api/sessions/{sid}/tasks/current/restart   (target = the current task, setup again)
       Each shows its target exactly like `show`; all -> 200 detail of the target.
       No current task -> 409 {"detail": "no_current_task"}; next/skip on the last task ->
       409 {"detail": "no_next_task"}; previous on the first -> 409 {"detail":
       "no_previous_task"}. None of them runs anything in those cases.
  Task detail: {"id", "index", "total", "kind", "title", "state"} plus, for hands-on,
       "text": {"en", "he"} (the task.<lang>.md Markdown) and "checklist": [{"en","he"}];
       for a quiz, "question": {"en","he"} and "options": [{"en","he"}].
  Never anywhere in a response: check.sh or setup.sh text, solution text, or the quiz
  `correct` field (no key named correct, check, setup, solution, check.sh, setup.sh).

Errors, in this order: no/invalid cookie 401; session id not a UUID or unknown 404
{"detail": "no_session"}; another student's session 403; the owner's session not live
(status ended) 410 {"detail": "session_ended"}; task not in the session's lab 404
{"detail": "unknown_task"}.

A failed setup (exit code != 0, or the runtime raises) answers 502 {"detail": {"code":
"setup_failed", "message": <non-empty text>}}; a timed-out one 504 {"detail": {"code":
"setup_timeout", "message": <non-empty text>}}. The script's output is not in the response.
Neither records shown_at nor changes the current task.

The runner is `app.state.runtime.exec_script(container_name, script_text, timeout)`
(see test_issue_9_runner.py); the container is `lab-<session id>`; the script text is the
task's setup.sh exactly as on disk. The timeout is a positive number of seconds, default at
most 600, overridable with LF_SETUP_TIMEOUT_S (read per request). Starting a session runs
no setup. Moving between tasks never removes or starts a container (no reset).

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and
skip locally when it is absent (CI sets it and fails on any skip). Student ids are fake.
Docker is never used here; the runtime is a fake that records exec calls.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import json
import os
import re
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

LAB = "eng"
TASKS = ["01-quiz", "02-files", "03-net", "04-last"]
HANDS_ON = TASKS[1:]
TITLES = {
    "02-files": ("Make files", "יצירת קבצים"),
    "03-net": ("Reach the network", "הגעה לרשת"),
    "04-last": ("The last one", "האחרונה"),
}
QUESTION = ("Which answer is right?", "איזו תשובה נכונה?")
FORBIDDEN_KEYS = {"correct", "check", "setup", "solution", "check.sh", "setup.sh"}
LEAK_MARKERS = ("SETUP-", "CHECK-", "SOLUTION-")

LAB_YAML = """id: eng
title:
  en: Engine lab
  he: מעבדת מנוע
description:
  en: Fake lab for the task engine exam.
  he: מעבדה מזויפת למבחן מנוע המשימות.
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
      - en: first
        he: ראשון
      - en: second
        he: שני
      - en: third
        he: שלישי
    correct: 1
""" + "".join(
    f"""  - id: {tid}
    kind: hands-on
    checklist:
      - en: Item one of {tid}
        he: פריט אחד של {tid}
"""
    for tid in HANDS_ON
)

OTHER_YAML = """id: other
title:
  en: Other lab
  he: מעבדה אחרת
description:
  en: Another fake lab.
  he: מעבדה מזויפת אחרת.
image: term
time_limit_min: 15
status: published
module: 1
lesson: 5
tasks:
  - id: 09-other
    kind: hands-on
    checklist:
      - en: Item
        he: פריט
"""


def setup_text(tid: str) -> str:
    return f"#!/bin/sh\n# SETUP-{tid}\nexit 0\n"


def _task_dir(lab_dir: Path, tid: str) -> None:
    d = lab_dir / "tasks" / tid
    d.mkdir(parents=True)
    en, he = TITLES.get(tid, ("Other task", "משימה אחרת"))
    (d / "task.en.md").write_text(f"# {en}\n\nBODY-{tid}-en\n", encoding="utf-8")
    (d / "task.he.md").write_text(f"# {he}\n\nBODY-{tid}-he\n", encoding="utf-8")
    (d / "solution.en.md").write_text(f"SOLUTION-{tid}-SECRET-en\n", encoding="utf-8")
    (d / "solution.he.md").write_text(f"SOLUTION-{tid}-SECRET-he\n", encoding="utf-8")
    (d / "setup.sh").write_text(setup_text(tid), encoding="utf-8")
    (d / "check.sh").write_text(f"#!/bin/sh\n# CHECK-{tid}-SECRET\nexit 0\n", encoding="utf-8")
    for name in ("setup.sh", "check.sh"):
        mode = (d / name).stat().st_mode
        (d / name).chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_content(root: Path) -> Path:
    for lab_id, text, tasks in ((LAB, LAB_YAML, HANDS_ON), ("other", OTHER_YAML, ["09-other"])):
        d = root / "test" / lab_id
        d.mkdir(parents=True)
        (d / "lab.yaml").write_text(text, encoding="utf-8")
        for tid in tasks:
            _task_dir(d, tid)
    return root


# ---------------------------------------------------------------- fake runtime


class FakeRuntime:
    """Starts and removes nothing for real; records every exec_script call."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.removed: list[str] = []
        self.execs: list[dict] = []
        self.outcome = None  # None = success; an ExecResult to return; an Exception to raise

    async def start(self, spec):
        name = str(spec["name"])
        self.started.append(name)
        return name

    async def remove(self, name):
        self.removed.append(name)

    async def list_lab_containers(self):
        return []

    async def exec_script(self, name, script, timeout=None):
        from app.runtime import ExecResult

        text = script.decode() if isinstance(script, bytes) else script
        self.execs.append({"name": name, "script": text, "timeout": timeout})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if self.outcome is not None:
            return self.outcome
        return ExecResult(0, "setup ok\n", False)

    def setups(self) -> list[str]:
        """Task ids whose setup.sh ran, in order."""
        return [m.group(1) for e in self.execs if (m := re.search(r"SETUP-(\S+)", e["script"]))]


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
    return f"10.79.{n // 250}.{n % 250 + 1}"


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

        raw = str(700000000 + next(_n))
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

    def open_lab(self, client, lab_id: str = LAB) -> str:
        resp = client.post("/api/sessions", json={"lab_id": lab_id})
        assert resp.status_code in (200, 201), resp.text
        return resp.json()["id"]

    def owner(self):
        """A student with a live session of the engine lab: (client, hash, session id)."""
        client, h = self.student()
        return client, h, self.open_lab(client)

    def result(self, session_id: str, task_id: str) -> dict | None:
        async def go(conn):
            r = await conn.fetchrow(
                f"select * from {SCHEMA}.task_results where session_id = $1 and task_id = $2",
                uuid.UUID(session_id), task_id,
            )
            return dict(r) if r else None

        return self.sql(go)

    def shown_at(self, session_id: str, task_id: str):
        row = self.result(session_id, task_id)
        return row["shown_at"] if row else None

    def mark_passed(self, session_id: str, student_hash: str, task_id: str) -> None:
        async def go(conn):
            await conn.execute(
                f"insert into {SCHEMA}.task_results (student, lab_id, session_id, task_id, "
                "shown_at, passed_at) values ($1, $2, $3, $4, now(), now()) "
                "on conflict (session_id, task_id) do update set passed_at = now()",
                student_hash, LAB, uuid.UUID(session_id), task_id,
            )

        self.sql(go)

    def end_in_db(self, session_id: str) -> None:
        async def go(conn):
            await conn.execute(
                f"update {SCHEMA}.lab_sessions set status = 'ended', ended_at = now(), "
                "end_reason = 'student' where id = $1", uuid.UUID(session_id),
            )

        self.sql(go)


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
        "LF_DISK_PATH", "LF_SETUP_TIMEOUT_S",
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
    return client.post(f"{tasks_url(sid)}/{tid}/show")


def nav(client, sid: str, action: str):
    return client.post(f"{tasks_url(sid)}/current/{action}")


def current(client, sid: str):
    return client.get(f"{tasks_url(sid)}/current")


def listing(client, sid: str) -> dict:
    resp = client.get(tasks_url(sid))
    assert resp.status_code == 200, resp.text
    return resp.json()


def keys_of(value) -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        out |= set(value)
        for v in value.values():
            out |= keys_of(v)
    elif isinstance(value, list):
        for v in value:
            out |= keys_of(v)
    return out


def assert_not_leaking(resp) -> None:
    text = resp.text
    for marker in LEAK_MARKERS:
        assert marker not in text, f"{marker} leaked: {text[:300]}"
    assert '"correct"' not in text, text[:300]
    try:
        body = resp.json()
    except ValueError:
        return
    assert not (keys_of(body) & FORBIDDEN_KEYS), keys_of(body) & FORBIDDEN_KEYS


def recent(ts) -> bool:
    return ts is not None and abs((dt.datetime.now(dt.UTC) - ts).total_seconds()) < 120


# ---------------------------------------------------------------- task list and current task


def test_task_list_has_ids_in_order_kinds_and_index(env):
    client, _, sid = env.owner()
    body = listing(client, sid)
    assert body["lab_id"] == LAB
    assert [t["id"] for t in body["tasks"]] == TASKS
    assert [t["index"] for t in body["tasks"]] == [1, 2, 3, 4]
    assert [t["kind"] for t in body["tasks"]] == ["quiz", "hands-on", "hands-on", "hands-on"]
    assert body["current"] is None


def test_task_list_titles_are_bilingual(env):
    client, _, sid = env.owner()
    by_id = {t["id"]: t for t in listing(client, sid)["tasks"]}
    assert by_id["01-quiz"]["title"] == {"en": QUESTION[0], "he": QUESTION[1]}
    for tid, (en, he) in TITLES.items():
        assert by_id[tid]["title"] == {"en": en, "he": he}


def test_task_list_state_is_not_started_before_anything_is_shown(env):
    client, _, sid = env.owner()
    assert {t["state"] for t in listing(client, sid)["tasks"]} == {"not_started"}


def test_task_list_state_follows_shown_and_passed(env):
    client, h, sid = env.owner()
    assert show(client, sid, "02-files").status_code == 200
    env.mark_passed(sid, h, "01-quiz")
    states = {t["id"]: t["state"] for t in listing(client, sid)["tasks"]}
    assert states == {
        "01-quiz": "passed", "02-files": "shown", "03-net": "not_started", "04-last": "not_started"
    }
    assert listing(client, sid)["current"] == "02-files"


def test_current_task_is_404_until_one_is_shown(env):
    client, _, sid = env.owner()
    resp = current(client, sid)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "no_current_task"}


def test_current_task_returns_the_shown_hands_on_task(env):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    resp = current(client, sid)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["id"], body["index"], body["total"], body["kind"]) == (
        "03-net", 3, 4, "hands-on"
    )
    assert body["title"] == {"en": TITLES["03-net"][0], "he": TITLES["03-net"][1]}
    assert body["state"] == "shown"
    assert "BODY-03-net-en" in body["text"]["en"] and "BODY-03-net-he" in body["text"]["he"]
    assert body["checklist"] == [{"en": "Item one of 03-net", "he": "פריט אחד של 03-net"}]


def test_current_task_returns_the_shown_quiz_with_options_but_no_answer(env):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    body = current(client, sid).json()
    assert body["kind"] == "quiz" and body["id"] == "01-quiz"
    assert body["question"] == {"en": QUESTION[0], "he": QUESTION[1]}
    assert [o["en"] for o in body["options"]] == ["first", "second", "third"]
    assert "correct" not in keys_of(body)
    assert '"correct"' not in json.dumps(body)


def test_student_responses_never_carry_scripts_solutions_or_the_quiz_answer(env):
    client, _, sid = env.owner()
    responses = [client.get(tasks_url(sid))]
    for tid in TASKS:
        responses.append(show(client, sid, tid))
        responses.append(current(client, sid))
        responses.append(client.get(tasks_url(sid)))
    for action in ("next", "skip", "previous", "restart"):
        responses.append(nav(client, sid, action))
    env.runtime.outcome = RuntimeError("boom SETUP-leak CHECK-leak")
    responses.append(show(client, sid, "02-files"))
    responses.append(show(client, sid, "no-such-task"))
    assert sum(r.status_code == 200 for r in responses) >= 8
    for resp in responses:
        assert_not_leaking(resp)


# ---------------------------------------------------------------- show runs setup, records shown_at


def test_starting_a_session_runs_no_setup(env):
    env.owner()
    assert env.runtime.execs == []


def test_showing_a_hands_on_task_runs_its_setup_on_the_session_container(env):
    client, _, sid = env.owner()
    resp = show(client, sid, "02-files")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "02-files"
    assert len(env.runtime.execs) == 1
    call = env.runtime.execs[0]
    assert call["name"] == f"lab-{sid}"
    assert call["script"] == setup_text("02-files")
    assert isinstance(call["timeout"], (int, float)) and 0 < call["timeout"] <= 600


def test_setup_timeout_is_configurable_and_read_per_request(env, monkeypatch):
    client, _, sid = env.owner()
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", "7")
    show(client, sid, "02-files")
    assert env.runtime.execs[-1]["timeout"] == 7
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", "11")
    show(client, sid, "03-net")
    assert env.runtime.execs[-1]["timeout"] == 11


def test_showing_a_hands_on_task_records_shown_at_for_the_student_and_lab(env):
    client, h, sid = env.owner()
    assert env.result(sid, "02-files") is None
    show(client, sid, "02-files")
    row = env.result(sid, "02-files")
    assert row is not None and recent(row["shown_at"])
    assert row["student"] == h and row["lab_id"] == LAB
    assert row["passed_at"] is None


def test_showing_a_quiz_runs_no_setup_but_records_shown_at(env):
    client, _, sid = env.owner()
    resp = show(client, sid, "01-quiz")
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "quiz"
    assert env.runtime.execs == []
    assert recent(env.shown_at(sid, "01-quiz"))
    assert current(client, sid).json()["id"] == "01-quiz"


def test_shown_at_is_recorded_only_the_first_time_in_a_session(env):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    first = env.shown_at(sid, "02-files")
    assert first is not None
    show(client, sid, "03-net")
    show(client, sid, "02-files")
    nav(client, sid, "restart")
    assert env.shown_at(sid, "02-files") == first
    assert env.runtime.setups() == ["02-files", "03-net", "02-files", "02-files"]


def test_a_new_session_records_its_own_shown_at(env):
    client, _, first_sid = env.owner()
    show(client, first_sid, "02-files")
    first = env.shown_at(first_sid, "02-files")
    assert client.post(f"/api/sessions/{first_sid}/end").status_code == 204
    second_sid = env.open_lab(client)
    assert second_sid != first_sid
    show(client, second_sid, "02-files")
    second = env.shown_at(second_sid, "02-files")
    assert second is not None and second >= first
    assert env.shown_at(first_sid, "02-files") == first
    assert str(env.result(second_sid, "02-files")["session_id"]) == second_sid


def test_showing_a_task_does_not_mark_the_other_tasks(env):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    assert env.result(sid, "02-files") is None
    assert env.result(sid, "04-last") is None


# ---------------------------------------------------------------- navigation (spec section 5)


def test_next_runs_only_the_target_setup_on_the_current_machine(env):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    env.runtime.execs.clear()
    resp = nav(client, sid, "next")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "03-net"
    assert env.runtime.setups() == ["03-net"]
    assert env.runtime.execs[0]["name"] == f"lab-{sid}"
    assert current(client, sid).json()["id"] == "03-net"
    assert recent(env.shown_at(sid, "03-net"))


def test_skip_runs_only_the_target_setup_on_the_current_machine(env):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    env.runtime.execs.clear()
    resp = nav(client, sid, "skip")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "03-net"
    assert env.runtime.setups() == ["03-net"]
    assert env.runtime.execs[0]["name"] == f"lab-{sid}"
    assert current(client, sid).json()["id"] == "03-net"


def test_next_from_the_quiz_runs_the_next_hands_on_setup(env):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    assert nav(client, sid, "next").json()["id"] == "02-files"
    assert env.runtime.setups() == ["02-files"]


def test_previous_onto_a_quiz_runs_no_setup(env):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    env.runtime.execs.clear()
    resp = nav(client, sid, "previous")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "01-quiz" and resp.json()["kind"] == "quiz"
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "01-quiz"


def test_previous_runs_the_earlier_tasks_setup_again_without_a_new_shown_at(env):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    first = env.shown_at(sid, "02-files")
    show(client, sid, "03-net")
    env.runtime.execs.clear()
    resp = nav(client, sid, "previous")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "02-files"
    assert env.runtime.setups() == ["02-files"]
    assert env.shown_at(sid, "02-files") == first
    assert current(client, sid).json()["id"] == "02-files"


def test_restart_runs_the_current_tasks_setup_again(env):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    first = env.shown_at(sid, "03-net")
    env.runtime.execs.clear()
    resp = nav(client, sid, "restart")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "03-net"
    assert env.runtime.setups() == ["03-net"]
    assert env.runtime.execs[0]["name"] == f"lab-{sid}"
    assert env.shown_at(sid, "03-net") == first
    assert current(client, sid).json()["id"] == "03-net"


def test_restart_of_a_quiz_runs_nothing(env):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    resp = nav(client, sid, "restart")
    assert resp.status_code == 200, resp.text
    assert env.runtime.execs == []


def test_moving_between_tasks_never_resets_the_machine(env):
    client, _, sid = env.owner()
    for step in (
        lambda: show(client, sid, "02-files"),
        lambda: nav(client, sid, "next"),
        lambda: nav(client, sid, "skip"),
        lambda: nav(client, sid, "previous"),
        lambda: nav(client, sid, "restart"),
    ):
        assert step().status_code == 200
    assert env.runtime.started == [f"lab-{sid}"]
    assert env.runtime.removed == []
    assert {e["name"] for e in env.runtime.execs} == {f"lab-{sid}"}


def test_continue_from_n_on_a_new_session_runs_only_setup_n_on_the_fresh_container(env):
    client, _, old_sid = env.owner()
    show(client, old_sid, "02-files")
    assert client.post(f"/api/sessions/{old_sid}/end").status_code == 204
    env.runtime.execs.clear()
    new_sid = env.open_lab(client)
    assert env.runtime.execs == []
    resp = show(client, new_sid, "03-net")
    assert resp.status_code == 200, resp.text
    assert env.runtime.setups() == ["03-net"]
    assert env.runtime.execs[0]["name"] == f"lab-{new_sid}"
    assert env.runtime.started == [f"lab-{old_sid}", f"lab-{new_sid}"]
    assert current(client, new_sid).json()["id"] == "03-net"
    assert env.result(new_sid, "02-files") is None


def test_next_on_the_last_task_is_a_409_and_runs_nothing(env):
    client, _, sid = env.owner()
    show(client, sid, "04-last")
    env.runtime.execs.clear()
    for action in ("next", "skip"):
        resp = nav(client, sid, action)
        assert resp.status_code == 409, resp.text
        assert resp.json() == {"detail": "no_next_task"}
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "04-last"


def test_previous_on_the_first_task_is_a_409_and_runs_nothing(env):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    resp = nav(client, sid, "previous")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "no_previous_task"}
    assert env.runtime.execs == []


@pytest.mark.parametrize("action", ["next", "skip", "previous", "restart"])
def test_navigation_without_a_current_task_is_a_409_and_runs_nothing(env, action):
    client, _, sid = env.owner()
    resp = nav(client, sid, action)
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "no_current_task"}
    assert env.runtime.execs == []


# ---------------------------------------------------------------- failed and timed-out setup


def _failed(env):
    from app.runtime import ExecResult

    env.runtime.outcome = ExecResult(1, "SETUP-OUTPUT-MARKER boom\n", False)


def _timed_out(env):
    from app.runtime import ExecResult

    env.runtime.outcome = ExecResult(None, "SETUP-OUTPUT-MARKER partial\n", True)


def _assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"].strip()
    assert "SETUP-OUTPUT-MARKER" not in resp.text


def test_a_failed_setup_is_a_502_setup_failed_and_records_nothing(env):
    client, _, sid = env.owner()
    _failed(env)
    resp = show(client, sid, "02-files")
    _assert_error(resp, 502, "setup_failed")
    row = env.result(sid, "02-files")
    assert row is None or row["shown_at"] is None
    assert current(client, sid).status_code == 404
    assert {t["state"] for t in listing(client, sid)["tasks"]} == {"not_started"}


def test_a_timed_out_setup_is_a_504_setup_timeout_and_records_nothing(env):
    client, _, sid = env.owner()
    _timed_out(env)
    resp = show(client, sid, "02-files")
    _assert_error(resp, 504, "setup_timeout")
    row = env.result(sid, "02-files")
    assert row is None or row["shown_at"] is None
    assert current(client, sid).status_code == 404


def test_a_runtime_error_during_setup_is_a_502_setup_failed_and_records_nothing(env):
    client, _, sid = env.owner()
    env.runtime.outcome = RuntimeError("docker is gone")
    resp = show(client, sid, "02-files")
    _assert_error(resp, 502, "setup_failed")
    row = env.result(sid, "02-files")
    assert row is None or row["shown_at"] is None


def test_a_failed_next_keeps_the_current_task_and_records_no_shown_at_for_the_target(env):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    _failed(env)
    resp = nav(client, sid, "next")
    _assert_error(resp, 502, "setup_failed")
    assert current(client, sid).json()["id"] == "02-files"
    row = env.result(sid, "03-net")
    assert row is None or row["shown_at"] is None


@pytest.mark.parametrize("action", ["skip", "previous", "restart"])
def test_a_failed_navigation_keeps_the_current_task(env, action):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    first = env.shown_at(sid, "03-net")
    _timed_out(env)
    resp = nav(client, sid, action)
    assert resp.status_code in (502, 504), resp.text
    assert current(client, sid).json()["id"] == "03-net"
    assert env.shown_at(sid, "03-net") == first


def test_after_a_failed_setup_a_retry_that_succeeds_records_shown_at(env):
    client, _, sid = env.owner()
    _failed(env)
    assert show(client, sid, "02-files").status_code == 502
    env.runtime.outcome = None
    resp = show(client, sid, "02-files")
    assert resp.status_code == 200, resp.text
    assert recent(env.shown_at(sid, "02-files"))
    assert current(client, sid).json()["id"] == "02-files"


# ---------------------------------------------------------------- access rules

ROUTES = [
    ("GET", ""),
    ("GET", "/current"),
    ("POST", "/02-files/show"),
    ("POST", "/current/next"),
    ("POST", "/current/skip"),
    ("POST", "/current/previous"),
    ("POST", "/current/restart"),
]


def call(client, method: str, sid: str, suffix: str):
    return client.request(method, f"{tasks_url(sid)}{suffix}")


@pytest.mark.parametrize("method,suffix", ROUTES)
def test_anonymous_callers_get_401_and_nothing_runs(env, method, suffix):
    _, _, sid = env.owner()
    resp = call(env.new_client(), method, sid, suffix)
    assert resp.status_code == 401, resp.text
    assert env.runtime.execs == []


@pytest.mark.parametrize("method,suffix", ROUTES)
def test_another_students_session_is_403_and_nothing_runs(env, method, suffix):
    owner, _, sid = env.owner()
    show(owner, sid, "02-files")
    env.runtime.execs.clear()
    stranger, _ = env.student()
    resp = call(stranger, method, sid, suffix)
    assert resp.status_code == 403, resp.text
    assert env.runtime.execs == []
    assert env.shown_at(sid, "02-files") is not None  # untouched by the stranger
    assert_not_leaking(resp)


def test_a_stranger_cannot_make_task_rows_in_the_owners_session(env):
    _, _, sid = env.owner()
    stranger, _ = env.student()
    assert show(stranger, sid, "03-net").status_code == 403
    assert env.result(sid, "03-net") is None


@pytest.mark.parametrize("method,suffix", ROUTES)
def test_an_ended_session_is_410_and_nothing_runs(env, method, suffix):
    client, _, sid = env.owner()
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    resp = call(client, method, sid, suffix)
    assert resp.status_code == 410, resp.text
    assert env.runtime.execs == []
    assert env.result(sid, "02-files") is None


def test_a_session_ended_by_the_system_is_410_too(env):
    client, _, sid = env.owner()
    env.end_in_db(sid)
    assert show(client, sid, "02-files").status_code == 410
    assert env.runtime.execs == []


@pytest.mark.parametrize("method,suffix", ROUTES)
def test_an_unknown_session_is_404_no_session(env, method, suffix):
    client, _ = env.student()
    for sid in (str(uuid.uuid4()), "not-a-uuid"):
        resp = call(client, method, sid, suffix)
        assert resp.status_code == 404, resp.text
        assert resp.json() == {"detail": "no_session"}
    assert env.runtime.execs == []


def test_a_task_of_another_lab_is_404_unknown_task_and_runs_nothing(env):
    client, _, sid = env.owner()
    resp = show(client, sid, "09-other")
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "unknown_task"}
    assert env.runtime.execs == []
    assert env.result(sid, "09-other") is None


@pytest.mark.parametrize("tid", ["no-such-task", "02-FILES", "02-files%2F..", "0"])
def test_an_unknown_task_id_is_a_404_and_runs_nothing(env, tid):
    client, _, sid = env.owner()
    resp = show(client, sid, tid)
    assert resp.status_code == 404, resp.text
    assert env.runtime.execs == []


def test_the_session_checks_come_before_the_task_check(env):
    client, _, sid = env.owner()
    stranger, _ = env.student()
    assert show(stranger, sid, "no-such-task").status_code == 403
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    assert show(client, sid, "no-such-task").status_code == 410


def test_a_stranger_cannot_read_the_tasks_of_an_ended_session_either(env):
    owner, _, sid = env.owner()
    assert owner.post(f"/api/sessions/{sid}/end").status_code == 204
    stranger, _ = env.student()
    assert stranger.get(tasks_url(sid)).status_code == 403
