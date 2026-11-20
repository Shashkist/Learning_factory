"""Acceptance tests for issue #89: lab completion, active time and the fast flag (slice 3 of D37).

Spec section 8 (data model, fast-work flag), decisions D37, D50, D51. Same harness as
test_issue_88_check_results.py (CI Postgres, fake runtime, fake students). The teacher UI (#11)
reads everything below straight from the database, so the storage is part of the contract.

CONTRACT chosen by this exam (the spec and the migrations leave these open):

Storage. A new migration adds, besides the existing columns of section 8:
  lab_sessions.active_seconds double precision not null default 0   running total of heartbeat time
  task_results.fast_reason    text null                    why the task was flagged
  lab_runs.fast_reason        text null                    why the lab run was flagged
`fast_reason` is a non-empty human sentence (for example "task 04-x passed 9 s after it
appeared") exactly when the matching `fast_flag` is true, and null otherwise.

Active time. Every accepted heartbeat (POST /api/sessions/{sid}/heartbeat, 204) adds the time
since the previous heartbeat (`last_heartbeat`) to `lab_sessions.active_seconds`, but only when
that gap is not longer than the heartbeat timeout (LF_HEARTBEAT_TIMEOUT_S, read per request,
default 300 s); a longer gap adds nothing. The exam backdates `last_heartbeat` in the database
to simulate time passing.

Completion. When the last task of the lab passes in a session (every task of the lab has a
`passed_at` in that session's `task_results`, by check or by quiz answer), the manager writes,
once: a `lab_completions` row (first time only, `completed_at` never changed afterwards) and
the session's `lab_runs` row with `first_completion` true only for the student's first
completion of that lab, `active_seconds` copied from the session and the lab `fast_flag`.
A session that has not completed everything has no `lab_runs` row and no completion.
Tasks passed in an earlier session never count for a later one.

lab.yaml keys (both optional): per task `min_task_seconds` (seconds, default 20), and at the
lab level `fast_lab_min` (MINUTES, like `time_limit_min`; absent means no absolute limit).

Task flag. At the moment a hands-on task first passes, `task_results.fast_flag` is true when
`passed_at - shown_at` is strictly less than the task's `min_task_seconds`. Quiz tasks are
never flagged. Tasks passed in a session of a lab the student already completed are not flagged.

Lab flag. Decided when the lab completes, only for the student's first completion: true when
`active_seconds` is strictly below `fast_lab_min` * 60, or when at least 5 OTHER students had
already completed the lab and `active_seconds` is strictly below 30 % of the median of those
students' first-completion `active_seconds` (their `lab_runs` rows). Repeat completions: false.

Pure functions in a new module `app.flags` (no database), used by the above:
  is_fast_task(kind, seconds, min_task_seconds=20, repeat=False) -> bool
  is_fast_lab(active_seconds, fast_lab_min=None, prior_active_seconds=(), repeat=False) -> bool
      (`fast_lab_min` in minutes or None; `prior_active_seconds` the first-completion values
       of the students who completed the lab before this one)
  covered_seconds(beats, timeout=300) -> float
      (`beats` are timestamps in seconds, any order; the sum of the gaps between consecutive
       beats that are not longer than `timeout`)

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and skip
locally when it is absent (CI sets it and fails on any skip). Student ids are fake. Docker is
never used here.
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

# ---------------------------------------------------------------- the fake labs

CORRECT = 1
QUIZ = "01-quiz"
PLAIN = "02-plain"
SHORT = "03-short"  # min_task_seconds: 5 where it is set
LONG = "04-long"  # min_task_seconds: 120 where it is set


def _task_yaml(tid: str, kind: str, min_task_seconds: int | None = None) -> str:
    if kind == "quiz":
        return (
            f"  - id: {tid}\n    kind: quiz\n    question:\n      en: Q {tid}?\n"
            "      he: מה?\n    options:\n"
            "      - en: A\n        he: א\n      - en: B\n        he: ב\n"
            f"    correct: {CORRECT}\n"
        )
    extra = f"    min_task_seconds: {min_task_seconds}\n" if min_task_seconds is not None else ""
    return (
        f"  - id: {tid}\n    kind: hands-on\n{extra}    checklist:\n"
        "      - en: Do it\n        he: לעשות\n"
    )


def lab_yaml(lab_id: str, tasks: list[tuple], fast_lab_min: int | None = None) -> str:
    lab_extra = f"fast_lab_min: {fast_lab_min}\n" if fast_lab_min is not None else ""
    return (
        f"id: {lab_id}\ntitle:\n  en: Lab {lab_id}\n  he: מעבדה {lab_id}\n"
        "description:\n  en: Fake lab.\n  he: מעבדה מזויפת.\nimage: term\ntime_limit_min: 15\n"
        "internet: false\nallow_domains: []\nsudo: false\nstatus: published\nmodule: 1\n"
        f"lesson: 6\n{lab_extra}tasks:\n" + "".join(_task_yaml(*t) for t in tasks)
    )


# lab id -> (tasks, fast_lab_min in minutes); every hands-on task has one checklist item
LABS: dict[str, tuple[list[tuple], int | None]] = {
    # completion, repeats, active time; no absolute fast limit
    "comp": ([(QUIZ, "quiz"), (PLAIN, "hands-on"), (SHORT, "hands-on")], None),
    "comp2": ([(PLAIN, "hands-on"), (SHORT, "hands-on")], None),
    "comp3": ([(PLAIN, "hands-on")], None),
    # a run that outlives the janitor's startup retry (#284)
    "slow": ([(PLAIN, "hands-on"), (SHORT, "hands-on")], None),
    # per-task overrides, quiz, and an absolute lab limit of 10 minutes
    "speedy": (
        [(QUIZ, "quiz"), (PLAIN, "hands-on"), (SHORT, "hands-on", 5), (LONG, "hands-on", 120)],
        10,
    ),
    "speedy2": ([(PLAIN, "hands-on")], 10),
    "speedy3": ([(PLAIN, "hands-on")], 10),
    "speedy4": ([(PLAIN, "hands-on")], 10),
    # class median rule (no absolute limit, except the last one: 1 minute)
    "median5": ([(PLAIN, "hands-on")], None),
    "median3": ([(PLAIN, "hands-on")], None),
    "median5b": ([(PLAIN, "hands-on")], 1),
    "median5c": ([(PLAIN, "hands-on")], None),
}


def write_content(root: Path) -> Path:
    for lab_id, (tasks, fast_min) in LABS.items():
        d = root / "test" / lab_id
        d.mkdir(parents=True)
        (d / "lab.yaml").write_text(lab_yaml(lab_id, tasks, fast_min), encoding="utf-8")
        for t in tasks:
            tid = t[0]
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


# ---------------------------------------------------------------- fake runtime


class FakeRuntime:
    """Starts and removes nothing for real. Setups succeed; every check prints `PASS 1` and
    exits 0 unless `passing` is switched off (then it prints `FAIL 1`, exit 1)."""

    def __init__(self) -> None:
        self.passing = True

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
    return f"10.189.{n // 250}.{n % 250 + 1}"


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

        # Own id range (D47): #6 uses 700000000+, #9 790000000+, #88 880000000+, this 890000000+.
        raw = str(890000000 + next(_n))
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

    def open_lab(self, client, lab_id: str) -> str:
        resp = client.post("/api/sessions", json={"lab_id": lab_id})
        assert resp.status_code in (200, 201), resp.text
        return resp.json()["id"]

    # --- direct database access (the teacher UI reads the same rows)

    def rows(self, query: str, *args) -> list[dict]:
        async def go(conn):
            return [dict(r) for r in await conn.fetch(query, *args)]

        return self.sql(go)

    def execute(self, query: str, *args) -> None:
        async def go(conn):
            await conn.execute(query, *args)

        self.sql(go)

    def task(self, sid: str, tid: str) -> dict | None:
        r = self.rows(
            f"select * from {SCHEMA}.task_results where session_id = $1 and task_id = $2",
            uuid.UUID(sid), tid,
        )
        return r[0] if r else None

    def session(self, sid: str) -> dict:
        return self.rows(f"select * from {SCHEMA}.lab_sessions where id = $1", uuid.UUID(sid))[0]

    def runs(self, student: str, lab: str) -> list[dict]:
        return self.rows(
            f"select * from {SCHEMA}.lab_runs where student = $1 and lab_id = $2 "
            "order by session_id", student, lab,
        )

    def run(self, sid: str) -> dict | None:
        r = self.rows(f"select * from {SCHEMA}.lab_runs where session_id = $1", uuid.UUID(sid))
        return r[0] if r else None

    def completions(self, student: str, lab: str) -> list[dict]:
        return self.rows(
            f"select * from {SCHEMA}.lab_completions where student = $1 and lab_id = $2",
            student, lab,
        )

    def set_active(self, sid: str, seconds: int) -> None:
        self.execute(
            f"update {SCHEMA}.lab_sessions set active_seconds = $2 where id = $1",
            uuid.UUID(sid), seconds,
        )

    def age_shown(self, sid: str, tid: str, seconds: float) -> None:
        """Pretend the task appeared `seconds` ago (relative to now)."""
        self.execute(
            f"update {SCHEMA}.task_results set shown_at = now() - make_interval(secs => $3) "
            "where session_id = $1 and task_id = $2",
            uuid.UUID(sid), tid, float(seconds),
        )

    def beat_back(self, sid: str, seconds: float) -> None:
        """Pretend the previous heartbeat was `seconds` ago."""
        self.execute(
            f"update {SCHEMA}.lab_sessions set last_heartbeat = now() - make_interval(secs => $2),"
            " started_at = now() - make_interval(secs => $2 + 1000) where id = $1",
            uuid.UUID(sid), float(seconds),
        )


@pytest.fixture
def env(_db, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.janitor as janitor

    async def idle(_app):
        return True

    # FakeRuntime is swapped in after the app has started. The janitor's startup pass fails on
    # the missing Docker socket and retries 2 s later; FakeRuntime lists no containers, so that
    # retry would end every running session as an orphan (#284). No case here tests the janitor.
    monkeypatch.setattr(janitor, "run_once", idle)

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
        e.started = time.monotonic()  # after startup, so the elapsed time is never overstated
        e.sql(end_all)
        yield e
        e.sql(end_all)


# ---------------------------------------------------------------- helpers


def tasks_url(sid: str) -> str:
    return f"/api/sessions/{sid}/tasks"


def task_ids(lab: str) -> list[str]:
    return [t[0] for t in LABS[lab][0]]


def task_kind(lab: str, tid: str) -> str:
    return next(t[1] for t in LABS[lab][0] if t[0] == tid)


def show(client, sid: str, tid: str) -> None:
    resp = client.post(f"{tasks_url(sid)}/{tid}/show")
    assert resp.status_code == 200, resp.text


def pass_task(env, client, sid: str, lab: str, tid: str, age: float | None = 300) -> None:
    """Show the task, pretend it appeared `age` seconds ago (None: leave it), then pass it."""
    show(client, sid, tid)
    if age is not None:
        env.age_shown(sid, tid, age)
    if task_kind(lab, tid) == "quiz":
        resp = client.post(f"{tasks_url(sid)}/{tid}/answer", json={"option": CORRECT})
        assert resp.status_code == 200 and resp.json()["correct"] is True, resp.text
    else:
        env.runtime.passing = True
        resp = client.post(f"{tasks_url(sid)}/{tid}/check")
        assert resp.status_code == 200 and resp.json()["passed"] is True, resp.text


def complete(env, client, sid: str, lab: str, age: float | None = 300,
             ages: dict[str, float] | None = None, active: int | None = None) -> None:
    """Pass every task of the lab in this session. `active` sets the session's active seconds
    first (simulating time spent)."""
    if active is not None:
        env.set_active(sid, active)
    for tid in task_ids(lab):
        pass_task(env, client, sid, lab, tid, (ages or {}).get(tid, age))


def beat(client, sid: str) -> None:
    resp = client.post(f"/api/sessions/{sid}/heartbeat")
    assert resp.status_code == 204, resp.text


def fresh_run(env, lab: str, active: int | None = None, age: float | None = 300,
              ages: dict[str, float] | None = None):
    """A new student completes `lab` for the first time. Returns (client, hash, sid)."""
    client, h = env.student()
    sid = env.open_lab(client, lab)
    complete(env, client, sid, lab, age=age, ages=ages, active=active)
    return client, h, sid


def recent(ts) -> bool:
    return ts is not None and abs((dt.datetime.now(dt.UTC) - ts).total_seconds()) < 120


# ================================================================ pure functions (no database)


def test_a_hands_on_task_passed_in_under_min_task_seconds_is_fast():
    from app.flags import is_fast_task

    assert is_fast_task("hands-on", 0) is True
    assert is_fast_task("hands-on", 19.9) is True


def test_the_default_min_task_seconds_is_20_and_the_limit_is_strict():
    from app.flags import is_fast_task

    assert is_fast_task("hands-on", 20) is False
    assert is_fast_task("hands-on", 20.1) is False
    assert is_fast_task("hands-on", 300) is False


def test_a_per_task_min_task_seconds_replaces_the_default():
    from app.flags import is_fast_task

    assert is_fast_task("hands-on", 10, min_task_seconds=5) is False  # default would flag
    assert is_fast_task("hands-on", 4.9, min_task_seconds=5) is True
    assert is_fast_task("hands-on", 60, min_task_seconds=120) is True  # default would not
    assert is_fast_task("hands-on", 120, min_task_seconds=120) is False


def test_a_quiz_is_never_fast_for_speed_alone():
    from app.flags import is_fast_task

    assert is_fast_task("quiz", 0) is False
    assert is_fast_task("quiz", 0.5, min_task_seconds=600) is False


def test_a_repeat_completion_is_never_fast_for_a_task():
    from app.flags import is_fast_task

    assert is_fast_task("hands-on", 0, repeat=True) is False


def test_a_lab_below_fast_lab_min_minutes_is_fast_and_the_limit_is_strict():
    from app.flags import is_fast_lab

    assert is_fast_lab(0, fast_lab_min=10) is True
    assert is_fast_lab(599, fast_lab_min=10) is True
    assert is_fast_lab(600, fast_lab_min=10) is False
    assert is_fast_lab(6000, fast_lab_min=10) is False


def test_without_fast_lab_min_and_with_few_priors_a_lab_is_never_fast():
    from app.flags import is_fast_lab

    assert is_fast_lab(0) is False
    assert is_fast_lab(0, None, []) is False
    assert is_fast_lab(0, None, [1000, 1000, 1000, 1000]) is False  # only 4 before


def test_with_five_priors_a_lab_below_30_percent_of_their_median_is_fast():
    from app.flags import is_fast_lab

    priors = [100, 100, 1000, 1000, 1000]  # median 1000, mean 640: tells them apart
    assert is_fast_lab(250, None, priors) is True
    assert is_fast_lab(299, None, priors) is True
    assert is_fast_lab(300, None, priors) is False
    assert is_fast_lab(350, None, priors) is False


def test_the_median_of_an_even_number_of_priors_is_the_mean_of_the_middle_two():
    from app.flags import is_fast_lab

    priors = [100, 200, 1000, 2000, 3000, 4000]  # median 1500, 30 % is 450
    assert is_fast_lab(449, None, priors) is True
    assert is_fast_lab(451, None, priors) is False


def test_the_absolute_and_the_median_rules_are_alternatives():
    from app.flags import is_fast_lab

    priors = [100] * 5  # 30 % of the median is 30 s
    assert is_fast_lab(500, 10, priors) is True  # under 10 min
    assert is_fast_lab(20, 0, priors) is True  # under 30 % of the median
    assert is_fast_lab(700, 10, priors) is False  # neither


def test_a_repeat_completion_is_never_fast_for_a_lab():
    from app.flags import is_fast_lab

    assert is_fast_lab(0, 10, [1000] * 6, repeat=True) is False


def test_covered_seconds_sums_the_gaps_between_heartbeats():
    from app.flags import covered_seconds

    assert covered_seconds([0, 15, 30, 45]) == 45
    assert covered_seconds([20, 0, 10]) == 20  # any order


def test_a_gap_longer_than_the_timeout_does_not_count():
    from app.flags import covered_seconds

    assert covered_seconds([0, 10, 400, 410]) == 20  # the 390 s gap is dropped
    assert covered_seconds([0, 301]) == 0
    assert covered_seconds([0, 300]) == 300  # equal to the timeout still counts
    assert covered_seconds([0, 70, 100], timeout=60) == 30


def test_no_heartbeat_or_one_heartbeat_covers_nothing():
    from app.flags import covered_seconds

    assert covered_seconds([]) == 0
    assert covered_seconds([123]) == 0


# ================================================================ active time (heartbeats)


def test_a_heartbeat_adds_the_time_since_the_previous_one(env):
    client, _ = env.student()
    sid = env.open_lab(client, "comp")
    env.beat_back(sid, 60)
    env.set_active(sid, 0)
    beat(client, sid)
    assert 58 <= env.session(sid)["active_seconds"] <= 64


def test_a_gap_longer_than_the_heartbeat_timeout_adds_nothing(env):
    client, _ = env.student()
    sid = env.open_lab(client, "comp")
    env.set_active(sid, 0)
    env.beat_back(sid, 400)  # default timeout is 300 s
    beat(client, sid)
    assert env.session(sid)["active_seconds"] <= 2
    assert recent(env.session(sid)["last_heartbeat"])  # the session still counts as alive


def test_active_seconds_is_summed_over_the_session_and_skips_gaps(env):
    client, _ = env.student()
    sid = env.open_lab(client, "comp")
    env.set_active(sid, 0)
    env.beat_back(sid, 60)
    beat(client, sid)
    env.beat_back(sid, 90)
    beat(client, sid)
    summed = env.session(sid)["active_seconds"]
    assert 148 <= summed <= 156
    env.beat_back(sid, 400)  # a long gap in between
    beat(client, sid)
    assert summed <= env.session(sid)["active_seconds"] <= summed + 2
    env.beat_back(sid, 30)
    beat(client, sid)
    assert summed + 28 <= env.session(sid)["active_seconds"] <= summed + 34


def test_two_quick_heartbeats_do_not_count_the_same_time_twice(env):
    client, _ = env.student()
    sid = env.open_lab(client, "comp")
    env.set_active(sid, 0)
    env.beat_back(sid, 50)
    beat(client, sid)
    first = env.session(sid)["active_seconds"]
    beat(client, sid)
    beat(client, sid)
    assert first <= env.session(sid)["active_seconds"] <= first + 2


def test_the_heartbeat_timeout_setting_decides_what_a_gap_is(env, monkeypatch):
    monkeypatch.setenv("LF_HEARTBEAT_TIMEOUT_S", "30")
    client, _ = env.student()
    sid = env.open_lab(client, "comp")
    env.set_active(sid, 0)
    env.beat_back(sid, 60)
    beat(client, sid)
    assert env.session(sid)["active_seconds"] <= 2  # 60 s is a gap with a 30 s timeout
    env.beat_back(sid, 20)
    beat(client, sid)
    assert 18 <= env.session(sid)["active_seconds"] <= 24


def test_lab_runs_active_seconds_is_the_session_total_from_heartbeats(env):
    client, _ = env.student()
    sid = env.open_lab(client, "comp3")
    env.set_active(sid, 0)
    env.beat_back(sid, 100)
    beat(client, sid)
    env.beat_back(sid, 400)  # not counted
    beat(client, sid)
    total = env.session(sid)["active_seconds"]
    assert 98 <= total <= 106
    complete(env, client, sid, "comp3")
    run = env.run(sid)
    assert run is not None
    assert total <= run["active_seconds"] <= total + 5
    assert run["active_seconds"] < 150  # the 400 s gap is not in it


def test_lab_runs_active_seconds_copies_the_sessions_value(env):
    _, h, sid = fresh_run(env, "comp3", active=777)
    assert env.run(sid)["active_seconds"] == 777
    assert env.session(sid)["active_seconds"] >= 777


# ================================================================ lab completion


def test_passing_every_task_records_a_first_completion_and_the_lab_run(env):
    client, h, sid = fresh_run(env, "comp", active=1500)
    completions = env.completions(h, "comp")
    assert len(completions) == 1
    assert recent(completions[0]["completed_at"])
    run = env.run(sid)
    assert run is not None
    assert run["student"] == h and run["lab_id"] == "comp"
    assert run["first_completion"] is True
    assert run["active_seconds"] == 1500
    assert len(env.runs(h, "comp")) == 1


def test_a_lab_with_tasks_still_open_is_not_completed(env):
    client, h = env.student()
    sid = env.open_lab(client, "comp")
    pass_task(env, client, sid, "comp", QUIZ)
    pass_task(env, client, sid, "comp", PLAIN)
    assert env.completions(h, "comp") == []
    run = env.run(sid)
    assert run is None or run["first_completion"] is False
    assert not any(r["first_completion"] for r in env.runs(h, "comp"))


def test_a_task_that_was_shown_but_not_passed_does_not_complete_the_lab(env):
    client, h = env.student()
    sid = env.open_lab(client, "comp2")
    pass_task(env, client, sid, "comp2", PLAIN)
    show(client, sid, SHORT)
    env.runtime.passing = False
    resp = client.post(f"{tasks_url(sid)}/{SHORT}/check")
    assert resp.status_code == 200 and resp.json()["passed"] is False
    assert env.completions(h, "comp2") == []
    assert env.run(sid) is None or env.run(sid)["first_completion"] is False


def test_the_last_task_passing_triggers_completion_whatever_the_order(env):
    client, h = env.student()
    sid = env.open_lab(client, "comp")
    for tid in (SHORT, PLAIN):  # hands-on first, the quiz last
        pass_task(env, client, sid, "comp", tid)
    assert env.completions(h, "comp") == []
    pass_task(env, client, sid, "comp", QUIZ)
    assert len(env.completions(h, "comp")) == 1
    assert env.run(sid)["first_completion"] is True


def test_tasks_passed_in_an_earlier_session_do_not_count_for_a_later_one(env):
    client, h = env.student()
    first = env.open_lab(client, "comp2")
    pass_task(env, client, first, "comp2", PLAIN)
    assert client.post(f"/api/sessions/{first}/end").status_code == 204
    second = env.open_lab(client, "comp2")
    pass_task(env, client, second, "comp2", SHORT)
    assert env.completions(h, "comp2") == []
    assert env.run(first) is None or env.run(first)["first_completion"] is False
    assert env.run(second) is None or env.run(second)["first_completion"] is False
    pass_task(env, client, second, "comp2", PLAIN)  # now the second session has both
    assert len(env.completions(h, "comp2")) == 1
    assert env.run(second)["first_completion"] is True
    assert env.run(first) is None


def test_a_repeat_run_is_recorded_with_first_completion_false_and_completes_nothing_new(env):
    client, h, first = fresh_run(env, "comp2", active=900)
    done = env.completions(h, "comp2")
    assert len(done) == 1
    assert client.post(f"/api/sessions/{first}/end").status_code == 204
    second = env.open_lab(client, "comp2")
    complete(env, client, second, "comp2", active=400)
    runs = env.runs(h, "comp2")
    assert len(runs) == 2
    again = env.run(second)
    assert again["first_completion"] is False
    assert again["active_seconds"] == 400
    assert env.run(first)["first_completion"] is True
    assert env.run(first)["active_seconds"] == 900
    assert env.completions(h, "comp2") == done  # one row, completed_at untouched


def test_a_third_run_is_a_repeat_too(env):
    client, h, sid = fresh_run(env, "comp3")
    for _ in range(2):
        assert client.post(f"/api/sessions/{sid}/end").status_code == 204
        sid = env.open_lab(client, "comp3")
        complete(env, client, sid, "comp3")
    runs = env.runs(h, "comp3")
    assert len(runs) == 3
    assert sum(r["first_completion"] for r in runs) == 1
    assert len(env.completions(h, "comp3")) == 1


def test_first_completion_is_per_student_and_per_lab(env):
    _, a, sa = fresh_run(env, "comp3")
    _, b, sb = fresh_run(env, "comp3")
    assert env.run(sa)["first_completion"] is True
    assert env.run(sb)["first_completion"] is True
    client, c = env.student()
    other = env.open_lab(client, "comp2")
    complete(env, client, other, "comp2")
    assert env.run(other)["first_completion"] is True
    assert env.completions(c, "comp3") == []


def test_a_student_who_abandoned_a_run_gets_first_completion_on_the_run_that_completes(env):
    client, h = env.student()
    abandoned = env.open_lab(client, "comp2")
    pass_task(env, client, abandoned, "comp2", PLAIN)
    assert client.post(f"/api/sessions/{abandoned}/end").status_code == 204
    finished = env.open_lab(client, "comp2")
    complete(env, client, finished, "comp2")
    assert env.run(finished)["first_completion"] is True
    assert env.run(abandoned) is None or env.run(abandoned)["first_completion"] is False
    assert len(env.completions(h, "comp2")) == 1


def test_checking_a_finished_lab_again_changes_nothing(env):
    client, h, sid = fresh_run(env, "comp", active=1234)
    before_run = env.run(sid)
    before_done = env.completions(h, "comp")
    env.runtime.passing = True
    assert client.post(f"{tasks_url(sid)}/{PLAIN}/check").status_code == 200
    answer = client.post(f"{tasks_url(sid)}/{QUIZ}/answer", json={"option": CORRECT})
    assert answer.status_code == 200
    env.runtime.passing = False
    assert client.post(f"{tasks_url(sid)}/{PLAIN}/check").status_code == 200
    assert env.run(sid) == before_run
    assert env.completions(h, "comp") == before_done
    assert len(env.runs(h, "comp")) == 1


def test_completing_one_lab_does_not_complete_another(env):
    client, h = env.student()
    sid = env.open_lab(client, "comp3")
    complete(env, client, sid, "comp3")
    assert len(env.completions(h, "comp3")) == 1
    assert env.completions(h, "comp") == [] and env.completions(h, "comp2") == []


# ================================================================ the task flag


def test_a_hands_on_task_passed_right_after_it_appeared_is_flagged_with_a_reason(env):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    pass_task(env, client, sid, "speedy", PLAIN, age=None)
    row = env.task(sid, PLAIN)
    assert (row["passed_at"] - row["shown_at"]).total_seconds() < 20
    assert row["fast_flag"] is True
    assert isinstance(row["fast_reason"], str) and row["fast_reason"].strip()


def test_a_hands_on_task_passed_after_the_default_20s_is_not_flagged(env):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    pass_task(env, client, sid, "speedy", PLAIN, age=60)
    row = env.task(sid, PLAIN)
    assert row["fast_flag"] is False and row["fast_reason"] is None


@pytest.mark.parametrize("age,flagged", [(10, True), (40, False)])
def test_the_default_limit_separates_10_seconds_from_40_seconds(env, age, flagged):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    pass_task(env, client, sid, "speedy", PLAIN, age=age)
    assert env.task(sid, PLAIN)["fast_flag"] is flagged


def test_a_lab_yaml_min_task_seconds_lowers_the_limit_for_that_task(env):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    pass_task(env, client, sid, "speedy", SHORT, age=10)  # limit 5: not fast; default 20: fast
    assert env.task(sid, SHORT)["fast_flag"] is False
    pass_task(env, client, sid, "speedy", PLAIN, age=10)  # same time, default limit: fast
    assert env.task(sid, PLAIN)["fast_flag"] is True


def test_a_lab_yaml_min_task_seconds_raises_the_limit_for_that_task(env):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    pass_task(env, client, sid, "speedy", LONG, age=60)  # limit 120: fast; default 20: not
    row = env.task(sid, LONG)
    assert row["fast_flag"] is True and row["fast_reason"]
    pass_task(env, client, sid, "speedy", PLAIN, age=60)
    assert env.task(sid, PLAIN)["fast_flag"] is False


def test_a_quiz_answered_at_once_is_never_flagged_for_speed(env):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    pass_task(env, client, sid, "speedy", QUIZ, age=None)
    row = env.task(sid, QUIZ)
    assert row["passed_at"] is not None
    assert row["fast_flag"] is False and row["fast_reason"] is None


def test_a_failed_check_flags_nothing(env):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    show(client, sid, PLAIN)
    env.runtime.passing = False
    assert client.post(f"{tasks_url(sid)}/{PLAIN}/check").status_code == 200
    row = env.task(sid, PLAIN)
    assert row["passed_at"] is None
    assert row["fast_flag"] is False and row["fast_reason"] is None


def test_the_flag_judges_the_first_pass_a_late_pass_after_quick_fails_is_not_fast(env):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    show(client, sid, PLAIN)
    env.runtime.passing = False
    for _ in range(3):
        client.post(f"{tasks_url(sid)}/{PLAIN}/check")
    env.age_shown(sid, PLAIN, 90)
    env.runtime.passing = True
    assert client.post(f"{tasks_url(sid)}/{PLAIN}/check").json()["passed"] is True
    assert env.task(sid, PLAIN)["fast_flag"] is False


def test_the_task_flag_never_reaches_the_student(env):
    client, _ = env.student()
    sid = env.open_lab(client, "speedy")
    show(client, sid, PLAIN)
    env.runtime.passing = True
    resp = client.post(f"{tasks_url(sid)}/{PLAIN}/check")
    listing = client.get(tasks_url(sid))
    for r in (resp, listing, client.get("/api/sessions/current")):
        assert "fast" not in r.text.lower(), r.text[:300]


def test_tasks_passed_in_a_repeat_run_are_not_flagged(env):
    client, h, first = fresh_run(env, "speedy2", active=900)
    assert client.post(f"/api/sessions/{first}/end").status_code == 204
    second = env.open_lab(client, "speedy2")
    pass_task(env, client, second, "speedy2", PLAIN, age=None)  # instantly
    row = env.task(second, PLAIN)
    assert row["passed_at"] is not None
    assert row["fast_flag"] is False and row["fast_reason"] is None


# ================================================================ the lab flag


def test_a_first_completion_below_fast_lab_min_is_flagged_with_a_reason(env):
    _, h, sid = fresh_run(env, "speedy2", active=300)  # lab says 10 min = 600 s
    run = env.run(sid)
    assert run["first_completion"] is True
    assert run["fast_flag"] is True
    assert isinstance(run["fast_reason"], str) and run["fast_reason"].strip()


def test_a_first_completion_at_or_above_fast_lab_min_is_not_flagged(env):
    _, h, sid = fresh_run(env, "speedy3", active=900)
    run = env.run(sid)
    assert run["first_completion"] is True
    assert run["fast_flag"] is False and run["fast_reason"] is None


def test_the_lab_flag_and_the_task_flags_are_independent(env):
    _, h, sid = fresh_run(env, "speedy4", active=100, age=300)  # slow tasks, little active time
    assert env.run(sid)["fast_flag"] is True
    assert env.task(sid, PLAIN)["fast_flag"] is False
    _, _, other = fresh_run(env, "speedy3", active=5000, age=None)  # quick task, plenty of time
    assert env.task(other, PLAIN)["fast_flag"] is True
    assert env.run(other)["fast_flag"] is False


def test_a_lab_without_fast_lab_min_and_few_classmates_is_never_flagged(env):
    _, _, sid = fresh_run(env, "comp3", active=0, age=None)
    run = env.run(sid)
    assert run["first_completion"] is True
    assert run["fast_flag"] is False and run["fast_reason"] is None


def test_a_repeat_completion_is_never_flagged_even_when_it_is_much_faster(env):
    client, h, first = fresh_run(env, "speedy3", active=3000)
    assert client.post(f"/api/sessions/{first}/end").status_code == 204
    second = env.open_lab(client, "speedy3")
    complete(env, client, second, "speedy3", age=1, active=0)
    again = env.run(second)
    assert again["first_completion"] is False
    assert again["fast_flag"] is False and again["fast_reason"] is None


def test_a_fast_first_completion_stays_flagged_and_the_repeat_is_not(env):
    client, h, first = fresh_run(env, "speedy2", active=60)
    assert env.run(first)["fast_flag"] is True
    assert client.post(f"/api/sessions/{first}/end").status_code == 204
    second = env.open_lab(client, "speedy2")
    complete(env, client, second, "speedy2", active=10)
    assert env.run(second)["fast_flag"] is False
    assert env.run(first)["fast_flag"] is True


def _five_classmates(env, lab: str, active: int = 1000) -> None:
    for _ in range(5):
        _, _, sid = fresh_run(env, lab, active=active)
        assert env.run(sid)["first_completion"] is True


def test_with_five_classmates_a_first_completion_under_30_percent_of_the_median_is_flagged(env):
    # Five students at 1000 s: the median is 1000 s, 30 % is 300 s.
    _five_classmates(env, "median5")
    _, _, quick = fresh_run(env, "median5", active=250)
    run = env.run(quick)
    assert run["first_completion"] is True
    assert run["fast_flag"] is True and run["fast_reason"]
    _, _, normal = fresh_run(env, "median5", active=400)
    assert env.run(normal)["first_completion"] is True
    assert env.run(normal)["fast_flag"] is False
    assert env.run(normal)["fast_reason"] is None


def test_the_class_median_not_the_mean_is_used(env):
    # 1000, 1000, 1000, 100, 100: median 1000 (30 % = 300), mean 640 (30 % = 192).
    for active in (1000, 1000, 1000, 100, 100):
        fresh_run(env, "median5b", active=active)
    _, _, sid = fresh_run(env, "median5b", active=250)  # also above the lab's 1 minute limit
    assert env.run(sid)["fast_flag"] is True  # only the median rule can say so


def test_with_fewer_than_five_classmates_the_median_rule_does_not_apply(env):
    for _ in range(3):
        fresh_run(env, "median3", active=1000)
    _, _, sid = fresh_run(env, "median3", active=10)
    run = env.run(sid)
    assert run["first_completion"] is True
    assert run["fast_flag"] is False


def test_the_median_uses_first_completions_not_repeat_runs(env):
    # Five first completions at 1000 s: median 1000 s. One of them then repeats the lab many
    # times very fast; those runs must not pull the median down, so 250 s is still flagged.
    mate, _, sid = fresh_run(env, "median5c", active=1000)
    for _ in range(4):
        fresh_run(env, "median5c", active=1000)
    for _ in range(6):
        assert mate.post(f"/api/sessions/{sid}/end").status_code == 204
        sid = env.open_lab(mate, "median5c")
        complete(env, mate, sid, "median5c", active=1)
    client, _ = env.student()
    last = env.open_lab(client, "median5c")
    complete(env, client, last, "median5c", active=250)
    run = env.run(last)
    assert run["first_completion"] is True
    assert run["fast_flag"] is True


def test_sessions_outlive_the_janitors_startup_retry(env):
    # The janitor's failed startup pass retries about 2 s after the app starts (#284). A run
    # that spans the 2 s mark, with both sessions open before the wait, still completes.
    client, h = env.student()
    sid = env.open_lab(client, "slow")
    other, _ = env.student()
    later = env.open_lab(other, "slow")
    pass_task(env, client, sid, "slow", PLAIN)
    # Wait until 3.5 s after app start, so the janitor's 2 s retry has fired, however long the
    # setup above took.
    time.sleep(max(0.0, env.started + 3.5 - time.monotonic()))
    pass_task(env, client, sid, "slow", SHORT)
    assert len(env.completions(h, "slow")) == 1
    assert env.run(sid)["first_completion"] is True
    assert env.session(sid)["status"] == "running"
    assert env.session(later)["status"] == "running"
