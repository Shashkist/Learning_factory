"""Acceptance tests for issue #137: follow-ups of the completion work (#89, #134).

Spec section 8 (fast-work flag), D55 (g: `active_seconds` is double precision), D61 (`lf-smoke`).

CONTRACT chosen by this exam:

Rounding. The run's active time is rounded ONCE, with ceil, to whole seconds, before the lab
flag is decided. The same whole number is used for the rule (strictly below the limit), the
`seconds` of `fast_lab_detail`, the number in the reason text ("lab finished in N s of active
time") and the stored `lab_runs.active_seconds`. So 299.4 s with `fast_lab_min: 5` is 300 s:
not below 300, not flagged; 298.2 s is 299 s: flagged, reason and row both say 299.

Smoke cohort. The `lf-smoke` cohort's runs (smoke runs on every deploy, with tiny active times)
must not count as classmates: they are left out of the first-completion priors of the class
median rule (and of its "at least 5 other students" count), exactly as the teacher views leave
the cohort out (app.smoke.NOT_SMOKE_SQL). The class table and the student page already leave
the smoke cohort's rows out; the exams of #163 and #164 pin that, and this file does not
repeat it. What it pins is the one place where the smoke runs still reach teacher-visible
data: the lab flags that the teacher views show.

Same harness as test_issue_89_completion.py (CI Postgres, fake runtime, fake students); the
database tests skip locally without LF_TEST_DATABASE_URL.
"""
# ruff: noqa: F811 (imported fixtures are used as test arguments)

import pytest

from tests.acceptance.test_issue_89_completion import (  # noqa: F401 (fixtures)
    DAY,
    LABS,
    PLAIN,
    SCHEMA,
    TODAY,
    _db,
    complete,
    env,
    fresh_run,
)

# One lab per test: the database is shared by the whole module, so no test may see another's runs.
LABS["round5a"] = ([(PLAIN, "hands-on")], 5)
LABS["round5b"] = ([(PLAIN, "hands-on")], 5)
LABS["smokelow"] = ([(PLAIN, "hands-on")], None)
LABS["smokehigh"] = ([(PLAIN, "hands-on")], None)

SMOKE = "lf-smoke"


@pytest.fixture(autouse=True)
def _no_janitor_passes(monkeypatch):
    """Keep the janitor out of these cases.

    The #89 harness swaps in its FakeRuntime after the app has started, so the janitor's startup
    pass fails on the real Docker socket and retries 2 s later. FakeRuntime lists no containers,
    so that retry ends every running session as an orphan. The smoke cases open ten or more
    sessions and outlive the 2 s (seen in CI: `session_ended` on the tenth session)."""
    import app.janitor as janitor

    async def idle(_app):
        return True

    monkeypatch.setattr(janitor, "run_once", idle)


# ================================================================ pure functions (no database)


def test_299_point_4_seconds_with_a_5_minute_minimum_is_not_fast():
    from app.flags import fast_lab_detail, fast_lab_reason, is_fast_lab

    assert is_fast_lab(299.4, fast_lab_min=5) is False
    assert fast_lab_reason(299.4, fast_lab_min=5) is None
    assert fast_lab_detail(299.4, fast_lab_min=5) is None


def test_299_point_4_seconds_is_judged_as_the_same_whole_seconds_as_300():
    from app.flags import is_fast_lab

    assert is_fast_lab(299.4, fast_lab_min=5) == is_fast_lab(300, fast_lab_min=5)


def test_298_point_2_seconds_is_rounded_up_to_299_and_is_fast_with_299_in_the_reason():
    from app.flags import fast_lab_detail, fast_lab_reason, is_fast_lab

    assert is_fast_lab(298.2, fast_lab_min=5) is True
    reason = fast_lab_reason(298.2, fast_lab_min=5)
    assert reason is not None
    assert "299 s" in reason
    assert "298 s" not in reason
    detail = fast_lab_detail(298.2, fast_lab_min=5)
    assert detail is not None
    assert detail["code"] == "fast_lab_min"
    assert detail["seconds"] == 299


def test_a_whole_number_of_seconds_is_not_changed_by_the_rounding():
    from app.flags import fast_lab_reason

    reason = fast_lab_reason(299, fast_lab_min=5)
    assert reason is not None and "299 s" in reason
    assert fast_lab_reason(300, fast_lab_min=5) is None


def test_the_class_median_rule_uses_the_same_rounded_time():
    from app.flags import fast_lab_detail, is_fast_lab

    priors = [1000.0] * 5  # median 1000 s, 30 % = 300 s
    assert is_fast_lab(299.4, prior_active_seconds=priors) is False
    assert fast_lab_detail(299.4, prior_active_seconds=priors) is None
    assert is_fast_lab(298.2, prior_active_seconds=priors) is True
    detail = fast_lab_detail(298.2, prior_active_seconds=priors)
    assert detail is not None and detail["seconds"] == 299


# ================================================================ stored run (database)


def test_299_point_4_seconds_is_stored_as_300_and_not_flagged(env):
    _, _, sid = fresh_run(env, "round5a", active=299.4)
    run = env.run(sid)
    assert run is not None
    assert run["first_completion"] is True
    assert run["active_seconds"] == 300
    assert run["fast_flag"] is False
    assert run["fast_reason"] is None


def test_298_point_2_seconds_is_stored_as_299_and_the_reason_says_299(env):
    _, _, sid = fresh_run(env, "round5b", active=298.2)
    run = env.run(sid)
    assert run is not None
    assert run["active_seconds"] == 299
    assert run["fast_flag"] is True
    assert "299 s" in run["fast_reason"]
    assert "298 s" not in run["fast_reason"]


# ================================================================ lf-smoke runs are not classmates


def smoke_run(env, lab: str, active: float) -> str:
    """A student of the reserved `lf-smoke` cohort completes `lab` with `active` seconds."""
    client, h = env.student()

    async def move(conn):
        cid = await conn.fetchval(
            f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
            f"select $1, password_hash, $2 from {SCHEMA}.cohorts limit 1 returning id",
            SMOKE, TODAY + 30 * DAY,
        )
        await conn.execute(
            f"update {SCHEMA}.students set cohort_id = $2 where id_hash = $1", h, cid
        )

    env.sql(move)
    sid = env.open_lab(client, lab)
    complete(env, client, sid, lab, active=active)
    run = env.run(sid)
    assert run is not None, "the smoke student's run must still be recorded"
    return sid


def test_smoke_runs_do_not_pull_the_class_median_down(env):
    # Five real first completions at 1000 s: median 1000 s, so 250 s (< 300) is fast. Six smoke
    # runs at 10 s would make the median 10 s (limit 3 s) and hide the flag.
    for _ in range(5):
        fresh_run(env, "smokelow", active=1000)
    for _ in range(6):
        smoke_run(env, "smokelow", 10)
    _, _, sid = fresh_run(env, "smokelow", active=250)
    run = env.run(sid)
    assert run["first_completion"] is True
    assert run["fast_flag"] is True
    assert run["fast_reason"]


def test_smoke_runs_do_not_count_towards_the_five_other_students(env):
    # Four real classmates and five smoke runs: only four count, the median rule needs five.
    for _ in range(4):
        fresh_run(env, "smokehigh", active=1000)
    for _ in range(5):
        smoke_run(env, "smokehigh", 1000)
    _, _, sid = fresh_run(env, "smokehigh", active=10)
    run = env.run(sid)
    assert run["first_completion"] is True
    assert run["fast_flag"] is False
    assert run["fast_reason"] is None
