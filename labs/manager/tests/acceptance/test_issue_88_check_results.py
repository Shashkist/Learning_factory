"""Acceptance tests for issue #88: check results, attempts and quiz grading (slice 2 of D37).

Spec sections 5 (task flow), 7 (the `check.sh` contract) and 8 (results), decisions D45
(scripts run as root with no extra capabilities), D47 (own student-id range) and D50 (the API
shape, which this exam follows to the letter). Two routes under the slice 1 task router, same
cookie, owner / live-session / lab checks and error order as test_issue_9_task_api.py:

  POST /api/sessions/{sid}/tasks/{task_id}/check    (hands-on only)
       -> 200 {"passed": bool, "attempts": int, "reason": null | "timeout" | "error",
               "checklist": [{"n": int, "text": {"en","he"}, "passed": bool,
                              "hint": str | null}]}
  POST /api/sessions/{sid}/tasks/{task_id}/answer   (quiz only), body {"option": <index>}
       -> 200 {"correct": bool, "attempts": int}

`check.sh` runs through `app.state.runtime.exec_script(container, script_text, timeout)` on
the container `lab-<session id>`, script text exactly as on disk, timeout 20 seconds. Passed
only when the script exited 0 and printed `PASS <n>` for every checklist item; missing,
conflicting-duplicate or unknown lines count as failed. A timeout (`ExecResult.timed_out`) or
a runtime exception is a normal 200 with passed false, reason "timeout" / "error" and every
item failed, quickly, never a 5xx. The script's raw output is never echoed (only the hint
text after `FAIL <n> `). Every graded call adds one to `task_results.attempts`; `passed_at`
is set on the first pass only. The quiz's correct index is never in any response. Wrong kind
for the route is 409 wrong_task_kind, a task never shown is 409 task_not_shown, an unknown
task or one of another lab is 404 unknown_task, an option out of range is 422 with no attempt.

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and skip
locally when it is absent (CI sets it and fails on any skip). Student ids are fake. Docker is
never used here; the runtime is a fake that scripts the output of each check.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import os
import stat
import time
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

LAB = "grader"
QUIZ = "01-quiz"
MULTI = "02-multi"  # three checklist items
SINGLE = "03-single"  # one checklist item
HANDS_ON = [MULTI, SINGLE]
ITEMS = {
    MULTI: [
        ("Create the file", "ליצור את הקובץ"),
        ("Make it readable", "להפוך אותו לקריא"),
        ("Put text in it", "לכתוב בו טקסט"),
    ],
    SINGLE: [("Do the one thing", "לעשות את הדבר האחד")],
}
OPTIONS = ["OPT-alpha", "OPT-bravo", "OPT-charlie"]
CORRECT = 1
FORBIDDEN_KEYS = {"correct", "check", "setup", "solution", "check.sh", "setup.sh"}
LEAK_MARKERS = ("SETUP-", "CHECK-", "SOLUTION-")
OUT_MARKER = "RAW-OUTPUT-MARKER"

LAB_YAML = (
    """id: grader
title:
  en: Grader lab
  he: מעבדת בדיקה
description:
  en: Fake lab for the check exam.
  he: מעבדה מזויפת למבחן הבדיקה.
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
        f"  - id: {tid}\n    kind: hands-on\n    checklist:\n"
        + "".join(f"      - en: {en}\n        he: {he}\n" for en, he in ITEMS[tid])
        for tid in HANDS_ON
    )
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


def check_text(tid: str) -> str:
    return f"#!/bin/sh\n# CHECK-{tid}-SECRET\nexit 0\n"


def setup_text(tid: str) -> str:
    return f"#!/bin/sh\n# SETUP-{tid}\nexit 0\n"


def _task_dir(lab_dir: Path, tid: str) -> None:
    d = lab_dir / "tasks" / tid
    d.mkdir(parents=True)
    (d / "task.en.md").write_text(f"# Task {tid}\n\nBODY-{tid}-en\n", encoding="utf-8")
    (d / "task.he.md").write_text(f"# משימה {tid}\n\nBODY-{tid}-he\n", encoding="utf-8")
    (d / "solution.en.md").write_text(f"SOLUTION-{tid}-SECRET-en\n", encoding="utf-8")
    (d / "solution.he.md").write_text(f"SOLUTION-{tid}-SECRET-he\n", encoding="utf-8")
    (d / "setup.sh").write_text(setup_text(tid), encoding="utf-8")
    (d / "check.sh").write_text(check_text(tid), encoding="utf-8")
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
    """Starts and removes nothing for real. Setups succeed; each check returns `check_outcome`
    (an ExecResult to return, or an Exception to raise), and every exec is recorded."""

    def __init__(self) -> None:
        self.execs: list[dict] = []
        self.check_outcome = None
        self.setup_outcome = None

    async def start(self, spec):
        return str(spec["name"])

    async def remove(self, name):
        return None

    async def list_lab_containers(self):
        return []

    async def exec_script(self, name, script, timeout=None):
        from app.runtime import ExecResult

        text = script.decode() if isinstance(script, bytes) else script
        self.execs.append({"name": name, "script": text, "timeout": timeout})
        outcome = self.check_outcome if "CHECK-" in text else self.setup_outcome
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            return outcome
        return ExecResult(0, "", False)

    def checks(self) -> list[dict]:
        return [e for e in self.execs if "CHECK-" in e["script"]]

    def say(self, output: str, exit_code: int = 0) -> None:
        from app.runtime import ExecResult

        self.check_outcome = ExecResult(exit_code, output, False)


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
    return f"10.188.{n // 250}.{n % 250 + 1}"


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

        # Own id range (D47): #6 uses 700000000+, #9 uses 790000000+, this exam 880000000+.
        raw = str(880000000 + next(_n))
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

    def attempts(self, session_id: str, task_id: str) -> int:
        row = self.result(session_id, task_id)
        return row["attempts"] if row else 0

    def end_in_db(self, session_id: str) -> None:
        async def go(conn):
            await conn.execute(
                f"update {SCHEMA}.lab_sessions set status = 'ended', ended_at = now(), "
                "end_reason = 'student' where id = $1", uuid.UUID(session_id),
            )

        self.sql(go)


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
    return client.post(f"{tasks_url(sid)}/{tid}/check")


def answer(client, sid: str, tid: str, option):
    return client.post(f"{tasks_url(sid)}/{tid}/answer", json={"option": option})


def shown_owner(env, tid: str):
    """An owner with a live session whose task `tid` was shown (its setup ran)."""
    client, h, sid = env.owner()
    show(client, sid, tid)
    env.runtime.execs.clear()
    return client, h, sid


def pass_lines(tid: str) -> str:
    return "".join(f"PASS {i}\n" for i in range(1, len(ITEMS[tid]) + 1))


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


def assert_not_leaking(resp, allow_correct_key: bool = False) -> None:
    text = resp.text
    for marker in LEAK_MARKERS:
        assert marker not in text, f"{marker} leaked: {text[:300]}"
    assert OUT_MARKER not in text, f"raw check output leaked: {text[:300]}"
    forbidden = FORBIDDEN_KEYS - ({"correct"} if allow_correct_key else set())
    if not allow_correct_key:
        assert '"correct"' not in text, text[:300]
    try:
        body = resp.json()
    except ValueError:
        return
    assert not (keys_of(body) & forbidden), keys_of(body) & forbidden


def recent(ts) -> bool:
    return ts is not None and abs((dt.datetime.now(dt.UTC) - ts).total_seconds()) < 120


def by_n(body: dict) -> dict[int, dict]:
    return {item["n"]: item for item in body["checklist"]}


# ---------------------------------------------------------------- the routes exist (no database)


def test_the_check_and_answer_routes_are_in_the_api_description():
    from app.main import app

    paths = app.openapi()["paths"]
    prefix = "/api/sessions/{session}/tasks/{task_id}"
    assert "post" in paths.get(f"{prefix}/check", {}), sorted(paths)
    assert "post" in paths.get(f"{prefix}/answer", {}), sorted(paths)


# ---------------------------------------------------------------- check: running the script


def test_check_runs_the_tasks_check_sh_as_text_on_the_session_container_with_a_20s_timeout(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say(pass_lines(MULTI))
    resp = check(client, sid, MULTI)
    assert resp.status_code == 200, resp.text
    assert len(env.runtime.execs) == 1
    call = env.runtime.execs[0]
    assert call["name"] == f"lab-{sid}"
    assert call["script"] == check_text(MULTI)
    assert call["timeout"] == 20


def test_check_runs_only_the_check_never_the_setup_again(env):
    client, _, sid = shown_owner(env, SINGLE)
    env.runtime.say("PASS 1\n")
    check(client, sid, SINGLE)
    assert [e["script"] for e in env.runtime.execs] == [check_text(SINGLE)]


# ---------------------------------------------------------------- check: parsing the contract


def test_all_items_pass_with_exit_0_is_passed_with_the_lab_item_texts(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say(pass_lines(MULTI))
    resp = check(client, sid, MULTI)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passed"] is True
    assert body["attempts"] == 1
    assert body["reason"] is None
    assert [i["n"] for i in body["checklist"]] == [1, 2, 3]
    for i, (en, he) in enumerate(ITEMS[MULTI], start=1):
        item = by_n(body)[i]
        assert item["text"] == {"en": en, "he": he}
        assert item["passed"] is True
        assert item["hint"] is None


def test_a_fail_line_with_a_hint_fails_the_item_and_returns_the_hint(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say("PASS 1\nFAIL 2 the file is not readable by group\nPASS 3\n", exit_code=1)
    body = check(client, sid, MULTI).json()
    assert body["passed"] is False
    items = by_n(body)
    assert items[1]["passed"] is True and items[1]["hint"] is None
    assert items[2]["passed"] is False
    assert items[2]["hint"] == "the file is not readable by group"
    assert items[3]["passed"] is True


def test_a_fail_line_without_a_hint_fails_the_item_with_a_null_hint(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say("FAIL 1\nPASS 2\nPASS 3\n", exit_code=1)
    body = check(client, sid, MULTI).json()
    assert body["passed"] is False
    assert by_n(body)[1]["passed"] is False
    assert by_n(body)[1]["hint"] is None
    assert by_n(body)[2]["passed"] is True


def test_an_item_with_no_line_counts_as_failed_even_when_the_exit_code_is_0(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say("PASS 1\nPASS 2\n", exit_code=0)
    body = check(client, sid, MULTI).json()
    assert body["passed"] is False
    assert by_n(body)[3]["passed"] is False
    assert by_n(body)[1]["passed"] is True and by_n(body)[2]["passed"] is True
    assert [i["n"] for i in body["checklist"]] == [1, 2, 3]


def test_a_check_that_prints_nothing_fails_every_item(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say("", exit_code=0)
    body = check(client, sid, MULTI).json()
    assert body["passed"] is False
    assert [i["passed"] for i in body["checklist"]] == [False, False, False]
    assert body["reason"] is None


@pytest.mark.parametrize(
    "output",
    [
        "PASS 1\nPASS 2\nPASS 9\n",  # an item number the lab does not have
        "PASS 1\nPASS 2\nPASS 0\n",
        "PASS 1\nPASS 2\nPASS three\n",
        "PASS 1\nPASS 2\nOK 3\n",
        "PASS 1\nPASS 2\nsomething unrelated\n",
    ],
)
def test_unknown_lines_never_pass_a_missing_item(env, output):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say(output, exit_code=0)
    body = check(client, sid, MULTI).json()
    assert body["passed"] is False
    assert by_n(body)[3]["passed"] is False
    assert len(body["checklist"]) == 3


@pytest.mark.parametrize(
    "output",
    ["PASS 1\nFAIL 1 changed my mind\nPASS 2\nPASS 3\n", "FAIL 1 early\nPASS 1\nPASS 2\nPASS 3\n"],
)
def test_conflicting_lines_for_one_item_count_as_failed(env, output):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say(output, exit_code=0)
    body = check(client, sid, MULTI).json()
    assert body["passed"] is False
    assert by_n(body)[1]["passed"] is False


def test_exit_non_zero_with_every_pass_line_is_not_passed(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say(pass_lines(MULTI), exit_code=1)
    body = check(client, sid, MULTI).json()
    assert body["passed"] is False
    assert body["attempts"] == 1
    assert body["reason"] is None
    assert env.result(sid, MULTI)["passed_at"] is None


def test_pass_lines_with_trailing_noise_and_blank_lines_still_pass(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say("\nPASS 1\n\nPASS 2\nPASS 3\n\n", exit_code=0)
    assert check(client, sid, MULTI).json()["passed"] is True


def test_the_raw_script_output_is_never_echoed_only_the_hint(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say(f"{OUT_MARKER} debug line\nPASS 1\nFAIL 2 try chmod\nPASS 3\n", exit_code=1)
    resp = check(client, sid, MULTI)
    assert resp.status_code == 200
    assert OUT_MARKER not in resp.text
    assert by_n(resp.json())[2]["hint"] == "try chmod"
    assert_not_leaking(resp)


# ---------------------------------------------------------------- check: timeout and runtime errors


def test_a_timed_out_check_is_a_fast_200_failed_with_reason_timeout(env):
    from app.runtime import ExecResult

    client, _, sid = shown_owner(env, MULTI)
    # Whatever it printed before the kill must not count.
    env.runtime.check_outcome = ExecResult(None, pass_lines(MULTI), True)
    started = time.monotonic()
    resp = check(client, sid, MULTI)
    assert time.monotonic() - started < 5
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passed"] is False
    assert body["reason"] == "timeout"
    assert [i["passed"] for i in body["checklist"]] == [False, False, False]
    assert [i["n"] for i in body["checklist"]] == [1, 2, 3]
    assert body["attempts"] == 1
    assert env.result(sid, MULTI)["passed_at"] is None


def test_a_runtime_error_during_check_is_a_200_failed_with_reason_error(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.check_outcome = RuntimeError(f"docker is gone {OUT_MARKER}")
    resp = check(client, sid, MULTI)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passed"] is False
    assert body["reason"] == "error"
    assert [i["passed"] for i in body["checklist"]] == [False, False, False]
    assert OUT_MARKER not in resp.text
    assert env.result(sid, MULTI)["passed_at"] is None


def test_a_check_after_a_timeout_works_normally(env):
    from app.runtime import ExecResult

    client, _, sid = shown_owner(env, SINGLE)
    env.runtime.check_outcome = ExecResult(None, "", True)
    assert check(client, sid, SINGLE).json()["reason"] == "timeout"
    env.runtime.say("PASS 1\n")
    body = check(client, sid, SINGLE).json()
    assert body["passed"] is True and body["reason"] is None and body["attempts"] == 2


# ---------------------------------------------------------------- check: attempts and passed_at


def test_attempts_go_up_by_one_per_check_in_the_response_and_the_database(env):
    client, _, sid = shown_owner(env, MULTI)
    env.runtime.say("FAIL 1\nFAIL 2\nFAIL 3\n", exit_code=1)
    seen = [check(client, sid, MULTI).json()["attempts"] for _ in range(3)]
    assert seen == [1, 2, 3]
    row = env.result(sid, MULTI)
    assert row["attempts"] == 3
    assert row["passed_at"] is None
    assert len(env.runtime.checks()) == 3


def test_a_failed_check_leaves_passed_at_null_and_shown_at_alone(env):
    client, _, sid = shown_owner(env, SINGLE)
    shown = env.result(sid, SINGLE)["shown_at"]
    env.runtime.say("FAIL 1 not yet\n", exit_code=1)
    check(client, sid, SINGLE)
    row = env.result(sid, SINGLE)
    assert row["passed_at"] is None and row["shown_at"] == shown


def test_the_first_pass_sets_passed_at_and_the_task_state(env):
    client, h, sid = shown_owner(env, SINGLE)
    env.runtime.say("PASS 1\n")
    body = check(client, sid, SINGLE).json()
    assert body["passed"] is True and body["attempts"] == 1
    row = env.result(sid, SINGLE)
    assert recent(row["passed_at"]) and row["attempts"] == 1
    assert row["student"] == h and row["lab_id"] == LAB
    listing = client.get(tasks_url(sid)).json()
    assert {t["id"]: t["state"] for t in listing["tasks"]}[SINGLE] == "passed"


def test_passed_at_is_set_on_the_first_pass_only_and_never_cleared(env):
    client, _, sid = shown_owner(env, SINGLE)
    env.runtime.say("FAIL 1\n", exit_code=1)
    check(client, sid, SINGLE)
    env.runtime.say("PASS 1\n")
    assert check(client, sid, SINGLE).json()["passed"] is True
    first = env.result(sid, SINGLE)["passed_at"]
    assert first is not None
    time.sleep(0.05)
    env.runtime.say("PASS 1\n")  # an already passed task is still checked, and counted
    assert check(client, sid, SINGLE).json()["attempts"] == 3
    assert env.result(sid, SINGLE)["passed_at"] == first
    env.runtime.say("FAIL 1 broke it again\n", exit_code=1)
    body = check(client, sid, SINGLE).json()
    assert body["passed"] is False and body["attempts"] == 4
    row = env.result(sid, SINGLE)
    assert row["passed_at"] == first and row["attempts"] == 4
    assert len(env.runtime.checks()) == 4


def test_attempts_are_per_task_and_per_session(env):
    client, _, sid = shown_owner(env, SINGLE)
    show(client, sid, MULTI)
    env.runtime.say("FAIL 1\n", exit_code=1)
    check(client, sid, SINGLE)
    check(client, sid, SINGLE)
    check(client, sid, MULTI)
    assert env.attempts(sid, SINGLE) == 2
    assert env.attempts(sid, MULTI) == 1
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    second = env.open_lab(client)
    show(client, second, SINGLE)
    assert check(client, second, SINGLE).json()["attempts"] == 1
    assert env.attempts(sid, SINGLE) == 2  # the first session's row is untouched


def test_a_stale_pass_in_another_task_does_not_count_for_this_one(env):
    client, _, sid = shown_owner(env, SINGLE)
    show(client, sid, MULTI)
    env.runtime.say("PASS 1\n")
    assert check(client, sid, SINGLE).json()["passed"] is True
    assert env.result(sid, MULTI)["passed_at"] is None


def test_concurrent_checks_do_not_lose_attempts(env):
    import concurrent.futures

    client, _, sid = shown_owner(env, SINGLE)
    env.runtime.say("FAIL 1\n", exit_code=1)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        codes = list(pool.map(lambda _: check(client, sid, SINGLE).status_code, range(4)))
    assert set(codes) <= {200, 429, 409}
    done = codes.count(200)
    assert done >= 1
    assert env.attempts(sid, SINGLE) == done == len(env.runtime.checks())


# ---------------------------------------------------------------- quiz grading


def test_the_right_answer_is_correct_counts_an_attempt_and_sets_passed_at(env):
    client, h, sid = shown_owner(env, QUIZ)
    resp = answer(client, sid, QUIZ, CORRECT)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"correct": True, "attempts": 1}
    row = env.result(sid, QUIZ)
    assert row["attempts"] == 1 and recent(row["passed_at"])
    assert row["student"] == h and row["lab_id"] == LAB
    assert env.runtime.execs == []  # a quiz runs nothing in the container


def test_a_wrong_answer_is_not_correct_and_counts_an_attempt(env):
    client, _, sid = shown_owner(env, QUIZ)
    resp = answer(client, sid, QUIZ, 0)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"correct": False, "attempts": 1}
    row = env.result(sid, QUIZ)
    assert row["attempts"] == 1 and row["passed_at"] is None


def test_wrong_then_right_counts_both_and_sets_passed_at_once(env):
    client, _, sid = shown_owner(env, QUIZ)
    assert answer(client, sid, QUIZ, 2).json() == {"correct": False, "attempts": 1}
    assert answer(client, sid, QUIZ, 0).json() == {"correct": False, "attempts": 2}
    assert answer(client, sid, QUIZ, CORRECT).json() == {"correct": True, "attempts": 3}
    first = env.result(sid, QUIZ)["passed_at"]
    assert first is not None
    assert answer(client, sid, QUIZ, 0).json() == {"correct": False, "attempts": 4}
    assert answer(client, sid, QUIZ, CORRECT).json() == {"correct": True, "attempts": 5}
    row = env.result(sid, QUIZ)
    assert row["passed_at"] == first and row["attempts"] == 5
    assert {t["id"]: t["state"] for t in client.get(tasks_url(sid)).json()["tasks"]}[QUIZ] == (
        "passed"
    )


def test_every_wrong_option_gives_the_same_answer_shape_and_no_hint(env):
    client, _, sid = shown_owner(env, QUIZ)
    wrong = [i for i in range(len(OPTIONS)) if i != CORRECT]
    bodies = [answer(client, sid, QUIZ, i).json() for i in wrong]
    assert all(set(b) == {"correct", "attempts"} for b in bodies), bodies
    assert [b["correct"] for b in bodies] == [False, False]


@pytest.mark.parametrize("option", [3, 4, 99, -1, -100])
def test_an_option_out_of_range_is_422_and_counts_no_attempt(env, option):
    client, _, sid = shown_owner(env, QUIZ)
    resp = answer(client, sid, QUIZ, option)
    assert resp.status_code == 422, resp.text
    assert_not_leaking(resp)
    assert env.attempts(sid, QUIZ) == 0
    row = env.result(sid, QUIZ)
    assert row is None or row["passed_at"] is None


@pytest.mark.parametrize("body", [{}, {"option": "one"}, {"option": None}, {"option": 1.5}])
def test_a_malformed_answer_is_422_and_counts_no_attempt(env, body):
    client, _, sid = shown_owner(env, QUIZ)
    resp = client.post(f"{tasks_url(sid)}/{QUIZ}/answer", json=body)
    assert resp.status_code == 422, resp.text
    assert env.attempts(sid, QUIZ) == 0
    assert_not_leaking(resp)


def test_an_answer_without_a_body_is_422_and_counts_no_attempt(env):
    client, _, sid = shown_owner(env, QUIZ)
    resp = client.post(f"{tasks_url(sid)}/{QUIZ}/answer")
    assert resp.status_code == 422, resp.text
    assert env.attempts(sid, QUIZ) == 0


def test_the_correct_option_is_in_no_response_of_any_route(env):
    client, _, sid = env.owner()
    sessions = [client.get("/api/sessions/current")]
    responses = [client.get(tasks_url(sid))]
    responses.append(client.get(f"{tasks_url(sid)}/current"))
    for tid in (QUIZ, MULTI, SINGLE):
        responses.append(client.post(f"{tasks_url(sid)}/{tid}/show"))
        responses.append(client.get(f"{tasks_url(sid)}/current"))
        responses.append(client.get(tasks_url(sid)))
    for action in ("previous", "next", "skip", "restart"):
        responses.append(client.post(f"{tasks_url(sid)}/current/{action}"))
    env.runtime.say("PASS 1\nFAIL 2 x\n", exit_code=1)
    responses.append(check(client, sid, MULTI))
    env.runtime.check_outcome = RuntimeError("boom")
    responses.append(check(client, sid, MULTI))
    responses.append(check(client, sid, "no-such-task"))
    responses.append(check(client, sid, QUIZ))  # wrong kind
    responses.append(answer(client, sid, MULTI, 1))  # wrong kind
    responses.append(answer(client, sid, QUIZ, 77))  # out of range
    responses.append(client.post(f"{tasks_url(sid)}/{QUIZ}/answer", json={"option": "x"}))
    responses.append(answer(client, sid, "09-other", 0))
    responses.append(client.post(f"/api/sessions/{sid}/heartbeat"))
    assert sum(r.status_code == 200 for r in responses) >= 10
    for resp in responses + sessions:
        assert_not_leaking(resp)
    # Answer responses carry the key `correct` by design, but only the grade, never the index.
    graded = [answer(client, sid, QUIZ, i) for i in (0, 1, 2)]
    for resp in graded:
        assert resp.status_code == 200
        assert set(resp.json()) == {"correct", "attempts"}
        assert_not_leaking(resp, allow_correct_key=True)
        assert "index" not in resp.text and "answer" not in resp.text.lower()


def test_the_lab_content_files_are_not_served_to_the_student(env):
    client, _, sid = shown_owner(env, QUIZ)
    for resp in (
        client.get(f"{tasks_url(sid)}/{QUIZ}"),
        client.get(f"{tasks_url(sid)}/{QUIZ}/check"),
        client.get(f"{tasks_url(sid)}/{QUIZ}/answer"),
    ):
        assert resp.status_code in (404, 405), resp.text
        assert_not_leaking(resp)


# ---------------------------------------------------------------- kind, shown, lab, session rules


def test_check_on_a_quiz_is_409_wrong_task_kind_and_counts_nothing(env):
    client, _, sid = shown_owner(env, QUIZ)
    resp = check(client, sid, QUIZ)
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "wrong_task_kind"}
    assert env.runtime.execs == []
    assert env.attempts(sid, QUIZ) == 0


def test_answer_on_a_hands_on_task_is_409_wrong_task_kind_and_counts_nothing(env):
    client, _, sid = shown_owner(env, MULTI)
    resp = answer(client, sid, MULTI, 0)
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "wrong_task_kind"}
    assert env.runtime.execs == []
    assert env.attempts(sid, MULTI) == 0


def test_check_of_a_task_never_shown_is_409_task_not_shown_and_runs_nothing(env):
    client, _, sid = env.owner()
    resp = check(client, sid, MULTI)
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "task_not_shown"}
    assert env.runtime.execs == []
    row = env.result(sid, MULTI)
    assert row is None or (row["attempts"] == 0 and row["passed_at"] is None)


def test_answer_to_a_quiz_never_shown_is_409_task_not_shown_and_counts_nothing(env):
    client, _, sid = env.owner()
    resp = answer(client, sid, QUIZ, CORRECT)
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "task_not_shown"}
    row = env.result(sid, QUIZ)
    assert row is None or (row["attempts"] == 0 and row["passed_at"] is None)


def test_a_task_whose_setup_failed_was_never_shown_so_it_cannot_be_checked(env):
    from app.runtime import ExecResult

    client, _, sid = env.owner()
    env.runtime.setup_outcome = ExecResult(1, "", False)
    assert client.post(f"{tasks_url(sid)}/{MULTI}/show").status_code == 502
    env.runtime.setup_outcome = None
    env.runtime.execs.clear()
    assert check(client, sid, MULTI).status_code == 409
    assert env.runtime.execs == []


@pytest.mark.parametrize("route", ["check", "answer"])
@pytest.mark.parametrize("tid", ["09-other", "no-such-task", "02-MULTI", "0"])
def test_a_task_not_of_the_sessions_lab_is_404_unknown_task(env, route, tid):
    client, _, sid = env.owner()
    resp = client.post(f"{tasks_url(sid)}/{tid}/{route}", json={"option": 0})
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "unknown_task"}
    assert env.runtime.execs == []
    assert env.result(sid, tid) is None


ROUTES = [
    ("check", None, MULTI),
    ("answer", {"option": CORRECT}, QUIZ),
]


@pytest.mark.parametrize("route,body,tid", ROUTES)
def test_anonymous_callers_get_401_and_nothing_runs_or_counts(env, route, body, tid):
    _, _, sid = shown_owner(env, tid)
    resp = env.new_client().post(f"{tasks_url(sid)}/{tid}/{route}", json=body)
    assert resp.status_code == 401, resp.text
    assert env.runtime.execs == []
    assert env.attempts(sid, tid) == 0


@pytest.mark.parametrize("route,body,tid", ROUTES)
def test_another_students_session_is_403_and_nothing_runs_or_counts(env, route, body, tid):
    owner, _, sid = shown_owner(env, tid)
    stranger, _ = env.student()
    resp = stranger.post(f"{tasks_url(sid)}/{tid}/{route}", json=body)
    assert resp.status_code == 403, resp.text
    assert env.runtime.execs == []
    assert env.attempts(sid, tid) == 0
    assert_not_leaking(resp)


@pytest.mark.parametrize("route,body,tid", ROUTES)
def test_an_ended_session_is_410_and_nothing_runs_or_counts(env, route, body, tid):
    client, _, sid = shown_owner(env, tid)
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    resp = client.post(f"{tasks_url(sid)}/{tid}/{route}", json=body)
    assert resp.status_code == 410, resp.text
    assert env.runtime.execs == []
    assert env.attempts(sid, tid) == 0


@pytest.mark.parametrize("route,body,tid", ROUTES)
def test_a_session_ended_by_the_system_is_410_too(env, route, body, tid):
    client, _, sid = shown_owner(env, tid)
    env.end_in_db(sid)
    resp = client.post(f"{tasks_url(sid)}/{tid}/{route}", json=body)
    assert resp.status_code == 410, resp.text
    assert env.runtime.execs == []


@pytest.mark.parametrize("route,body,tid", ROUTES)
def test_an_unknown_session_is_404_no_session(env, route, body, tid):
    client, _ = env.student()
    for sid in (str(uuid.uuid4()), "not-a-uuid"):
        resp = client.post(f"{tasks_url(sid)}/{tid}/{route}", json=body)
        assert resp.status_code == 404, resp.text
        assert resp.json() == {"detail": "no_session"}
    assert env.runtime.execs == []


@pytest.mark.parametrize("route,body,tid", ROUTES)
def test_the_session_checks_come_before_the_task_checks(env, route, body, tid):
    client, _, sid = env.owner()
    stranger, _ = env.student()
    url = f"{tasks_url(sid)}/no-such-task/{route}"
    assert stranger.post(url, json=body).status_code == 403
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    assert client.post(url, json=body).status_code == 410
