"""Acceptance tests for issue #127: check parser and busy slug nits (review of #123, #88).

No database and no Docker: the parsing function `app.content.parse_check_output` is called
directly, and the check route function is driven with a scripted runtime (as the unit tests of
the task router do) so the verdict the student sees (`passed`) is observed.

  1. A stray `FAIL` line that is not `FAIL <n>` for an item of the checklist (a tool's stderr
     `FAIL: ...`, `FAIL 9` on a 3-item task) does not fail a check whose every item passed.
  2. Item numbers are ASCII digits only (`FAIL ١` is not item 1).
  3. A Next/Restart/Previous (the show path) while a check of the same session runs answers
     409 `session_busy`; two shows at once still answer 409 `setup_running`.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import tasks
from app.content import parse_check_output

ALL_PASS = "PASS 1\nPASS 2\nPASS 3\n"
THREE = [{"en": "a", "he": "b"}, {"en": "c", "he": "d"}, {"en": "e", "he": "f"}]


def _run_check_route(monkeypatch, output, exit_ok=True):
    task = SimpleNamespace(id="t1", checklist=THREE)

    async def gradable(ctx, task_id, kind):
        return task

    async def run_check(ctx, t):
        return None, output, exit_ok

    async def count(ctx, t, passed):
        return 1

    monkeypatch.setattr(tasks, "_gradable", gradable)
    monkeypatch.setattr(tasks, "_run_check", run_check)
    monkeypatch.setattr(tasks, "_count_attempt", count)
    return asyncio.run(tasks.check_task("t1", SimpleNamespace(sid=uuid.uuid4())))


# ---- criterion 1: stray FAIL lines ----------------------------------------


def test_stray_fail_colon_line_does_not_fail_a_check_whose_every_item_passed(monkeypatch):
    out = ALL_PASS + "FAIL: grep: /nope: No such file or directory\n"
    assert parse_check_output(out, 0).passed is True
    body = _run_check_route(monkeypatch, out)
    assert body["passed"] is True
    assert [i["passed"] for i in body["checklist"]] == [True, True, True]


def test_fail_for_an_item_outside_the_checklist_does_not_fail_a_check_that_passed(monkeypatch):
    out = ALL_PASS + "FAIL 9 not an item of this task\n"
    body = _run_check_route(monkeypatch, out)
    assert body["passed"] is True
    assert [i["passed"] for i in body["checklist"]] == [True, True, True]


def test_bare_fail_9_without_hint_does_not_fail_a_check_that_passed(monkeypatch):
    body = _run_check_route(monkeypatch, "PASS 1\nPASS 2\nFAIL 9\nPASS 3\n")
    assert body["passed"] is True


def test_a_real_item_failure_still_fails_the_check(monkeypatch):
    body = _run_check_route(monkeypatch, "PASS 1\nFAIL 2 file is missing\nPASS 3\n")
    assert body["passed"] is False
    assert [i["passed"] for i in body["checklist"]] == [True, False, True]
    assert body["checklist"][1]["hint"] == "file is missing"


def test_stray_fail_line_with_a_nonzero_exit_is_still_not_passed():
    assert parse_check_output(ALL_PASS + "FAIL: boom\n", 1).passed is False


# ---- criterion 2: ASCII digits only ---------------------------------------


def test_non_ascii_digit_is_not_an_item_number():
    r = parse_check_output("PASS 1\nPASS 2\nFAIL ١ sneaky\n", 0)
    assert 1 in r.items and r.items[1].passed is True
    assert r.items[1].hint is None
    assert set(r.items) == {1, 2}


def test_non_ascii_pass_line_does_not_pass_an_item():
    r = parse_check_output("PASS ١\n", 0)
    assert r.items == {}


def test_non_ascii_fail_line_does_not_fail_a_passing_check(monkeypatch):
    body = _run_check_route(monkeypatch, ALL_PASS + "FAIL ١\n")
    assert body["passed"] is True


# ---- criterion 3: busy slug ------------------------------------------------


def test_show_while_a_check_of_the_same_session_runs_is_409_session_busy(monkeypatch):
    sid = uuid.uuid4()
    started, release, shown = asyncio.Event(), asyncio.Event(), []
    task = SimpleNamespace(id="t1", checklist=THREE)

    async def gradable(ctx, task_id, kind):
        return task

    async def run_check(ctx, t):
        started.set()
        await release.wait()
        return None, ALL_PASS, True

    async def count(ctx, t, passed):
        return 1

    async def do_show(ctx, t):
        shown.append(t)
        return {"ok": True}

    monkeypatch.setattr(tasks, "_gradable", gradable)
    monkeypatch.setattr(tasks, "_run_check", run_check)
    monkeypatch.setattr(tasks, "_count_attempt", count)
    monkeypatch.setattr(tasks, "_do_show", do_show)
    ctx = SimpleNamespace(sid=sid)

    async def go():
        check = asyncio.create_task(tasks.check_task("t1", ctx))
        await started.wait()
        with pytest.raises(HTTPException) as err:
            await tasks._show(ctx, task)
        release.set()
        assert (await check)["passed"] is True
        return err.value

    exc = asyncio.run(go())
    assert exc.status_code == 409 and exc.detail == "session_busy"
    assert shown == []


def test_two_shows_at_once_still_answer_setup_running(monkeypatch):
    sid = uuid.uuid4()
    started, release = asyncio.Event(), asyncio.Event()

    async def do_show(ctx, t):
        started.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(tasks, "_do_show", do_show)
    ctx = SimpleNamespace(sid=sid)

    async def go():
        first = asyncio.create_task(tasks._show(ctx, "a"))
        await started.wait()
        with pytest.raises(HTTPException) as err:
            await tasks._show(ctx, "b")
        release.set()
        await first
        return err.value

    exc = asyncio.run(go())
    assert exc.status_code == 409 and exc.detail == "setup_running"
