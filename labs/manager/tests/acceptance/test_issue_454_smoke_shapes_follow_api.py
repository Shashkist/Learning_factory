"""Acceptance tests for issue #454: the smoke allow-lists follow the real task API.

Pure Python (no Postgres, no Docker): the real `app.tasks._detail` builds the quiz and task
details from the real `smoke-tasks` lab; the real `check_task` and `answer_task` handlers build the
check and answer responses with only the DB/container helpers (`_gradable`, `_run_check`,
`_count_attempt`) replaced by fakes. The smoke module is `labs/deploy/smoke/tasks.py`.
"""
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
sys.path.insert(0, str(ROOT / "labs" / "deploy" / "smoke"))
import tasks as smoke  # noqa: E402

from app import tasks as api  # noqa: E402
from app.content import load_labs  # noqa: E402

CONTENT = ROOT / "labs" / "content"


@pytest.fixture(scope="module")
def lab():
    result = load_labs(CONTENT)
    assert "smoke-tasks" in result.labs, result.errors
    return result.labs["smoke-tasks"]


@pytest.fixture
def quiz(lab):
    return next(t for t in lab.tasks if t.id == smoke.QUIZ)


@pytest.fixture
def hands_on(lab):
    return next(t for t in lab.tasks if t.id == smoke.HANDS_ON)


def _row(**kw):
    base = {"shown_at": "t", "passed_at": None, "attempts": 0}
    return {**base, **kw}


QUIZ_ROWS = [None, _row(), _row(attempts=1), _row(attempts=3, passed_at="t")]


@pytest.mark.parametrize("row", QUIZ_ROWS)
def test_the_real_quiz_detail_keys_are_within_the_smoke_quiz_keys(lab, quiz, row):
    assert set(api._detail(lab, quiz, row)) <= smoke.QUIZ_KEYS


@pytest.mark.parametrize("row", QUIZ_ROWS)
def test_the_smoke_quiz_shape_accepts_the_real_quiz_detail(lab, quiz, row):
    smoke._quiz_shape(api._detail(lab, quiz, row))


def _check_response(lab, task, passed, attempts=1):
    async def gradable(ctx, task_id, kind):
        return task

    async def run_check(ctx, t):
        return None, ("PASS 1\n" if passed else "FAIL 1 touch ~/smoke-ok\n"), passed

    async def count(ctx, t, ok):
        return attempts

    ctx = SimpleNamespace(sid=uuid.uuid4(), lab=lab)
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(api, "_gradable", gradable)
        mp.setattr(api, "_run_check", run_check)
        mp.setattr(api, "_count_attempt", count)
        return asyncio.run(api.check_task(task.id, ctx))
    finally:
        mp.undo()


@pytest.mark.parametrize("passed", [False, True])
def test_the_real_check_response_keys_are_within_the_smoke_check_keys(lab, hands_on, passed):
    res = _check_response(lab, hands_on, passed)
    assert res and set(res) <= smoke.CHECK_KEYS
    smoke._contract("check", res, smoke.CHECK_KEYS)


@pytest.mark.parametrize("correct", [0, 1])
def test_the_real_answer_response_keys_are_within_the_smoke_answer_keys(lab, quiz, correct):
    async def gradable(ctx, task_id, kind):
        return quiz

    async def count(ctx, t, ok):
        return 1

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(api, "_gradable", gradable)
        mp.setattr(api, "_count_attempt", count)
        res = asyncio.run(api.answer_task(quiz.id, api._Answer(option=correct), SimpleNamespace()))
    finally:
        mp.undo()
    assert res and set(res) <= smoke.ANSWER_KEYS
    smoke._contract("answer", res, smoke.ANSWER_KEYS)


def test_a_key_added_to_the_detail_makes_the_subset_check_fail(lab, quiz, monkeypatch):
    """Mutation guard: the subset check is not vacuous."""
    real = api._detail
    monkeypatch.setattr(api, "_detail", lambda *a: {**real(*a), "extra_field": 1})
    detail = api._detail(lab, quiz, None)
    assert not set(detail) <= smoke.QUIZ_KEYS
    with pytest.raises(smoke.StepFail):
        smoke._quiz_shape(detail)


def test_a_key_added_to_the_check_response_fails_the_contract(lab, hands_on):
    res = {**_check_response(lab, hands_on, True), "extra_field": 1}
    with pytest.raises(smoke.StepFail):
        smoke._contract("check", res, smoke.CHECK_KEYS)


def test_a_quiz_without_solution_available_fails_at_quiz_show(lab, quiz):
    detail = api._detail(lab, quiz, None)
    del detail["solution_available"]
    with pytest.raises(smoke.StepFail) as exc:
        smoke._quiz_shape(detail)
    assert exc.value.step == "quiz-show"


def test_a_quiz_with_solution_available_true_fails_at_quiz_show(lab, quiz):
    detail = {**api._detail(lab, quiz, None), "solution_available": True}
    with pytest.raises(smoke.StepFail) as exc:
        smoke._quiz_shape(detail)
    assert exc.value.step == "quiz-show"
