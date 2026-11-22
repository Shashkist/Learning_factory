# ruff: noqa: F811
"""Acceptance tests for issue #458 (pins #444, D128 amended): a solution view of a task that is
no longer in lab.yaml does not mark the class cell, matching `cell_status` (only tasks still in
lab.yaml count). Reuses the fixtures of the #164 and #444 exams. Database tests need
LF_TEST_DATABASE_URL and skip locally (CI sets it and fails on any skip). Every name and id is fake.
"""

from tests.acceptance.test_issue_164_student_page import (  # noqa: F401 (fixtures)
    T1,
    Student,
    _db,
    admin,
    class_cell,
    env,
    page,
)
from tests.acceptance.test_issue_444_teacher_solution_views import view, viewed_at

REMOVED = "90-removed"  # has a result row but is not in lab.yaml


def test_a_view_of_a_task_no_longer_in_lab_yaml_does_not_mark_the_class_cell(env, admin):
    s: Student = env.student()
    env.result(s, "stu-a", REMOVED, seconds=30)
    view(env, s, "stu-a", REMOVED)
    assert class_cell(admin, s, "stu-a")["solution_viewed"] is False


def test_a_removed_task_view_does_not_change_the_cell_or_the_page(env, admin):
    s: Student = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    env.result(s, "stu-a", REMOVED, seconds=30)
    view(env, s, "stu-a", REMOVED)
    cell = class_cell(admin, s, "stu-a")
    assert cell["solution_viewed"] is False
    assert cell["status"] == "1/3"
    assert viewed_at(page(env, admin, s), "stu-a", T1) is None


def test_a_view_of_a_current_task_still_marks_the_cell_next_to_a_removed_one(env, admin):
    s: Student = env.student()
    env.result(s, "stu-a", T1, seconds=30)
    env.result(s, "stu-a", REMOVED, seconds=30)
    view(env, s, "stu-a", REMOVED)
    view(env, s, "stu-a", T1)
    assert class_cell(admin, s, "stu-a")["solution_viewed"] is True
