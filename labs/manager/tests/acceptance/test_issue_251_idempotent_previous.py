"""Acceptance tests for issue #251: Previous is idempotent too (review of #222, D75).

Same wire shape as Next/Skip (test_issue_222_idempotent_moves.py):

  POST /api/sessions/{sid}/tasks/current/previous   JSON body {"from": "<task id>"}

`from` is the id of the task the student is on when they press Previous. With `from` present, the
server compares it with the session's current task C and with the task P right BEFORE `from`:

  C == from     the move is not applied yet: it applies as today (setup of P runs, shown_at
                recorded, current becomes P), 200 detail of P.
  C == P        the move was already applied (a retry): no-op. 200 detail of the current task
                (same as GET tasks/current), nothing runs, nothing is recorded again.
  anything else -> 409 {"detail": "task_changed"}, nothing runs, nothing moves.
  `from` on the first task: C == from and nothing precedes -> 409 {"detail": "no_previous_task"}.

No body, or a body without `from`: today's relative move. A body with `from_` (the Python
attribute name, not the wire name) is NOT read as `from`: unknown fields are ignored as in #222, so
it behaves as a request without `from` (a relative move). Restart is unchanged.

Database tests need LF_TEST_DATABASE_URL and skip locally without it (CI fails on any skip).
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


def back(client, sid: str, frm=None, raw=None):
    url = f"{tasks_url(sid)}/current/previous"
    if raw is not None:
        return client.post(url, json=raw)
    if frm is None:
        return client.post(url)
    return client.post(url, json={"from": frm})


def test_first_previous_with_from_applies_like_today(env):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    env.runtime.execs.clear()
    resp = back(client, sid, "03-net")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "02-files"
    assert env.runtime.setups() == ["02-files"]
    assert current(client, sid).json()["id"] == "02-files"
    assert env.shown_at(sid, "02-files") is not None


def test_identical_repeat_is_a_noop_that_answers_the_current_task(env):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    assert back(client, sid, "03-net").status_code == 200
    env.runtime.execs.clear()
    shown = env.shown_at(sid, "02-files")
    again = back(client, sid, "03-net")
    assert again.status_code == 200, again.text
    assert again.json()["id"] == "02-files"
    assert again.json() == current(client, sid).json()
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "02-files"
    assert env.shown_at(sid, "02-files") == shown


def test_many_repeats_never_move_back_past_the_target(env):
    client, _, sid = env.owner()
    show(client, sid, "04-last")
    for _ in range(4):
        resp = back(client, sid, "04-last")
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == "03-net"
    assert env.runtime.setups() == ["03-net"]
    assert current(client, sid).json()["id"] == "03-net"
    assert env.shown_at(sid, "02-files") is None


def test_going_forward_again_makes_the_same_from_apply_again(env):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    back(client, sid, "03-net")
    assert client.post(f"{tasks_url(sid)}/current/next").status_code == 200
    env.runtime.execs.clear()
    resp = back(client, sid, "03-net")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "02-files"
    assert env.runtime.setups() == ["02-files"]


@pytest.mark.parametrize("stale", ["01-quiz", "02-files", "no-such-task"])
def test_a_stale_from_is_409_task_changed_and_moves_nothing(env, stale):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    env.runtime.execs.clear()
    resp = back(client, sid, stale)
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "task_changed"}
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "03-net"


def test_from_on_the_first_task_is_still_409_no_previous_task(env):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    env.runtime.execs.clear()
    resp = back(client, sid, "01-quiz")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "no_previous_task"}
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "01-quiz"


def test_a_request_without_from_keeps_the_relative_move(env):
    client, _, sid = env.owner()
    show(client, sid, "04-last")
    assert back(client, sid).json()["id"] == "03-net"
    assert back(client, sid).json()["id"] == "02-files"
    assert current(client, sid).json()["id"] == "02-files"


def test_an_empty_body_keeps_the_relative_move(env):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    assert back(client, sid, raw={}).json()["id"] == "02-files"


def test_with_no_current_task_a_previous_with_from_is_still_409_no_current_task(env):
    client, _, sid = env.owner()
    resp = back(client, sid, "03-net")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "no_current_task"}
    assert env.runtime.execs == []


def test_a_failed_setup_keeps_current_so_the_retry_with_the_same_from_applies(env):
    from app.runtime import ExecResult

    client, _, sid = env.owner()
    show(client, sid, "03-net")
    env.runtime.outcome = ExecResult(1, "", False)
    assert back(client, sid, "03-net").status_code == 502
    assert current(client, sid).json()["id"] == "03-net"
    env.runtime.outcome = None
    env.runtime.execs.clear()
    resp = back(client, sid, "03-net")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "02-files"
    assert env.runtime.setups() == ["02-files"]


def test_a_from_underscore_body_is_not_read_as_from_for_previous(env):
    # Read as `from`, "04-last" would make current (03-net) the already-applied target: a no-op.
    # Ignored, it is a relative move back to 02-files.
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    resp = back(client, sid, raw={"from_": "04-last"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "02-files"
    assert current(client, sid).json()["id"] == "02-files"


def test_a_from_underscore_body_is_not_read_as_from_for_next(env):
    # Read as `from`, "02-files" would make current (03-net) the applied target: a no-op.
    # Ignored, it is a relative move on to 04-last.
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    resp = client.post(f"{tasks_url(sid)}/current/next", json={"from_": "02-files"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "04-last"
    assert current(client, sid).json()["id"] == "04-last"
