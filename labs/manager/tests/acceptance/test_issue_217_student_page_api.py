# ruff: noqa: F811
"""Acceptance tests for issue #217: student page API nits (split from #204, D66).

Spec section 9, D63, D64. Reuses the fixtures and helpers of the #164 exam. Database tests need
LF_TEST_DATABASE_URL and skip locally (CI sets it and fails on any skip). Every name and id is fake.

CONTRACT
1. Authentication comes before the body is read. A request with a malformed JSON body answers 401
   without a session; with an admin session and a malformed or unknown student key it answers 404;
   only an admin with a real student gets 422. Same for the body routes of teacher.py (401).
2. PATCH keeps answering the same body as GET (medians included); only the way it is computed may
   change (the query count is left to a unit test).
3. The lab-level `attempts` is the sum of the `attempts` of the task rows shown: results of a task
   id that is no longer in lab.yaml are not counted.
"""

import pytest

from tests.acceptance.test_issue_164_student_page import (  # noqa: F401 (fixtures)
    SOME_KEY,
    _db,
    admin,
    env,
    lab_of,
    page,
    page_url,
)

BAD_JSON = {"content": b"{not json", "headers": {"Content-Type": "application/json"}}
GONE = "99-removed-from-lab-yaml"

BODY_ROUTES = [
    ("PATCH", f"/api/admin/students/{SOME_KEY}"),
    ("POST", "/api/admin/cohorts"),
    ("PATCH", "/api/admin/cohorts/1"),
    ("POST", "/api/admin/cohorts/1/import"),
]


# ============================================================ item 1: auth before the body


@pytest.mark.parametrize("method,path", BODY_ROUTES)
def test_anonymous_request_with_malformed_json_gets_401_not_422(method, path):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, base_url="https://testserver")  # no lifespan: no pool needed
    resp = client.request(method, path, **BAD_JSON)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("key", ["not-a-key", "0" * 64, SOME_KEY])
def test_admin_with_malformed_json_and_unknown_student_key_gets_404_not_422(env, admin, key):
    resp = admin.patch(page_url(key), **BAD_JSON)
    assert resp.status_code == 404, resp.text


def test_admin_with_malformed_json_and_a_real_student_gets_422(env, admin):
    s = env.student()
    resp = admin.patch(page_url(env.key(s)), **BAD_JSON)
    assert resp.status_code == 422, resp.text


def test_admin_with_a_valid_body_still_edits_the_student(env, admin):
    s = env.student()
    resp = admin.patch(page_url(env.key(s)), json={"note": "  hello  "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] == "hello"


# ============================================================ item 2: medians stay correct


def test_patch_answers_the_same_body_as_get_with_medians(env, admin):
    a, b = env.student(), env.student()
    env.result(a, "stu-med", "01-one", seconds=20)
    env.result(b, "stu-med", "01-one", seconds=60)
    key = env.key(a)
    resp = admin.patch(page_url(key), json={"note": "checked"})
    assert resp.status_code == 200, resp.text
    got = admin.get(page_url(key)).json()
    assert resp.json() == got
    task = next(t for t in lab_of(got, "stu-med")["tasks"] if t["id"] == "01-one")
    assert task["median_seconds"] is not None


def test_dismiss_answers_the_same_body_as_get_with_medians(env, admin):
    a, b = env.student(), env.student()
    env.result(a, "stu-med", "01-one", seconds=20, flag=True)
    env.result(b, "stu-med", "01-one", seconds=60)
    key = env.key(a)
    flag = lab_of(admin.get(page_url(key)).json(), "stu-med")["flags"][0]["id"]
    resp = admin.post(f"{page_url(key)}/flags/{flag}/dismiss")
    assert resp.status_code == 200, resp.text
    assert resp.json() == admin.get(page_url(key)).json()


# ============================================================ item 3: attempts of current tasks


def test_lab_attempts_ignore_tasks_removed_from_lab_yaml(env, admin):
    s = env.student()
    env.result(s, "stu-b", "01-one", seconds=30, attempts=2)
    env.result(s, "stu-b", GONE, seconds=30, attempts=5)
    lab = lab_of(page(env, admin, s), "stu-b")
    assert [t["id"] for t in lab["tasks"]] == ["01-one"]
    assert lab["attempts"] == 2


def test_lab_attempts_equal_the_sum_of_the_task_rows_shown(env, admin):
    s = env.student()
    env.result(s, "stu-a", "01-one", seconds=30, attempts=3)
    env.result(s, "stu-a", "03-three", seconds=None, attempts=4)
    env.result(s, "stu-a", GONE, seconds=10, attempts=9)
    lab = lab_of(page(env, admin, s), "stu-a")
    assert lab["attempts"] == sum(t["attempts"] for t in lab["tasks"]) == 7
