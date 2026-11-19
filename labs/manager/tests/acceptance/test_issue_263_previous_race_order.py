"""Acceptance tests for issue #263: two ordering cases the #255 exam did not force (D75, D78).

The code is right today; these cases keep it so. Both force an interleaving instead of hoping
for it (events, never sleeps as the only guarantee).

  1. The race no-op. Request A (Previous, from 03-net) is inside its setup. Request B (the same
     Previous) has read the current task (still 03-net, A has not committed) and only then asks
     for the per-session lock, after A released it. B's target is now the current task: the move
     was applied, so B answers the no-op 200 with 02-files, never 409 `task_changed` (D75).
     Both answer 200 with 02-files; the setup runs once; nothing moves back.
  2. Forced overlap. The slowed setup of the first request does not finish until the other
     request has returned. So the two requests overlap for certain: exactly one answers 200 with
     02-files and the other 409 `setup_running` (the lock is never waited for); one setup run.

Same fixtures as the #9, #222, #251 and #255 exams (engine lab: 01-quiz, 02-files, 03-net, 04-last).
Database tests need LF_TEST_DATABASE_URL and skip locally without it (CI fails on any skip).
"""
# ruff: noqa: F811
import asyncio
import concurrent.futures
import threading

from test_issue_9_task_api import (  # noqa: F401  (fixtures are imported on purpose)
    _db,
    current,
    env,
    show,
    tasks_url,
)

WAIT_S = 10  # a safety net so a broken ordering fails the case instead of hanging it


def previous(client, sid: str, frm: str):
    return client.post(f"{tasks_url(sid)}/current/previous", json={"from": frm})


async def _wait(event: threading.Event) -> bool:
    return await asyncio.to_thread(event.wait, WAIT_S)


def test_b_reads_before_a_commits_and_locks_after_a_releases_answers_the_noop(env, monkeypatch):
    import app.tasks as tasks

    client, _, sid = env.owner()
    show(client, sid, "03-net")
    env.runtime.execs.clear()
    a_in_setup = threading.Event()  # A holds the lock, inside its setup
    b_has_read = threading.Event()  # B holds its view of the session: still on 03-net
    a_done = threading.Event()  # A has committed and released the lock
    seen: dict = {}
    real_exec = env.runtime.exec_script
    real_show = tasks._show
    calls: list[int] = []

    async def exec_script(*args, **kwargs):
        a_in_setup.set()
        seen["b_read_in_time"] = await _wait(b_has_read)
        return await real_exec(*args, **kwargs)

    async def show_in_order(ctx, task, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:  # request A
            try:
                return await real_show(ctx, task, *args, **kwargs)
            finally:
                a_done.set()
        seen["b_ctx_current"] = ctx.current  # request B: what it read before A committed
        b_has_read.set()
        seen["a_done_in_time"] = await _wait(a_done)
        return await real_show(ctx, task, *args, **kwargs)

    monkeypatch.setattr(env.runtime, "exec_script", exec_script)
    monkeypatch.setattr(tasks, "_show", show_in_order)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(previous, client, sid, "03-net")
        assert a_in_setup.wait(WAIT_S), "request A never reached its setup"
        second = pool.submit(previous, client, sid, "03-net")
        results = [first.result(WAIT_S * 3), second.result(WAIT_S * 3)]
    assert seen == {
        "b_read_in_time": True,
        "b_ctx_current": "03-net",
        "a_done_in_time": True,
    }, seen
    for resp in results:
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == "02-files", resp.text
    assert env.runtime.setups() == ["02-files"]
    assert current(client, sid).json()["id"] == "02-files"
    assert env.shown_at(sid, "02-files") is not None
    assert env.shown_at(sid, "01-quiz") is None


def test_forced_overlap_of_two_previous_is_one_200_and_one_409_setup_running(env, monkeypatch):
    client, _, sid = env.owner()
    show(client, sid, "03-net")
    env.runtime.execs.clear()
    in_setup = threading.Event()  # one request holds the lock, inside its setup
    other_returned = threading.Event()  # the other request has got its answer
    seen: dict = {}
    real_exec = env.runtime.exec_script

    async def exec_script(*args, **kwargs):
        in_setup.set()
        seen["overlapped"] = await _wait(other_returned)  # the setup ends after the loser answered
        return await real_exec(*args, **kwargs)

    def request(_):
        resp = previous(client, sid, "03-net")
        if resp.status_code != 200:
            other_returned.set()
        return resp

    monkeypatch.setattr(env.runtime, "exec_script", exec_script)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(request, 0)
        assert in_setup.wait(WAIT_S), "no request reached its setup"
        second = pool.submit(request, 1)
        results = [first.result(WAIT_S * 3), second.result(WAIT_S * 3)]
    assert seen == {"overlapped": True}, seen
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409], [r.text for r in results]
    winner = next(r for r in results if r.status_code == 200)
    loser = next(r for r in results if r.status_code == 409)
    assert winner.json()["id"] == "02-files", winner.text
    assert loser.json() == {"detail": "setup_running"}, loser.text
    assert env.runtime.setups() == ["02-files"]
    assert current(client, sid).json()["id"] == "02-files"
    assert env.shown_at(sid, "01-quiz") is None
