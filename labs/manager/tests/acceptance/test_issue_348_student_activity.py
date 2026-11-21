# ruff: noqa: F811
"""Acceptance tests for issue #348 (AC1): the student page API carries activity figures (D110).

Spec: teacher views (student page), data model `lab_runs.active_seconds`.

CONTRACT. GET /api/admin/students/<key> (and PATCH / dismiss, which answer the same body) gains two
top-level fields:
  "last_activity": "<ISO 8601>" | null   the same "seen" source as the class table row of that
                                          student: the latest of lab_sessions.started_at,
                                          last_heartbeat and ended_at; null with no session.
  "active_seconds": number               the sum of lab_runs.active_seconds over all of the
                                          student's runs (every lab, every session); 0 when the
                                          student has no run. Never null, never missing.
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


def _session(env, s, lab: str, started: dt.datetime, ended: dt.datetime | None) -> str:
    async def seed(conn):
        return str(
            await conn.fetchval(
                f"insert into {SCHEMA}.lab_sessions (student, lab_id, status, started_at, "
                "ended_at) values ($1, $2, 'ended', $3, $4) returning id",
                s.hash,
                lab,
                started,
                ended or started,
            )
        )

    return env.sql(seed)


def _run(env, s, lab: str, seconds: int, started: dt.datetime = BASE) -> None:
    sid = _session(env, s, lab, started, started)

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


def _at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def _class_last_activity(env, admin, s) -> str | None:
    rows = admin.get("/api/admin/class", params={"cohort": s.cohort}).json()["rows"]
    return next(r for r in rows if r["name"] == s.name)["last_activity"]


# ---------------------------------------------------------------- active_seconds


def test_active_seconds_is_zero_for_a_student_with_no_run(env, admin):
    s = env.student()
    body = page(env, admin, s)
    assert "active_seconds" in body
    assert body["active_seconds"] == 0


def test_active_seconds_is_the_sum_over_all_runs_of_all_labs(env, admin):
    s = env.student()
    _run(env, s, "stu-a", 100, BASE)
    _run(env, s, "stu-a", 250, BASE + DAY)  # a second run of the same lab counts too
    _run(env, s, "stu-b", 3375, BASE + 2 * DAY)
    assert page(env, admin, s)["active_seconds"] == 3725


def test_active_seconds_counts_only_this_students_runs(env, admin):
    me, other = env.student(), env.student()
    _run(env, me, "stu-a", 60)
    _run(env, other, "stu-a", 9000)
    assert page(env, admin, me)["active_seconds"] == 60
    assert page(env, admin, other)["active_seconds"] == 9000


def test_active_seconds_is_a_number_not_null_for_a_student_with_only_sessions(env, admin):
    # #360: heartbeat time of a session that has no run yet is counted.
    s = env.student()
    sid = _session(env, s, "stu-a", BASE, BASE)

    async def seed(conn):
        await conn.execute(
            f"update {SCHEMA}.lab_sessions set active_seconds = 900 where id = $1::uuid", sid
        )

    env.sql(seed)
    value = page(env, admin, s)["active_seconds"]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    assert value == 900


# ---------------------------------------------------------------- last_activity


def test_last_activity_is_null_for_a_student_who_never_started_a_lab(env, admin):
    s = env.student()
    body = page(env, admin, s)
    assert "last_activity" in body
    assert body["last_activity"] is None


def test_last_activity_is_the_latest_session_time(env, admin):
    s = env.student()
    _session(env, s, "stu-a", BASE, BASE + dt.timedelta(hours=1))
    latest = BASE + 3 * DAY
    _session(env, s, "stu-b", latest - dt.timedelta(hours=1), latest)
    got = page(env, admin, s)["last_activity"]
    assert isinstance(got, str)
    assert _at(got) == latest


def test_last_activity_equals_the_class_table_seen_value(env, admin):
    s = env.student()
    _session(env, s, "stu-a", BASE, BASE + dt.timedelta(minutes=20))
    seen = _class_last_activity(env, admin, s)
    assert seen is not None
    assert page(env, admin, s)["last_activity"] == seen


def test_last_activity_uses_the_heartbeat_when_it_is_the_latest(env, admin):
    s = env.student()
    beat = BASE + 2 * DAY

    async def seed(conn):
        await conn.execute(
            f"insert into {SCHEMA}.lab_sessions (student, lab_id, status, started_at, "
            "last_heartbeat) values ($1, 'stu-a', 'running', $2, $3)",
            s.hash,
            BASE,
            beat,
        )

    env.sql(seed)
    got = page(env, admin, s)["last_activity"]
    assert got is not None and _at(got) == beat
    assert got == _class_last_activity(env, admin, s)


def test_patch_answers_the_new_fields_like_get(env, admin):
    s = env.student()
    _run(env, s, "stu-a", 42)
    key = env.key(s)
    resp = admin.patch(page_url(key), json={"note": "x"})
    assert resp.status_code == 200, resp.text
    got = admin.get(page_url(key)).json()
    assert resp.json() == got
    assert got["active_seconds"] == 42
    assert got["last_activity"] is not None
