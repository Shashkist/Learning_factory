import datetime as dt
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.content import Lab, Task
from app.student_page import (
    StudentEdit,
    assemble,
    clean_note,
    lab_flag_id,
    parse_flag_id,
    task_flag_id,
)

SID = uuid.UUID("5b0e2f6c-7a1d-4c53-9d58-0f1f1f6f9a11")
KEY = uuid.UUID("9c1d7e22-3b4a-4f6e-8a10-2d3c4b5a6e77")


def lab(lab_id="x", tasks=("a", "b", "c")):
    return Lab(
        id=lab_id,
        title={"en": "T", "he": "כ"},
        status="published",
        image="term",
        module=1,
        lesson=1,
        path=Path("."),
        tasks=[Task(id=t, kind="hands-on") for t in tasks],
    )


STUDENT = {
    "key": KEY,
    "name": "Fake Name",
    "cohort": "Fake cohort",
    "note": None,
    "disabled": False,
    "access_until": None,
    "cohort_until": dt.date(2027, 3, 1),
}


def flag(scope, lab_id, task_id=None, session=SID, dismissed=None, reason="why"):
    return {
        "scope": scope,
        "lab_id": lab_id,
        "session_id": session,
        "task_id": task_id,
        "fast_reason": reason,
        "fast_code": None,
        "fast_seconds": None,
        "fast_threshold": None,
        "flag_dismissed_at": dismissed,
    }


def test_flag_ids_round_trip():
    assert parse_flag_id(task_flag_id(SID, "01-one")) == ("task", SID, "01-one")
    assert parse_flag_id(task_flag_id(SID, "a.b-c")) == ("task", SID, "a.b-c")
    assert parse_flag_id(lab_flag_id(SID)) == ("lab", SID, None)


@pytest.mark.parametrize(
    "bad",
    ["", "x", "no-such-flag", "task-", f"task-{SID}", f"task-{SID}-", f"lab-{SID}-a", f"lab-{SID}x",
     f"lab-{str(SID).upper()}", "lab-not-a-uuid-at-all-0000000000000"],
)
def test_other_flag_ids_are_not_parsed(bad):
    assert parse_flag_id(bad) is None


def test_clean_note():
    assert clean_note("  front row  ") == "front row"
    assert clean_note("   ") is None
    assert clean_note("") is None
    assert clean_note(None) is None


def test_edit_only_marks_the_fields_sent():
    assert StudentEdit.model_validate({"disabled": True}).model_fields_set == {"disabled"}
    edit = StudentEdit.model_validate({"access_until": None, "note": None})
    assert edit.model_fields_set == {"access_until", "note"}
    assert edit.access_until is None and edit.note is None
    assert StudentEdit.model_validate({"access_until": "2027-01-31"}).access_until == dt.date(
        2027, 1, 31
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"disabled": "yes"},
        {"disabled": 1},
        {"disabled": None},
        {"note": 5},
        {"note": "a\x00b"},
        {"note": "x" * 1001},
        {"access_until": "2027-13-40"},
        {"access_until": "2027-1-5"},
        {"access_until": 20270101},
        {"access_until": "tomorrow"},
    ],
)
def test_edit_refuses_wrong_types_and_bad_dates(bad):
    with pytest.raises(ValidationError):
        StudentEdit.model_validate(bad)


def test_edit_refuses_a_body_that_is_not_an_object():
    for bad in (None, [], "note"):
        with pytest.raises(ValidationError):
            StudentEdit.model_validate(bad)


def test_assemble_counts_tasks_attempts_times_and_medians():
    tasks = [
        {"lab_id": "x", "task_id": "b", "attempts": 3, "passed": True},
        {"lab_id": "x", "task_id": "a", "attempts": 2, "passed": False},
        {"lab_id": "x", "task_id": "gone", "attempts": 4, "passed": True},  # not in lab.yaml
    ]
    firsts = [{"lab_id": "x", "task_id": "b", "secs": 41.04}]
    medians = [{"lab_id": "x", "task_id": "b", "median": 25.0}]
    body = assemble([lab()], STUDENT, tasks, firsts, medians, [], [])
    (x,) = body["labs"]
    assert (x["status"], x["tasks_passed"], x["tasks_total"], x["attempts"]) == ("1/3", 1, 3, 5) 
    assert [t["id"] for t in x["tasks"]] == ["a", "b", "c"]
    a, b, c = x["tasks"]
    assert a == {"id": "a", "passed": False, "attempts": 2, "seconds": None, "median_seconds": None}
    assert b == {"id": "b", "passed": True, "attempts": 3, "seconds": 41.0, "median_seconds": 25.0}
    assert c["attempts"] == 0 and c["passed"] is False
    assert x["flags"] == []


def test_assemble_student_fields_carry_the_key_and_no_hash():
    student = dict(STUDENT, id_hash="f" * 64, access_until=dt.date(2027, 5, 20), note="n")
    body = assemble([], student, [], [], [], [], [])
    assert body == {
        "key": str(KEY),
        "name": "Fake Name",
        "cohort": "Fake cohort",
        "note": "n",
        "disabled": False,
        "access_until": "2027-05-20",
        "cohort_access_until": "2027-03-01",
        "labs": [],
    }


def test_assemble_done_and_untouched_labs():
    body = assemble([lab("x"), lab("y")], STUDENT, [], [], [], [{"lab_id": "x"}], [])
    assert [(x["id"], x["status"]) for x in body["labs"]] == [("x", "done"), ("y", "none")]


def test_assemble_flags_task_order_then_lab_and_dismissed_timestamp():
    other = uuid.UUID("11111111-2222-4333-8444-555555555555")
    when = dt.datetime(2026, 11, 18, 10, 0, tzinfo=dt.UTC)
    flags = [
        flag("lab", "x"),
        flag("task", "x", "c", session=other, dismissed=when),
        flag("task", "x", "a", reason=None),
        flag("task", "y", "a"),
    ]
    body = assemble([lab("x")], STUDENT, [], [], [], [], flags)
    got = body["labs"][0]["flags"]
    assert [f["id"] for f in got] == [f"task-{SID}-a", f"task-{other}-c", f"lab-{SID}"]
    assert got[0] == {
        "id": f"task-{SID}-a", "scope": "task", "task_id": "a", "reason": "",
        "reason_code": None, "seconds": None, "threshold": None, "dismissed_at": None,
    }
    assert got[1]["dismissed_at"] == "2026-11-18T10:00:00+00:00"
    assert got[2]["scope"] == "lab" and got[2]["task_id"] is None


# ---------------------------------------------------------------- routes, with a fake pool


class FakePool:
    """Records every query; knows one student (by key) and no rows otherwise."""

    def __init__(self):
        self.calls: list[str] = []

    async def fetchrow(self, sql, *args):
        self.calls.append(sql)
        if args and args[0] == KEY:
            return dict(STUDENT, id_hash="h")
        return None

    async def fetch(self, sql, *args):
        self.calls.append(sql)
        return []

    async def fetchval(self, sql, *args):
        self.calls.append(sql)
        return None

    async def execute(self, sql, *args):
        self.calls.append(sql)


@pytest.fixture
def api():
    from fastapi.testclient import TestClient

    from app.auth import Principal, current_admin
    from app.main import app

    pool = FakePool()
    app.dependency_overrides[current_admin] = lambda: Principal("admin", "a", "a")
    saved = (getattr(app.state, "pool", None), getattr(app.state, "labs", None))
    app.state.pool, app.state.labs = pool, {"x": lab()}
    try:
        yield TestClient(app, base_url="https://testserver"), pool
    finally:
        app.dependency_overrides.pop(current_admin, None)
        app.state.pool, app.state.labs = saved


@pytest.mark.parametrize("bad", ["abc", "0" * 64, str(KEY).upper(), "%00"])
def test_a_malformed_key_is_a_404_without_a_query(api, bad):
    client, pool = api
    assert client.get(f"/api/admin/students/{bad}").status_code == 404
    assert client.patch(f"/api/admin/students/{bad}", json={"note": 5}).status_code == 404
    assert client.post(f"/api/admin/students/{bad}/flags/x/dismiss").status_code == 404
    assert pool.calls == []


def test_an_unknown_key_beats_a_bad_body(api):
    client, _ = api
    other = "11111111-2222-4333-8444-555555555555"
    assert client.patch(f"/api/admin/students/{other}", json={"disabled": "yes"}).status_code == 404


def test_a_bad_body_for_a_known_key_is_a_422_and_writes_nothing(api):
    client, pool = api
    resp = client.patch(f"/api/admin/students/{KEY}", json={"disabled": "yes"})
    assert resp.status_code == 422
    assert not any(sql.lstrip().startswith("update") for sql in pool.calls)


def test_the_page_answers_with_the_published_labs(api):
    client, _ = api
    body = client.get(f"/api/admin/students/{KEY}").json()
    assert body["key"] == str(KEY) and [x["id"] for x in body["labs"]] == ["x"]
    assert "h" not in body.values()


def test_an_unknown_flag_is_a_404(api):
    client, _ = api
    assert client.post(f"/api/admin/students/{KEY}/flags/x/dismiss").status_code == 404
    flag_path = f"/api/admin/students/{KEY}/flags/{lab_flag_id(SID)}/dismiss"
    assert client.post(flag_path).status_code == 404  # fetchval finds no row of this student


def test_a_patch_updates_only_the_fields_sent(api):
    client, pool = api
    writes = []

    async def execute(sql, *args):
        writes.append((sql, args))

    pool.execute = execute
    url = f"/api/admin/students/{KEY}"
    assert client.patch(url, json={"note": "  x  ", "disabled": True}).status_code == 200
    assert client.patch(url, json={"access_until": None}).status_code == 200
    assert client.patch(url, json={"note": "   "}).status_code == 200
    assert client.patch(url, json={}).status_code == 200
    assert writes == [
        ("update learning_factory.students set note = $2, disabled = $3 where id_hash = $1",
         ("h", "x", True)),
        ("update learning_factory.students set access_until = $2 where id_hash = $1", ("h", None)),
        ("update learning_factory.students set note = $2 where id_hash = $1", ("h", None)),
    ]


@pytest.mark.parametrize(
    "headers",
    [{"Content-Type": "text/plain"}, {"Content-Type": "application/x-www-form-urlencoded"}, {}],
)
def test_cohort_post_needs_a_json_content_type(api, headers):
    client, pool = api
    resp = client.post("/api/admin/cohorts", content=b'{"name": "x"}', headers=headers)
    assert resp.status_code == 422
    assert pool.calls == []
    assert "input" not in resp.text


def test_json_variants_pass_the_content_type_check_and_errors_are_under_body(api):
    client, _ = api
    for ctype in ("application/json; charset=utf-8", "application/merge-patch+json"):
        resp = client.post("/api/admin/cohorts", content=b"{}", headers={"Content-Type": ctype})
        assert resp.status_code == 422
        assert all(e["loc"][0] == "body" and len(e["loc"]) == 2 for e in resp.json()["detail"])


def test_deeply_nested_json_is_a_422(api):
    client, _ = api
    resp = client.post(
        "/api/admin/cohorts",
        content=b"[" * 100000 + b"]" * 100000,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
