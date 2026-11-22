"""Unit tests for the smoke tasks client (#128): stale prompts and the quiz allow-list."""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
def _load_smoke_tasks(name):
    """Load labs/deploy/smoke/tasks.py by path under a unique name, without touching sys.path.

    The module imports its siblings `lab_api` and `terminal` by bare name, so they are loaded by
    path too and held in sys.modules only while it executes; nothing is left behind.
    """
    smoke_dir = ROOT / "labs" / "deploy" / "smoke"

    def load(mod_name, file_name):
        spec = importlib.util.spec_from_file_location(mod_name, smoke_dir / file_name)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    saved = {n: sys.modules.get(n) for n in ("lab_api", "terminal", name)}
    try:
        load("lab_api", "lab_api.py")
        load("terminal", "terminal.py")
        return load(name, "tasks.py")
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


tasks = _load_smoke_tasks("lf_deploy_smoke_tasks_128")


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
