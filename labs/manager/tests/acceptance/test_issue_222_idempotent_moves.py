"""Acceptance tests for issue #222: Next/Skip are idempotent (split from #220, D70).

A Next/Skip that the server committed but answered 503 is retried by the student. Today the retry
is another relative move and skips one more task. This exam fixes the wire shape (the build must
follow it or change the exam through the manager):

  POST /api/sessions/{sid}/tasks/current/next   JSON body {"from": "<task id>"}
  POST /api/sessions/{sid}/tasks/current/skip   JSON body {"from": "<task id>"}

`from` is the id of the task the student is on when they press the button (the task they see).
The routes, the 200 task detail, the setup run and every other answer stay as in
test_issue_9_task_api.py. With `from` present, the server compares it with the session's current
task C and with the task N right after `from` in the lab's order:

  C == from     the move is not applied yet: it applies exactly like today (setup of N runs,
                shown_at recorded, current becomes N), 200 detail of N.
  C == N        the move was already applied (a retry): no-op. 200 detail of the current task,
                nothing runs (no setup, no exec of any kind), nothing is recorded again, the
                current task stays N. The answer is the same detail as GET tasks/current.
  anything else (stale: `from` is neither the current task nor the one before it, or is not a
                task of the lab) -> 409 {"detail": "task_changed"}, nothing runs, nothing moves.
                (The lab screen already re-reads tasks/current on that answer, #160.)
  `from` on the last task: C == from and no task follows -> 409 {"detail": "no_next_task"} as
                today. A repeat after the last task cannot exist, so it needs no rule.

`next` and `skip` behave the same. A request WITHOUT a body or without `from` keeps today's
behaviour (relative move): the older exams and any older client keep working. `previous` and
`restart` are not changed by this issue. A failed setup of the first move (502/504) leaves
current at `from`, so a retry with the same `from` applies it.

Database tests need LF_TEST_DATABASE_URL and skip locally without it (CI sets it and fails on any
skip). The fake lab, fake runtime and fixtures are those of test_issue_9_task_api.py.
"""
# ruff: noqa: F811
import pytest
from test_issue_9_task_api import (  # noqa: F401  (fixtures are imported on purpose)
    _db,
    current,
    env,
    show,
    tasks_url,
)

ACTIONS = ["next", "skip"]


def move(client, sid: str, action: str, frm=None):
    url = f"{tasks_url(sid)}/current/{action}"
    if frm is None:
        return client.post(url)
    return client.post(url, json={"from": frm})


def previous(client, sid: str):
    return client.post(f"{tasks_url(sid)}/current/previous")


@pytest.mark.parametrize("action", ACTIONS)
def test_first_move_with_from_applies_like_today(env, action):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    env.runtime.execs.clear()
    resp = move(client, sid, action, "02-files")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "03-net"
    assert env.runtime.setups() == ["03-net"]
    assert current(client, sid).json()["id"] == "03-net"
    assert env.shown_at(sid, "03-net") is not None


@pytest.mark.parametrize("action", ACTIONS)
def test_identical_repeat_is_a_noop_that_answers_the_current_task(env, action):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    first = move(client, sid, action, "02-files")
    assert first.status_code == 200, first.text
    env.runtime.execs.clear()
    shown = env.shown_at(sid, "03-net")
    again = move(client, sid, action, "02-files")
    assert again.status_code == 200, again.text
    assert again.json()["id"] == "03-net"
    assert again.json() == current(client, sid).json()
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "03-net"
    assert env.shown_at(sid, "03-net") == shown


@pytest.mark.parametrize("action", ACTIONS)
def test_many_repeats_never_move_past_the_target(env, action):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    for _ in range(4):
        resp = move(client, sid, action, "01-quiz")
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == "02-files"
    assert env.runtime.setups() == ["02-files"]
    assert current(client, sid).json()["id"] == "02-files"
    assert env.shown_at(sid, "03-net") is None


def test_a_repeat_across_next_and_skip_is_also_a_noop(env):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    assert move(client, sid, "next", "02-files").json()["id"] == "03-net"
    env.runtime.execs.clear()
    resp = move(client, sid, "skip", "02-files")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "03-net"
    assert env.runtime.execs == []


def test_going_back_makes_the_same_from_apply_again(env):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    move(client, sid, "next", "02-files")
    assert previous(client, sid).status_code == 200
    env.runtime.execs.clear()
    resp = move(client, sid, "next", "02-files")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "03-net"
    assert env.runtime.setups() == ["03-net"]


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("stale", ["01-quiz", "04-last", "no-such-task"])
def test_a_stale_from_is_409_task_changed_and_moves_nothing(env, action, stale):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    env.runtime.execs.clear()
    resp = move(client, sid, action, stale)
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "task_changed"}
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "03-net"


@pytest.mark.parametrize("action", ACTIONS)
def test_from_on_the_last_task_is_still_409_no_next_task(env, action):
    client, _, sid = env.owner()
    show(client, sid, "04-last")
    env.runtime.execs.clear()
    resp = move(client, sid, action, "04-last")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "no_next_task"}
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "04-last"


@pytest.mark.parametrize("action", ACTIONS)
def test_a_request_without_from_keeps_the_relative_move(env, action):
    client, _, sid = env.owner()
    show(client, sid, "02-files")
    assert move(client, sid, action).json()["id"] == "03-net"
    assert move(client, sid, action).json()["id"] == "04-last"
    assert current(client, sid).json()["id"] == "04-last"


def test_with_no_current_task_a_move_with_from_is_still_409_no_current_task(env):
    client, _, sid = env.owner()
    resp = move(client, sid, "next", "02-files")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "no_current_task"}
    assert env.runtime.execs == []


def test_a_failed_setup_keeps_current_so_the_retry_with_the_same_from_applies(env):
    from app.runtime import ExecResult

    client, _, sid = env.owner()
    show(client, sid, "02-files")
    env.runtime.outcome = ExecResult(1, "", False)
    assert move(client, sid, "next", "02-files").status_code == 502
    assert current(client, sid).json()["id"] == "02-files"
    env.runtime.outcome = None
    env.runtime.execs.clear()
    resp = move(client, sid, "next", "02-files")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "03-net"
    assert env.runtime.setups() == ["03-net"]
