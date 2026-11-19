"""Acceptance tests for issue #255: three Previous cases the #251 exam did not pin (D75, D78).

The code handles all three already; these cases keep it so.

  1. Restart ignores a `{"from": ...}` body: it behaves as without a body (the current task's
     setup runs again, 200 with the current task), whatever `from` says, even a stale one.
  2. A Previous retry whose target is the first task: the session is on 01-quiz, the body is
     {"from": "02-files"} -> 200 with 01-quiz, nothing runs, nothing moves (a no-op, not 409).
  2b. The -1 "unknown task" marker at that boundary: on 01-quiz, Previous or Next with a `from`
     that is no task of the lab is 409 `task_changed` (D75), never the retry no-op.
  3. Two Previous requests with the same `from` at once: the move is applied once. The per-session
     lock is never waited for, so the loser answers either the no-op 200 (the move had been
     applied) or 409 `setup_running` (the lock was held); never 409 `task_changed`, never a
     second move back (D75, amended for #255). The setup is slowed so the two requests overlap.

Same fixtures as the #9, #222 and #251 exams (engine lab: 01-quiz, 02-files, 03-net, 04-last).
Database tests need LF_TEST_DATABASE_URL and skip locally without it (CI fails on any skip).
"""
# ruff: noqa: F811
import asyncio
import concurrent.futures

import pytest
from test_issue_9_task_api import (  # noqa: F401  (fixtures are imported on purpose)
    _db,
    current,
    env,
    show,
    tasks_url,
)


def move(client, sid: str, action: str, frm=None):
    url = f"{tasks_url(sid)}/current/{action}"
    if frm is None:
        return client.post(url)
    return client.post(url, json={"from": frm})


# ------------------------------------------------------------------ 1. Restart with a body


@pytest.mark.parametrize("frm", ["01-quiz", "04-last", "no-such-task", "03-net"])
def test_restart_with_a_from_body_ignores_it_and_runs_the_current_setup_again(env, frm):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    first = env.shown_at(sid, "03-net")
    env.runtime.execs.clear()
    resp = move(client, sid, "restart", frm)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "03-net"
    assert env.runtime.setups() == ["03-net"]
    assert current(client, sid).json()["id"] == "03-net"
    assert env.shown_at(sid, "03-net") == first


# ------------------------------------------------------------------ 2. retry onto the first task


def test_a_previous_retry_whose_target_is_the_first_task_is_a_noop(env):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    env.runtime.execs.clear()
    shown = env.shown_at(sid, "01-quiz")
    resp = move(client, sid, "previous", "02-files")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "01-quiz"
    assert resp.json() == current(client, sid).json()
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "01-quiz"
    assert env.shown_at(sid, "01-quiz") == shown
    assert env.shown_at(sid, "02-files") is None


def test_that_retry_repeated_still_never_moves_or_runs_anything(env):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    env.runtime.execs.clear()
    for _ in range(3):
        resp = move(client, sid, "previous", "02-files")
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == "01-quiz"
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "01-quiz"


@pytest.mark.parametrize("action", ["previous", "next"])
def test_an_unknown_from_on_the_first_task_is_409_task_changed_not_a_retry(env, action):
    client, _, sid = env.owner()
    show(client, sid, "01-quiz")
    env.runtime.execs.clear()
    shown = env.shown_at(sid, "01-quiz")
    resp = move(client, sid, action, "no-such-task")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {"detail": "task_changed"}
    assert env.runtime.execs == []
    assert current(client, sid).json()["id"] == "01-quiz"
    assert env.shown_at(sid, "01-quiz") == shown
    assert env.shown_at(sid, "02-files") is None


# ------------------------------------------------------------------ 3. concurrent double Previous


def test_two_concurrent_previous_with_the_same_from_move_back_once(env, monkeypatch):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    env.runtime.execs.clear()
    fast = env.runtime.exec_script

    async def slow_exec_script(*args, **kwargs):
        await asyncio.sleep(0.3)  # hold the move open so the second request overlaps it
        return await fast(*args, **kwargs)

    monkeypatch.setattr(env.runtime, "exec_script", slow_exec_script)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: move(client, sid, "previous", "03-net"), range(2)))
    for resp in results:
        if resp.status_code == 200:
            assert resp.json()["id"] == "02-files", resp.text
        else:
            assert resp.status_code == 409, resp.text
            assert resp.json() == {"detail": "setup_running"}, resp.text
    assert any(r.status_code == 200 for r in results), [r.text for r in results]
    assert env.runtime.setups() == ["02-files"]
    assert current(client, sid).json()["id"] == "02-files"
    assert env.shown_at(sid, "02-files") is not None
    assert env.shown_at(sid, "01-quiz") is None
    # the retry of the loser, once the dust has settled, is the plain no-op
    env.runtime.execs.clear()
    again = move(client, sid, "previous", "03-net")
    assert again.status_code == 200, again.text
    assert again.json()["id"] == "02-files"
    assert env.runtime.execs == []
