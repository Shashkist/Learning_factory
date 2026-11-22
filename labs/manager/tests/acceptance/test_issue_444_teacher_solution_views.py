# ruff: noqa: F811
"""Acceptance tests for issue #444: teacher views show when a student opened a task's solution.

Spec sections 8 and 9, decisions D55 (the fast flag) and D128 (`task_results.solution_viewed_at`,
stamped by #154 on the first view). Reuses the fixtures and helpers of the #164 exam. Database
tests need LF_TEST_DATABASE_URL and skip locally (CI sets it and fails on any skip). Every name
and id is fake.

CONTRACT (names chosen by this exam)
1. GET /api/admin/students/<key> (also the PATCH and dismiss answers, which carry the same body):
   every entry of `labs[].tasks[]` gains `solution_viewed_at`: an ISO 8601 timestamp string with
   a time zone, or null. It is the EARLIEST non-null `task_results.solution_viewed_at` of that
   task over all the student's sessions (several sessions count once), null when the student
   never opened the solution (also for a task with no result row). Nothing else on the body
   changes (no new lab-level field is pinned).
2. GET /api/admin/class: every cell `cells[<lab id>]` gains `solution_viewed: bool`, true when
   the student opened the solution of at least one task of that lab in any session. The cell is
   then `{"status": ..., "flag": ..., "solution_viewed": ...}`. It is independent of `flag`
   (the fast flag) and of `status`.
3. A view changes nothing else: `status`, `tasks_passed`, `passed`, `attempts`, `seconds`, the
   flags and the `flag` of the cell are what they would be without it (D128: no score effect and
   no automatic flag). `?flagged=1` still lists only students with a live fast flag (a view alone
   does not make a row flagged). `GET /api/admin/class.csv` is unchanged: its cells hold only the
   status and the bolt.
4. Students of the reserved `lf-smoke` cohort are ignored, as everywhere: not in the class table,
   404 on the student page, and their views change no other student's cell or page.
5. No id hash and no raw id appears in the new data (the bodies stay free of both).

The web side is pinned in labs/web/tests/acceptance/issue-444-teacher-solution-views.test.tsx.
"""

import datetime as dt

from tests.acceptance.test_issue_164_student_page import (  # noqa: F401 (fixtures)
    SCHEMA,
    T1,
    T2,
    T3,
    Student,
    _db,
    admin,
    class_cell,
    env,
    lab_of,
    page,
    page_url,
    task_of,
)

VIEWED = dt.datetime.now(dt.UTC).replace(microsecond=0) - dt.timedelta(days=2)
EARLIER = VIEWED - dt.timedelta(hours=5)


def view(env, s: Student, lab: str, task: str, at: dt.datetime = VIEWED) -> None:
    """Stamp solution_viewed_at on the student's task_results row(s) of that task."""

    async def seed(conn):
        n = await conn.fetchval(
            f"update {SCHEMA}.task_results set solution_viewed_at = $4 where student = $1 "
            "and lab_id = $2 and task_id = $3 returning 1",
            s.hash,
            lab,
            task,
            at,
        )
        assert n == 1, "the test seeds a result row first"

    env.sql(seed)


def viewed_at(body: dict, lab: str, task: str):
    return task_of(body, lab, task)["solution_viewed_at"]


def parse(value: str) -> dt.datetime:
    got = dt.datetime.fromisoformat(value)
    assert got.tzinfo is not None, f"{value!r} has no time zone"
    return got


def cells(admin, s: Student) -> dict:
    rows = admin.get("/api/admin/class", params={"cohort": s.cohort}).json()["rows"]
    return next(r for r in rows if r["name"] == s.name)["cells"]


# ================================================================== student page (criterion 1)


def test_student_page_task_has_solution_viewed_at_null_when_never_opened(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    body = page(env, admin, s)
    for lab in body["labs"]:
        for task in lab["tasks"]:
            assert "solution_viewed_at" in task, f"{lab['id']}/{task['id']}"
            assert task["solution_viewed_at"] is None


def test_student_page_shows_when_the_student_first_opened_the_solution(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    env.result(s, "stu-a", T2, seconds=40)
    view(env, s, "stu-a", T1)
    body = page(env, admin, s)
    assert parse(viewed_at(body, "stu-a", T1)) == VIEWED
    assert viewed_at(body, "stu-a", T2) is None, "only the task that was opened is marked"
    assert viewed_at(body, "stu-a", T3) is None, "a task with no result row is null"


def test_student_page_shows_the_view_of_a_task_that_was_never_passed(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=None, attempts=2)  # failed checks, then a look
    view(env, s, "stu-a", T1)
    body = page(env, admin, s)
    assert parse(viewed_at(body, "stu-a", T1)) == VIEWED
    assert task_of(body, "stu-a", T1)["passed"] is False


def test_student_page_shows_the_earliest_view_over_sessions(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=30)  # session one
    env.result(s, "stu-a", T1, seconds=50)  # session two
    env.result(s, "stu-a", T1, seconds=None)  # session three, never opened there

    async def two_views(conn):
        rows = await conn.fetch(
            f"select session_id from {SCHEMA}.task_results where student = $1 and task_id = $2 "
            "order by passed_at nulls last, session_id",
            s.hash,
            T1,
        )
        for row, at in zip(rows[:2], (VIEWED, EARLIER), strict=True):
            await conn.execute(
                f"update {SCHEMA}.task_results set solution_viewed_at = $3 "
                "where student = $1 and session_id = $2",
                s.hash,
                row["session_id"],
                at,
            )

    env.sql(two_views)
    assert parse(viewed_at(page(env, admin, s), "stu-a", T1)) == EARLIER


def test_another_students_view_does_not_show_on_this_page(env, admin):
    a, b = env.student(), env.student()
    env.result(a, "stu-a", T1, seconds=30)
    env.result(b, "stu-a", T1, seconds=30)
    view(env, b, "stu-a", T1)
    assert viewed_at(page(env, admin, a), "stu-a", T1) is None
    assert parse(viewed_at(page(env, admin, b), "stu-a", T1)) == VIEWED


def test_patch_and_dismiss_answers_carry_the_same_solution_viewed_at(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=9, flag=True)
    view(env, s, "stu-a", T1)
    key = env.key(s)
    got = admin.get(page_url(key)).json()
    assert parse(viewed_at(got, "stu-a", T1)) == VIEWED
    patched = admin.patch(page_url(key), json={"note": "x"})
    assert patched.status_code == 200, patched.text
    assert patched.json() == admin.get(page_url(key)).json()
    assert parse(viewed_at(patched.json(), "stu-a", T1)) == VIEWED
    flag = lab_of(got, "stu-a")["flags"][0]["id"]
    dismissed = admin.post(f"{page_url(key)}/flags/{flag}/dismiss")
    assert dismissed.status_code == 200, dismissed.text
    assert parse(viewed_at(dismissed.json(), "stu-a", T1)) == VIEWED


def test_student_page_viewing_has_no_score_effect(env, admin):
    plain, looker = env.student(), env.student()
    for s in (plain, looker):
        env.result(s, "stu-a", T1, seconds=30, attempts=2)
        env.result(s, "stu-a", T2, seconds=None, attempts=3)
    view(env, looker, "stu-a", T2)
    a = lab_of(page(env, admin, plain), "stu-a")
    b = lab_of(page(env, admin, looker), "stu-a")
    for field in ("status", "tasks_passed", "tasks_total", "attempts", "flags"):
        assert a[field] == b[field], field

    def bare(tasks):
        return [{k: v for k, v in t.items() if k != "solution_viewed_at"} for t in tasks]

    assert bare(a["tasks"]) == bare(b["tasks"])


def test_a_view_alone_raises_no_flag_on_the_student_page(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    view(env, s, "stu-a", T1)
    assert lab_of(page(env, admin, s), "stu-a")["flags"] == []


def test_student_page_body_has_no_id_hash_or_raw_id_with_views_present(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    view(env, s, "stu-a", T1)
    text = str(page(env, admin, s))
    assert s.hash not in text
    assert s.raw not in text


# ================================================================== class table (criterion 2)


def test_class_cell_marks_the_lab_where_a_solution_was_opened(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    env.result(s, "stu-b", T1, seconds=30)
    view(env, s, "stu-a", T1)
    got = cells(admin, s)
    assert got["stu-a"]["solution_viewed"] is True
    assert got["stu-b"]["solution_viewed"] is False
    assert got["stu-med"]["solution_viewed"] is False, "an untouched lab is not marked"


def test_every_class_cell_carries_solution_viewed_false_by_default(env, admin):
    s = env.student()
    got = cells(admin, s)
    assert set(got) == {"stu-a", "stu-b", "stu-med"}
    for lab, cell in got.items():
        assert cell["solution_viewed"] is False, lab
        assert set(cell) == {"status", "flag", "solution_viewed"}, lab


def test_one_view_among_several_tasks_marks_the_lab_cell(env, admin):
    s = env.student()
    for task in (T1, T2, T3):
        env.result(s, "stu-a", task, seconds=30)
    view(env, s, "stu-a", T3)
    assert class_cell(admin, s, "stu-a")["solution_viewed"] is True


def test_the_mark_is_per_student(env, admin):
    cid = env.cohort()
    a, b = env.student(cid), env.student(cid)
    env.result(a, "stu-a", T1, seconds=30)
    env.result(b, "stu-a", T1, seconds=30)
    view(env, b, "stu-a", T1)
    assert class_cell(admin, a, "stu-a")["solution_viewed"] is False
    assert class_cell(admin, b, "stu-a")["solution_viewed"] is True


def test_the_mark_is_separate_from_the_fast_flag(env, admin):
    cid = env.cohort()
    only_view, only_flag, both, neither = (env.student(cid) for _ in range(4))
    for s in (only_view, only_flag, both, neither):
        env.result(s, "stu-a", T1, seconds=30, flag=s in (only_flag, both))
    view(env, only_view, "stu-a", T1)
    view(env, both, "stu-a", T1)
    want = {
        only_view: (False, True),
        only_flag: (True, False),
        both: (True, True),
        neither: (False, False),
    }
    for s, (flag, viewed) in want.items():
        cell = class_cell(admin, s, "stu-a")
        assert (cell["flag"], cell["solution_viewed"]) == (flag, viewed), s.name


def test_a_view_does_not_change_the_fast_flag_or_its_dismissal(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=9, flag=True)
    view(env, s, "stu-a", T1)
    cell = class_cell(admin, s, "stu-a")
    assert (cell["flag"], cell["solution_viewed"]) == (True, True)
    key = env.key(s)
    flag = lab_of(admin.get(page_url(key)).json(), "stu-a")["flags"][0]["id"]
    assert admin.post(f"{page_url(key)}/flags/{flag}/dismiss").status_code == 200
    cell = class_cell(admin, s, "stu-a")
    assert cell["flag"] is False, "dismissing the flag clears the bolt as before"
    assert cell["solution_viewed"] is True, "and does not clear the view mark"


def test_a_view_has_no_effect_on_the_cell_status(env, admin):
    plain, looker = env.student(), env.student()
    for s in (plain, looker):
        env.result(s, "stu-a", T1, seconds=30)
        env.result(s, "stu-a", T2, seconds=None)
    view(env, looker, "stu-a", T2)
    assert class_cell(admin, plain, "stu-a")["status"] == "1/3"
    assert class_cell(admin, looker, "stu-a")["status"] == "1/3"
    env.result(looker, "stu-b", T1, seconds=30)
    env.complete(looker, "stu-b")
    view(env, looker, "stu-b", T1)
    assert class_cell(admin, looker, "stu-b")["status"] == "done"
    assert class_cell(admin, looker, "stu-b")["solution_viewed"] is True


def test_flagged_filter_ignores_a_view_alone(env, admin):
    cid = env.cohort()
    looker, flagged = env.student(cid), env.student(cid)
    env.result(looker, "stu-a", T1, seconds=30)
    env.result(flagged, "stu-a", T1, seconds=9, flag=True)
    view(env, looker, "stu-a", T1)
    rows = admin.get("/api/admin/class", params={"cohort": cid, "flagged": 1}).json()["rows"]
    assert [r["name"] for r in rows] == [flagged.name]
    both = admin.get("/api/admin/class", params={"cohort": cid}).json()["rows"]
    assert {r["name"] for r in both} == {looker.name, flagged.name}


def test_class_csv_is_unchanged_by_views(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    view(env, s, "stu-a", T1)
    resp = admin.get("/api/admin/class.csv", params={"cohort": s.cohort})
    assert resp.status_code == 200
    line = next(ln for ln in resp.content.decode("utf-8-sig").splitlines() if s.name in ln)
    assert line.split(",")[3:] == ["1/3", "none", "none"]  # labs: stu-a, stu-b, stu-med
    assert "solution" not in line.lower()


def test_class_table_never_leaks_an_id_hash_through_the_new_field(env, admin):
    s = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    view(env, s, "stu-a", T1)
    text = admin.get("/api/admin/class", params={"cohort": s.cohort}).text
    assert s.hash not in text
    assert s.raw not in text


# ================================================================== smoke student (criterion 3)


def test_a_smoke_student_who_opened_a_solution_is_not_in_the_class_table(env, admin):
    smoke = env.smoke_student()
    env.result(smoke, "stu-a", T1, seconds=30)
    view(env, smoke, "stu-a", T1)
    rows = admin.get("/api/admin/class").json()["rows"]
    assert smoke.name not in {r["name"] for r in rows}
    assert admin.get("/api/admin/class", params={"cohort": smoke.cohort}).status_code == 404


def test_a_smoke_student_page_stays_a_404_with_a_view(env, admin):
    smoke = env.smoke_student()
    env.result(smoke, "stu-a", T1, seconds=30)
    view(env, smoke, "stu-a", T1)

    async def read(conn):
        return str(
            await conn.fetchval(f"select key from {SCHEMA}.students where id_hash = $1", smoke.hash)
        )

    assert admin.get(page_url(env.sql(read))).status_code == 404


def test_a_smoke_view_marks_no_other_students_cell_or_page(env, admin):
    smoke = env.smoke_student()
    real = env.student()
    for s in (smoke, real):
        env.result(s, "stu-a", T1, seconds=30)
    view(env, smoke, "stu-a", T1)
    assert class_cell(admin, real, "stu-a")["solution_viewed"] is False
    assert viewed_at(page(env, admin, real), "stu-a", T1) is None
