"""Unit tests for the smoke tasks client (#128): stale prompts and the quiz allow-list."""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
sys.path.insert(0, str(ROOT / "labs" / "deploy" / "smoke"))
import tasks  # noqa: E402


class FakeWs:
    def __init__(self, frames):
        self.frames = list(frames)
        self.taken = 0

    async def recv(self):
        if not self.frames:
            await asyncio.sleep(10)
        self.taken += 1
        return self.frames.pop(0)


PROMPT = b"0student@lab:~$ "


def test_the_fix_step_does_not_return_on_a_stale_prompt():
    ws = FakeWs([PROMPT])
    with pytest.raises(tasks.StepFail):
        asyncio.run(tasks._wait(ws, tasks._fix_done, 0.3, "fix", "no prompt"))


def test_the_fix_step_waits_for_the_echo_and_the_prompt_after_it():
    ws = FakeWs([PROMPT, b"0touch ~/smoke-ok\r\n", PROMPT])
    asyncio.run(tasks._wait(ws, tasks._fix_done, 2, "fix", "no prompt"))
    assert ws.taken == 3


def test_the_hi_step_consumes_the_prompt_after_hi():
    ws = FakeWs([b"0echo hi\r\n", b"0\r\nhi\r\n", PROMPT])
    asyncio.run(tasks._wait(ws, tasks._hi_then_prompt, 2, "terminal", "no hi"))
    assert ws.taken == 3


GOOD = {
    "id": "01-quiz", "index": 1, "total": 2, "kind": "quiz", "title": {"en": "Q", "he": "ש"},
    "state": "shown", "question": {"en": "?", "he": "?"},
    "options": [{"en": "a", "he": "a"}, {"en": "b", "he": "b"}], "solution_available": False,
}


def test_a_clean_quiz_detail_passes():
    tasks._quiz_shape(GOOD)


@pytest.mark.parametrize("bad", [
    {**GOOD, "options": [{"en": "a", "he": "a", "correct": True}, {"en": "b", "he": "b"}]},
    {**GOOD, "correct": 1},
    {**GOOD, "extra": 1},
    {**GOOD, "question": {"en": "?", "he": "?", "answer": 1}},
    {**GOOD, "solution_available": True},
    {k: v for k, v in GOOD.items() if k != "solution_available"},
])
def test_any_extra_key_in_the_quiz_detail_fails(bad):
    with pytest.raises(tasks.StepFail) as exc:
        tasks._quiz_shape(bad)
    assert exc.value.step == "quiz-show"
