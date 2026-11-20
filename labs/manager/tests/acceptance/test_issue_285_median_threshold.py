"""Acceptance tests for issue #285 (D87): the class-median flag has one whole-seconds threshold.

With an even number of earlier students the median is x.5 s. The stored threshold and the number in
the reason text must be the same whole number: math.ceil(median). The fast / not fast comparison
still uses the raw median. Database tests skip locally without LF_TEST_DATABASE_URL. All data fake.
"""
# ruff: noqa: F811 (imported fixtures are used as test arguments)
import math

from app import flags as _flags
from tests.acceptance.test_issue_89_completion import (  # noqa: F401 (fixtures)
    LABS,
    PLAIN,
    SCHEMA,
    _db,
    env,
    fresh_run,
)

LABS["median285"] = ([(PLAIN, "hands-on")], None)

HALF = [1000, 1000, 1000, 1001, 1001, 1001]  # median 1000.5 s
WHOLE = [1000] * 6  # median 1000 s


def _number_in_reason(reason: str) -> int:
    inner = reason.rsplit("(", 1)[1].split(" s)")[0]
    return int(inner)  # raises on "1000.5" or "1000"-with-fraction


def test_a_half_second_median_gives_a_whole_second_threshold():
    got = _flags.fast_lab_detail(100, prior_active_seconds=HALF)
    assert got is not None and got["code"] == "fast_lab_median"
    assert got["threshold"] == 1001
    assert isinstance(got["threshold"], int)


def test_the_reason_shows_the_same_threshold_as_the_detail():
    reason = _flags.fast_lab_reason(100, prior_active_seconds=HALF)
    detail = _flags.fast_lab_detail(100, prior_active_seconds=HALF)
    assert reason is not None and "(1001 s)" in reason
    assert "1000 s" not in reason
    assert _number_in_reason(reason) == detail["threshold"]


def test_a_whole_median_stays_the_same_whole_number():
    got = _flags.fast_lab_detail(100, prior_active_seconds=WHOLE)
    assert got["threshold"] == 1000 and isinstance(got["threshold"], int)
    assert "(1000 s)" in _flags.fast_lab_reason(100, prior_active_seconds=WHOLE)


def test_the_threshold_is_the_ceiling_of_the_median_for_other_halves():
    priors = [10, 20, 30, 41, 50, 60]  # median 35.5
    got = _flags.fast_lab_detail(1, prior_active_seconds=priors)
    assert got["threshold"] == math.ceil(35.5) == 36


def test_the_comparison_uses_the_raw_median_not_the_shown_threshold():
    # Median 1016.5: limit 0.3 * 1016.5 = 304.95. The shown threshold is 1017 (limit 305.1), but
    # 305 s is not under the raw limit, so it is not fast; 304 s is.
    priors = [1016, 1016, 1016, 1017, 1017, 1017]
    assert _flags.fast_lab_detail(305, prior_active_seconds=priors) is None
    assert _flags.fast_lab_reason(305, prior_active_seconds=priors) is None
    got = _flags.fast_lab_detail(304, prior_active_seconds=priors)
    assert got is not None and got["threshold"] == 1017


def test_the_minimum_threshold_is_unchanged():
    got = _flags.fast_lab_detail(100, fast_lab_min=5, prior_active_seconds=HALF)
    assert got["code"] == "fast_lab_min" and got["threshold"] == 300


# ================================================================ stored run (database)


def test_the_stored_threshold_and_the_stored_reason_both_say_1001(env):
    for a in HALF:
        fresh_run(env, "median285", active=a)
    _, _, sid = fresh_run(env, "median285", active=100)
    run = env.run(sid)
    assert run["fast_code"] == "fast_lab_median"
    assert run["fast_threshold"] == 1001
    assert "(1001 s)" in run["fast_reason"]
