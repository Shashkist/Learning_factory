"""Acceptance tests for issue #216: flag reasons as structured fields (split from #204, D66).

Spec section 9 (Student page), D55 (fast flags and their reasons), D64 (the student page API).
The web text built from these fields is pinned in
labs/web/tests/acceptance/issue-216-flag-reasons.test.tsx.
Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and skip locally
when it is absent (CI sets it and fails on any skip). Every id and name is fake.

CONTRACT (the issue names the information, the exam names the fields)

Each flag in GET /api/admin/students/<key> `labs[].flags[]` keeps its fields (id, scope, task_id,
reason, dismissed_at) and gains three more. `reason` (the English sentence) stays for old rows.
  "reason_code": "fast_task" | "fast_lab_min" | "fast_lab_median" | null
  "seconds":     number | null   fast_task: whole seconds from the task appearing to its first pass
                                 (the number in the sentence); the lab codes: whole seconds of
                                 active time of the lab run
  "threshold":   number | null   fast_task: the task's min_task_seconds (seconds); fast_lab_min: the
                                 lab's `fast_lab_min` in SECONDS (minutes * 60);
                                 fast_lab_median: the median of the earlier students' active seconds
All three are null for a row written before this change and for a row without them. They are all
set or all null, never mixed.

Storage (needed so that old rows exist and new rows keep their values; set by the exam). Both
`task_results` and `lab_runs` get three nullable columns, new migration:
  fast_code text, fast_seconds double precision, fast_threshold double precision
written together with `fast_flag` / `fast_reason` when the flag is raised and null otherwise. The
migration adds them as null for existing rows (no backfill).

Pure functions in app.flags (the builders; the *_reason functions stay as they are):
  fast_task_detail(task_id, kind, seconds, min_task_seconds=20, repeat=False)
      -> {"code": "fast_task", "seconds": int(seconds), "threshold": min_task_seconds} | None
  fast_lab_detail(active_seconds, fast_lab_min=None, prior_active_seconds=(), repeat=False)
      -> {"code": "fast_lab_min" | "fast_lab_median", "seconds": math.ceil(active_seconds),
          "threshold": fast_lab_min * 60 | median(prior_active_seconds)} | None
  A detail is None exactly when the matching *_reason is None; when both lab rules hold the
  minimum wins, as in the sentence.

app.student_page.assemble reads the flag rows' `fast_code`, `fast_seconds`, `fast_threshold` and
writes `reason_code`, `seconds`, `threshold`.
"""
# ruff: noqa: F811
import datetime as dt
import uuid
from pathlib import Path

import pytest
from test_issue_89_completion import (  # noqa: F401  (fixtures are used by name)
    LONG,
    PLAIN,
    SCHEMA,
    SHORT,
    _db,
    _five_classmates,
    env,
    fresh_run,
    pass_task,
    show,
    tasks_url,
)

from app import flags as _flags_module  # looked up at call time: absent -> AttributeError


def fast_task_detail(*args, **kwargs):
    return _flags_module.fast_task_detail(*args, **kwargs)


def fast_lab_detail(*args, **kwargs):
    return _flags_module.fast_lab_detail(*args, **kwargs)


def fast_task_reason(*args, **kwargs):
    return _flags_module.fast_task_reason(*args, **kwargs)


def fast_lab_reason(*args, **kwargs):
    return _flags_module.fast_lab_reason(*args, **kwargs)

# ================================================================ pure builders (no database)


def test_a_fast_task_detail_has_the_code_whole_seconds_and_the_limit():
    got = fast_task_detail("02-two", "hands-on", 9.7, 20)
    assert got == {"code": "fast_task", "seconds": 9, "threshold": 20}


def test_the_default_limit_of_a_task_detail_is_20_seconds():
    got = fast_task_detail("t", "hands-on", 5)
    assert got == {"code": "fast_task", "seconds": 5, "threshold": 20}


def test_a_task_detail_carries_the_per_task_limit():
    assert fast_task_detail("t", "hands-on", 60, 120) == {
        "code": "fast_task", "seconds": 60, "threshold": 120,
    }


@pytest.mark.parametrize(
    "args",
    [
        ("t", "quiz", 1, 20, False),  # a quiz is never fast
        ("t", "hands-on", 20, 20, False),  # the limit is strict
        ("t", "hands-on", 300, 20, False),
        ("t", "hands-on", 1, 20, True),  # a repeat run is never flagged
    ],
)
def test_no_task_detail_when_the_task_is_not_fast(args):
    assert fast_task_detail(*args) is None
    assert fast_task_reason(*args) is None


def test_a_lab_under_the_minimum_gives_the_minimum_code_with_the_limit_in_seconds():
    got = fast_lab_detail(95.4, fast_lab_min=10)  # whole seconds rounded up once (#137)
    assert got == {"code": "fast_lab_min", "seconds": 96, "threshold": 600}


def test_a_fractional_minimum_is_converted_to_seconds():
    got = fast_lab_detail(30, fast_lab_min=1.5)
    assert got is not None and got["threshold"] == pytest.approx(90)


def test_a_lab_under_30_percent_of_the_class_median_gives_the_median_code():
    got = fast_lab_detail(250, prior_active_seconds=[1000] * 5)
    assert got is not None
    assert got["code"] == "fast_lab_median" and got["seconds"] == 250
    assert got["threshold"] == pytest.approx(1000)


def test_the_median_threshold_is_the_median_not_the_mean():
    got = fast_lab_detail(100, prior_active_seconds=[400, 400, 400, 400, 4000])
    assert got is not None and got["code"] == "fast_lab_median"
    assert got["threshold"] == pytest.approx(400)


def test_the_minimum_wins_when_both_lab_rules_hold():
    got = fast_lab_detail(100, fast_lab_min=10, prior_active_seconds=[1000] * 5)
    assert got is not None and got["code"] == "fast_lab_min" and got["threshold"] == 600


@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # no limit, no classmates
        {"fast_lab_min": 1},  # 100 s is over 1 min
        {"prior_active_seconds": [1000] * 4},  # fewer than 5 classmates
        {"prior_active_seconds": [1000] * 5, "fast_lab_min": None, "repeat": True},
        {"fast_lab_min": 10, "repeat": True},
    ],
)
def test_no_lab_detail_when_the_lab_is_not_fast(kwargs):
    assert fast_lab_detail(100, **kwargs) is None
    assert fast_lab_reason(100, **kwargs) is None


def test_the_detail_and_the_sentence_agree_on_whether_the_lab_is_fast():
    cases = [
        (50, dict(fast_lab_min=1)),
        (59, dict(fast_lab_min=1)),
        (60, dict(fast_lab_min=1)),
        (299, dict(prior_active_seconds=[1000] * 5)),
        (300, dict(prior_active_seconds=[1000] * 5)),
    ]
    for active, kwargs in cases:
        detail = fast_lab_detail(active, **kwargs)
        reason = fast_lab_reason(active, **kwargs)
        assert (detail is None) == (reason is None)


def test_the_old_sentences_are_unchanged():
    sentence = fast_task_reason("02-two", "hands-on", 9, 20)
    assert sentence == "task 02-two passed 9 s after it appeared"
    assert fast_lab_reason(40, fast_lab_min=1) == (
        "lab finished in 40 s of active time, under the 1 min minimum"
    )


# ================================================================ assemble (no database)

SID = uuid.UUID("5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11")
KEY = uuid.UUID("9c1d7e22-3b4a-4f6e-8a10-2d3c4b5a6e77")
STUDENT = {
    "key": KEY, "name": "Fake Name", "cohort": "Fake cohort", "note": None, "disabled": False,
    "access_until": None, "cohort_until": dt.date(2027, 3, 1),
}


def _lab():
    from app.content import Lab, Task

    return Lab(
        id="x", title={"en": "T", "he": "כ"}, status="published", image="term", module=1, lesson=1,
        path=Path("."), tasks=[Task(id="a", kind="hands-on")],
    )


def _flag(scope, task_id, reason, code=None, seconds=None, threshold=None):
    return {
        "scope": scope, "lab_id": "x", "session_id": SID, "task_id": task_id,
        "fast_reason": reason,
        "fast_code": code, "fast_seconds": seconds, "fast_threshold": threshold,
        "flag_dismissed_at": None,
    }


def test_assemble_turns_the_stored_columns_into_the_structured_fields():
    from app.student_page import assemble

    rows = [_flag("task", "a", "task a passed 9 s after it appeared", "fast_task", 9.0, 20.0)]
    (got,) = assemble([_lab()], STUDENT, [], [], [], [], rows)["labs"][0]["flags"]
    assert got["reason"] == "task a passed 9 s after it appeared"
    assert got["reason_code"] == "fast_task"
    assert got["seconds"] == pytest.approx(9) and got["threshold"] == pytest.approx(20)
    assert got["scope"] == "task" and got["task_id"] == "a" and got["dismissed_at"] is None


def test_assemble_gives_null_fields_for_an_old_row_and_keeps_its_reason():
    from app.student_page import assemble

    rows = [_flag("lab", None, "lab finished in 40 s of active time")]
    (got,) = assemble([_lab()], STUDENT, [], [], [], [], rows)["labs"][0]["flags"]
    assert got["reason"] == "lab finished in 40 s of active time"
    assert (got["reason_code"], got["seconds"], got["threshold"]) == (None, None, None)


# ================================================================ the page API (database)


def _admin_client(env):
    from argon2 import PasswordHasher

    name = f"fake-admin-216-{uuid.uuid4().hex[:8]}"
    pw = "admin-" + "password-216"  # throwaway
    hashed = PasswordHasher().hash(pw)
    env.execute(
        f"insert into {SCHEMA}.admins (username, password_hash) values ($1, $2)", name, hashed
    )
    client = env.new_client()
    resp = client.post("/api/auth/admin/login", json={"username": name, "password": pw})
    assert resp.status_code == 200, resp.text
    return client


def _page(env, student_hash: str) -> dict:
    (row,) = env.rows(f"select key from {SCHEMA}.students where id_hash = $1", student_hash)
    resp = _admin_client(env).get(f"/api/admin/students/{row['key']}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _flags(body: dict, lab: str) -> list[dict]:
    (found,) = [x for x in body["labs"] if x["id"] == lab]
    return found["flags"]


def _only(flags: list[dict], scope: str) -> dict:
    (one,) = [f for f in flags if f["scope"] == scope]
    return one


def _fast_task(env, lab="speedy", tid=PLAIN, age=10):
    client, h = env.student()
    sid = env.open_lab(client, lab)
    pass_task(env, client, sid, lab, tid, age=age)
    return h, sid


def test_a_fast_task_flag_reaches_the_page_with_its_structured_fields(env):
    h, sid = _fast_task(env, tid=PLAIN, age=10)
    flag = _only(_flags(_page(env, h), "speedy"), "task")
    assert flag["task_id"] == PLAIN
    assert flag["reason_code"] == "fast_task"
    assert flag["seconds"] == pytest.approx(10, abs=3)
    assert flag["threshold"] == 20
    assert flag["reason"]  # the sentence stays


def test_the_flag_keeps_the_english_reason_next_to_the_fields(env):
    h, sid = _fast_task(env, tid=PLAIN, age=10)
    flag = _only(_flags(_page(env, h), "speedy"), "task")
    assert flag["reason"] == env.task(sid, PLAIN)["fast_reason"]
    assert flag["reason"].startswith(f"task {PLAIN} passed ")


def test_a_per_task_limit_is_the_threshold_of_that_flag(env):
    h, _ = _fast_task(env, tid=LONG, age=60)  # limit 120 in lab.yaml
    flag = _only(_flags(_page(env, h), "speedy"), "task")
    assert (flag["task_id"], flag["reason_code"], flag["threshold"]) == (LONG, "fast_task", 120)
    assert flag["seconds"] == pytest.approx(60, abs=3)


def test_the_fast_task_columns_are_written_with_the_flag_and_null_without_it(env):
    _, fast_sid = _fast_task(env, tid=PLAIN, age=10)
    row = env.task(fast_sid, PLAIN)
    assert row["fast_flag"] is True and row["fast_code"] == "fast_task"
    assert row["fast_seconds"] == pytest.approx(10, abs=3) and row["fast_threshold"] == 20
    _, slow_sid = _fast_task(env, tid=PLAIN, age=60)
    slow = env.task(slow_sid, PLAIN)
    assert slow["fast_flag"] is False
    assert (slow["fast_code"], slow["fast_seconds"], slow["fast_threshold"]) == (None, None, None)


def test_a_slow_task_has_no_flag_on_the_page(env):
    h, _ = _fast_task(env, tid=PLAIN, age=60)
    assert _flags(_page(env, h), "speedy") == []


def test_a_fast_lab_under_the_minimum_reaches_the_page_with_the_limit_in_seconds(env):
    _, h, sid = fresh_run(env, "speedy2", active=300)  # lab says 10 min = 600 s
    flag = _only(_flags(_page(env, h), "speedy2"), "lab")
    assert flag["task_id"] is None
    assert flag["reason_code"] == "fast_lab_min"
    assert flag["seconds"] == pytest.approx(300, abs=1)
    assert flag["threshold"] == 600
    assert flag["reason"]
    run = env.run(sid)
    assert run["fast_code"] == "fast_lab_min" and run["fast_threshold"] == 600


def test_a_fast_lab_under_the_class_median_reaches_the_page_with_the_median(env):
    _five_classmates(env, "median5")  # five students at 1000 s: median 1000 s
    _, h, sid = fresh_run(env, "median5", active=250)
    flag = _only(_flags(_page(env, h), "median5"), "lab")
    assert flag["reason_code"] == "fast_lab_median"
    assert flag["seconds"] == pytest.approx(250, abs=1)
    assert flag["threshold"] == pytest.approx(1000, abs=1)
    run = env.run(sid)
    assert run["fast_code"] == "fast_lab_median"


def test_a_lab_that_is_not_fast_leaves_the_lab_columns_null(env):
    _, h, sid = fresh_run(env, "speedy3", active=900)
    run = env.run(sid)
    assert run["fast_flag"] is False
    assert (run["fast_code"], run["fast_seconds"], run["fast_threshold"]) == (None, None, None)
    assert _flags(_page(env, h), "speedy3") == []


def test_a_row_written_before_the_change_comes_back_with_null_fields_and_its_reason(env):
    h, sid = _fast_task(env, tid=PLAIN, age=10)
    env.execute(  # what a row of an older release looks like: a reason and no structured data
        f"update {SCHEMA}.task_results set fast_code = null, fast_seconds = null, "
        "fast_threshold = null, fast_reason = 'task old-one passed 9 s after it appeared' "
        "where session_id = $1 and task_id = $2",
        uuid.UUID(sid), PLAIN,
    )
    flag = _only(_flags(_page(env, h), "speedy"), "task")
    assert flag["reason"] == "task old-one passed 9 s after it appeared"
    assert (flag["reason_code"], flag["seconds"], flag["threshold"]) == (None, None, None)
    assert flag["task_id"] == PLAIN and flag["dismissed_at"] is None


def test_an_old_lab_row_comes_back_with_null_fields_too(env):
    _, h, sid = fresh_run(env, "speedy4", active=100, age=300)
    env.execute(
        f"update {SCHEMA}.lab_runs set fast_code = null, fast_seconds = null, "
        "fast_threshold = null, fast_reason = 'lab finished in 100 s of active time' "
        "where session_id = $1",
        uuid.UUID(sid),
    )
    flag = _only(_flags(_page(env, h), "speedy4"), "lab")
    assert flag["reason"] == "lab finished in 100 s of active time"
    assert (flag["reason_code"], flag["seconds"], flag["threshold"]) == (None, None, None)


def test_the_structured_fields_stay_after_the_flag_is_dismissed(env):
    h, _ = _fast_task(env, tid=SHORT, age=2)  # limit 5 in lab.yaml
    admin = _admin_client(env)
    (row,) = env.rows(f"select key from {SCHEMA}.students where id_hash = $1", h)
    url = f"/api/admin/students/{row['key']}"
    flag = _only(_flags(admin.get(url).json(), "speedy"), "task")
    assert flag["reason_code"] == "fast_task" and flag["threshold"] == 5
    resp = admin.post(f"{url}/flags/{flag['id']}/dismiss")
    assert resp.status_code == 200, resp.text
    after = _only(_flags(resp.json(), "speedy"), "task")
    assert after["dismissed_at"] is not None
    assert (after["reason_code"], after["threshold"]) == ("fast_task", 5)
    assert after["seconds"] == pytest.approx(flag["seconds"])


def test_the_task_flag_and_the_lab_flag_of_one_student_each_have_their_own_fields(env):
    client, h = env.student()
    sid = env.open_lab(client, "speedy2")
    env.set_active(sid, 120)
    pass_task(env, client, sid, "speedy2", PLAIN, age=None)  # instantly, in a lab of little time
    flags = _flags(_page(env, h), "speedy2")
    assert {f["scope"] for f in flags} == {"task", "lab"}
    assert _only(flags, "task")["reason_code"] == "fast_task"
    assert _only(flags, "lab")["reason_code"] == "fast_lab_min"
    assert _only(flags, "task")["threshold"] == 20 and _only(flags, "lab")["threshold"] == 600


def test_the_student_never_sees_the_structured_fields(env):
    client, h = env.student()
    sid = env.open_lab(client, "speedy")
    show(client, sid, PLAIN)
    env.runtime.passing = True
    resp = client.post(f"{tasks_url(sid)}/{PLAIN}/check")
    listing = client.get(tasks_url(sid))
    for r in (resp, listing, client.get("/api/sessions/current")):
        for word in ("reason_code", "fast_code", "fast_seconds", "fast_threshold"):
            assert word not in r.text, r.text[:300]
