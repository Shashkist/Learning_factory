# ruff: noqa: F811
"""Acceptance tests for issue #360 (AC1): the student page "active_seconds" counts unfinished labs.

Spec: teacher views (student page), data model `lab_runs` / `lab_sessions.active_seconds` (D113).

CONTRACT. GET /api/admin/students/<key> "active_seconds" =
    sum(lab_runs.active_seconds)                                  finished runs
  + sum(lab_sessions.active_seconds of the student's sessions
        that have NO lab_runs row (lab_runs.session_id = lab_sessions.id))
A completed session's time is copied into its run (the session row keeps its value), so a session
that has a run is counted through the run only, never twice. Always a number, never null.
Database tests need LF_TEST_DATABASE_URL and skip locally (CI sets it and fails on any skip).
Every name and id is fake; fixtures come from the #164 exam.
"""

import datetime as dt

from tests.acceptance.test_issue_164_student_page import (  # noqa: F401 (fixtures)
    BASE,
    DAY,
    SCHEMA,
    _db,
    admin,
    env,
    page,
    page_url,
)


def _session(env, s, lab: str, seconds: float, started: dt.datetime = BASE) -> str:
    """A session with heartbeat time and no run."""

    async def seed(conn):
        return str(
            await conn.fetchval(
                f"insert into {SCHEMA}.lab_sessions (student, lab_id, status, started_at, "
                "ended_at, active_seconds) values ($1, $2, 'ended', $3, $3, $4) returning id",
                s.hash,
                lab,
                started,
                seconds,
            )
        )

    return env.sql(seed)


def _finish(env, s, lab: str, sid: str, seconds: int) -> None:
    """The run of a completed session: its time is copied from the session."""

    async def seed(conn):
        await conn.execute(
            f"insert into {SCHEMA}.lab_runs (student, lab_id, session_id, first_completion, "
            "active_seconds) values ($1, $2, $3::uuid, false, $4)",
            s.hash,
            lab,
            sid,
            seconds,
        )

    env.sql(seed)


def _total(env, admin, s) -> float:
    value = page(env, admin, s)["active_seconds"]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return value


def test_finished_runs_plus_unfinished_sessions_are_summed(env, admin):
    s = env.student()
    done = _session(env, s, "stu-a", 600, BASE)
    _finish(env, s, "stu-a", done, 600)
    _session(env, s, "stu-b", 10800, BASE + DAY)  # three hours in a lab not finished
    assert _total(env, admin, s) == 600 + 10800


def test_a_session_with_a_run_is_counted_once_not_twice(env, admin):
    s = env.student()
    sid = _session(env, s, "stu-a", 1200)  # the session keeps its heartbeat time ...
    _finish(env, s, "stu-a", sid, 1200)  # ... and the run holds the same time
    assert _total(env, admin, s) == 1200


def test_a_mix_across_several_labs(env, admin):
    s = env.student()
    a = _session(env, s, "stu-a", 100, BASE)
    _finish(env, s, "stu-a", a, 100)
    b = _session(env, s, "stu-b", 250, BASE + DAY)
    _finish(env, s, "stu-b", b, 250)
    _session(env, s, "stu-a", 40, BASE + 2 * DAY)  # a second, unfinished try of lab A
    _session(env, s, "stu-b", 7, BASE + 3 * DAY)
    _session(env, s, "stu-c", 3000, BASE + 4 * DAY)
    assert _total(env, admin, s) == 100 + 250 + 40 + 7 + 3000


def test_sessions_of_other_students_are_not_counted(env, admin):
    me, other = env.student(), env.student()
    _session(env, me, "stu-a", 60)
    _session(env, other, "stu-a", 9000)
    done = _session(env, other, "stu-b", 500)
    _finish(env, other, "stu-b", done, 500)
    assert _total(env, admin, me) == 60
    assert _total(env, admin, other) == 9500


def test_a_session_with_zero_active_time_adds_nothing_and_stays_a_number(env, admin):
    s = env.student()
    _session(env, s, "stu-a", 0)
    assert _total(env, admin, s) == 0
    _session(env, s, "stu-b", 30, BASE + DAY)
    assert _total(env, admin, s) == 30


def test_fractional_heartbeat_time_is_summed(env, admin):
    s = env.student()
    _session(env, s, "stu-a", 10.5)
    _session(env, s, "stu-b", 20.25, BASE + DAY)
    assert _total(env, admin, s) == 30.75
