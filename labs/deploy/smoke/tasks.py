"""Live task-API smoke client (issue #128, decisions D36, D54).

Runs right after terminal.py with the same environment. Logs in as the synthetic smoke student,
starts the internal lab `smoke-tasks` and drives the task API through the public URL: terminal,
show, check (fails), fix in the terminal, check (passes), quiz show, wrong answer, right answer,
end. Prints one `ok <step>` line per step.

    SMOKE_ID=... SMOKE_PASSWORD=... [SMOKE_TIMEOUT=60] python tasks.py [BASE_URL]

On failure exits 1 with `smoke failed: <step>: <reason>` on stderr; the session is always ended.
Never prints the password, the cookie, the student id or a traceback.

A SIGTERM before the stop handler is installed (during login and start, up to the first step)
kills the process at once: a session the server already started is left to the janitor (its id
may not be known yet, so it cannot be ended).
"""
import asyncio
import json
import os
import sys
from typing import Any

from lab_api import (
    STOP_STEP,
    Fail,
    leave,
    login,
    run_then_end,
    start,
)
from lab_api import request as _request
from terminal import HI_LINE, _open, _output

DEFAULT_BASE = "https://labs.shashkist.com"
LAB_ID = "smoke-tasks"
QUIZ, HANDS_ON = "01-quiz", "02-touch-file"
CHECK_KEYS = {"passed", "attempts", "reason", "checklist"}
ANSWER_KEYS = {"correct", "attempts"}


QUIZ_KEYS = {"id", "index", "total", "kind", "title", "state", "question", "options"}
LANG_KEYS = {"en", "he"}


class StepFail(Fail):
    def __init__(self, step: str, reason: str) -> None:
        super().__init__(reason)
        self.step = step


def _json(raw: bytes, step: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise StepFail(step, "the answer is not JSON") from None
    if not isinstance(data, dict):
        raise StepFail(step, "the answer is not a JSON object")
    return data


async def _post(step: str, base: str, path: str, cookie: str, body: dict | None = None):
    try:
        status, _, raw = await asyncio.to_thread(_request, base, "POST", path, cookie, body)
    except Fail as exc:
        raise StepFail(step, str(exc)) from None
    if status != 200:
        raise StepFail(step, f"http {status}")
    return _json(raw, step)


async def _wait(ws: Any, until: Any, timeout: float, step: str, what: str) -> None:
    buf = ""
    try:
        async with asyncio.timeout(timeout):
            while not until(buf):
                buf += _output(await ws.recv())
    except TimeoutError:
        raise StepFail(step, what) from None
    except Exception as exc:  # noqa: BLE001
        raise StepFail(step, f"terminal websocket failed ({type(exc).__name__})") from None


def _prompt(buf: str) -> bool:
    return buf.endswith("$ ")


def _hi_then_prompt(buf: str) -> bool:
    """`hi` seen and the prompt that follows it consumed, so no stale prompt stays queued."""
    return HI_LINE.search(buf) is not None and _prompt(buf)


def _fix_done(buf: str) -> bool:
    """The typed command was echoed (`smoke-ok`) and a prompt came after that echo."""
    i = buf.find("smoke-ok")
    return i >= 0 and _prompt(buf[i:])


async def _type(ws: Any, cmd: str) -> None:
    await ws.send(b"0" + cmd.encode() + b"\r")


def _ok(step: str) -> None:
    print(f"ok {step}", flush=True)


async def _steps(base: str, sid: str, cookie: str, timeout: float) -> None:
    url = ("wss" if base.startswith("https") else "ws") + base[base.index("://") :]
    task = f"/api/sessions/{sid}/tasks"
    try:
        ws = await _open(f"{url}/s/{sid}/term/ws", cookie, timeout)
    except Exception as exc:  # noqa: BLE001
        raise StepFail("terminal", f"terminal websocket failed ({type(exc).__name__})") from None
    async with ws:
        await ws.send(json.dumps({"AuthToken": "", "columns": 80, "rows": 24}).encode())
        await _wait(ws, _prompt, timeout, "terminal", "no shell prompt in the terminal")
        await _type(ws, "echo hi")
        await _wait(
            ws, _hi_then_prompt, timeout, "terminal", "the terminal never answered hi to echo hi"
        )
        _ok("terminal")

        await _post("show", base, f"{task}/{HANDS_ON}/show", cookie)
        _ok("show")

        res = await _post("check-fail", base, f"{task}/{HANDS_ON}/check", cookie)
        _contract("check-fail", res, CHECK_KEYS)
        items = res.get("checklist")
        if res.get("passed") is not False:
            raise StepFail("check-fail", "the check passed before the change")
        if (
            not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict)
            or items[0].get("passed") is not False
            or "smoke-ok" not in str(items[0].get("hint"))
        ):
            raise StepFail("check-fail", "expected one failed item with a hint naming smoke-ok")
        _ok("check-fail")

        await _type(ws, "touch ~/smoke-ok")
        await _wait(ws, _fix_done, timeout, "fix", "no shell prompt after the change")
        _ok("fix")

        res = await _post("check-pass", base, f"{task}/{HANDS_ON}/check", cookie)
        _contract("check-pass", res, CHECK_KEYS)
        if res.get("passed") is not True:
            raise StepFail("check-pass", "the check still fails after the change")
        _ok("check-pass")

    quiz = await _post("quiz-show", base, f"{task}/{QUIZ}/show", cookie)
    _quiz_shape(quiz)
    _ok("quiz-show")

    for step, option, expected in (("answer-wrong", 0, False), ("answer-right", 1, True)):
        res = await _post(step, base, f"{task}/{QUIZ}/answer", cookie, {"option": option})
        _contract(step, res, ANSWER_KEYS)
        if res.get("correct") is not expected:
            raise StepFail(step, "the answer was graded the wrong way round")
        _ok(step)


def _quiz_shape(quiz: dict[str, Any]) -> None:
    """Allow-list: any key the contract does not name might leak the correct option."""
    bad = set(quiz) - QUIZ_KEYS
    texts = [quiz.get("title"), quiz.get("question")]
    options = quiz.get("options")
    if not isinstance(options, list):
        raise StepFail("quiz-show", "the quiz detail has no options list")
    for item in [*texts, *options]:
        if not isinstance(item, dict):
            raise StepFail("quiz-show", "a quiz text is not a mapping")
        if set(item) - LANG_KEYS or not all(isinstance(v, str) for v in item.values()):
            raise StepFail("quiz-show", "a quiz text carries something besides en/he strings")
    if bad:
        raise StepFail("quiz-show", f"unexpected keys in the quiz: {', '.join(sorted(bad))}")


def _contract(step: str, res: dict[str, Any], allowed: set[str]) -> None:
    extra = set(res) - allowed
    if extra:
        raise StepFail(step, f"unexpected keys in the answer: {', '.join(sorted(extra))}")


def run(base: str, student: str, password: str, timeout: float) -> tuple[str, str] | None:
    """Run the smoke; return (step, reason) of the failure, or None."""
    try:
        return _run(base, student, password, timeout)
    except StepFail as exc:
        return exc.step, str(exc)


def _run(base: str, student: str, password: str, timeout: float) -> tuple[str, str] | None:
    try:
        cookie = login(base, student, password)
    except Fail as exc:
        raise StepFail("login", str(exc)) from None
    try:
        sid = start(base, cookie, LAB_ID)
    except Fail as exc:
        raise StepFail("start", str(exc)) from None
    print(f"ok start {sid}", flush=True)  # a session id (UUID): lets the workflow check the container
    failure: tuple[str, str] | None = None

    def on_failure(step: str, reason: str) -> None:
        nonlocal failure
        failure = failure or (step, reason)

    try:
        run_then_end(base, sid, cookie, _steps(base, sid, cookie, timeout), on_failure)
        if failure is None:
            _ok("end")
    except StepFail as exc:
        failure = (exc.step, str(exc))
    except Exception as exc:  # noqa: BLE001
        failure = ("terminal", f"unexpected error ({type(exc).__name__})")
    return failure


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: tasks.py [BASE_URL] (credentials: environment only)", file=sys.stderr)
        return 2
    base = (argv[1] if len(argv) == 2 else DEFAULT_BASE).rstrip("/")
    student = os.environ.get("SMOKE_ID", "")
    password = os.environ.get("SMOKE_PASSWORD", "")
    for name, value in (("SMOKE_ID", student), ("SMOKE_PASSWORD", password)):
        if not value:
            print(f"{name} is not set", file=sys.stderr)
            return 2
    try:
        timeout = float(os.environ.get("SMOKE_TIMEOUT", "60"))
    except ValueError:
        print("SMOKE_TIMEOUT must be a number of seconds", file=sys.stderr)
        return 2
    if not base.startswith(("http://", "https://")):
        print("BASE_URL must start with http:// or https://", file=sys.stderr)
        return 2
    failure = run(base, student, password, timeout)
    if failure:
        print(f"smoke failed: {failure[0]}: {failure[1]}", file=sys.stderr)
        if failure[0] == STOP_STEP:
            leave(1)  # do not wait for a worker thread stuck in a request
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
