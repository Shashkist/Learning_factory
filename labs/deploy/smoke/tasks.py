"""Live task-API smoke client (issue #128, decisions D36, D54).

Runs right after terminal.py with the same environment. Logs in as the synthetic smoke student,
starts the internal lab `smoke-tasks` and drives the task API through the public URL: terminal,
show, check (fails), fix in the terminal, check (passes), quiz show, wrong answer, right answer,
end. Prints one `ok <step>` line per step.

    SMOKE_ID=... SMOKE_PASSWORD=... [SMOKE_TIMEOUT=60] python tasks.py [BASE_URL]

On failure exits 1 with `smoke failed: <step>: <reason>` on stderr; the session is always ended.
Never prints the password, the cookie, the student id or a traceback.
"""
import asyncio
import http.cookies
import json
import os
import sys
from typing import Any

from terminal import (
    COOKIE,
    HI_LINE,
    Fail,
    _open,
    _output,
    _request,
)

DEFAULT_BASE = "https://labs.shashkist.com"
LAB_ID = "smoke-tasks"
QUIZ, HANDS_ON = "01-quiz", "02-touch-file"
CHECK_KEYS = {"passed", "attempts", "reason", "checklist"}
ANSWER_KEYS = {"correct", "attempts"}


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
            ws, lambda b: HI_LINE.search(b) is not None, timeout, "terminal",
            "the terminal never answered hi to echo hi",
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
        await _wait(ws, _prompt, timeout, "fix", "no shell prompt after the change")
        _ok("fix")

        res = await _post("check-pass", base, f"{task}/{HANDS_ON}/check", cookie)
        _contract("check-pass", res, CHECK_KEYS)
        if res.get("passed") is not True:
            raise StepFail("check-pass", "the check still fails after the change")
        _ok("check-pass")

    quiz = await _post("quiz-show", base, f"{task}/{QUIZ}/show", cookie)
    if {"correct", "answer", "correct_option"} & set(quiz):
        raise StepFail("quiz-show", "the quiz detail carries its answer")
    _ok("quiz-show")

    for step, option, expected in (("answer-wrong", 0, False), ("answer-right", 1, True)):
        res = await _post(step, base, f"{task}/{QUIZ}/answer", cookie, {"option": option})
        _contract(step, res, ANSWER_KEYS)
        if res.get("correct") is not expected:
            raise StepFail(step, "the answer was graded the wrong way round")
        _ok(step)


def _contract(step: str, res: dict[str, Any], allowed: set[str]) -> None:
    extra = set(res) - allowed
    if extra:
        raise StepFail(step, f"unexpected keys in the answer: {', '.join(sorted(extra))}")


def run(base: str, student: str, password: str, timeout: float) -> tuple[str, str] | None:
    """Run the smoke; return (step, reason) of the failure, or None."""
    status, headers, _ = _request(
        base, "POST", "/api/auth/login", None, {"id": student, "password": password}
    )
    if status != 200:
        return "login", f"login failed (http {status})"
    jar: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
    for value in headers.get_all("Set-Cookie") or []:
        jar.load(value)
    if COOKIE not in jar:
        return "login", "login set no session cookie"
    cookie = jar[COOKIE].value

    status, _, raw = _request(base, "POST", "/api/sessions", cookie, {"lab_id": LAB_ID})
    if status not in (200, 201):
        return "start", f"starting the lab failed (http {status})"
    try:
        sid = str(json.loads(raw)["id"])
    except (ValueError, KeyError, TypeError):
        return "start", "starting the lab returned no session id"
    failure: tuple[str, str] | None = None
    try:
        asyncio.run(_steps(base, sid, cookie, timeout))
    except StepFail as exc:
        failure = (exc.step, str(exc))
    except Exception as exc:  # noqa: BLE001
        failure = ("terminal", f"unexpected error ({type(exc).__name__})")
    try:
        end_status, _, _ = _request(base, "POST", f"/api/sessions/{sid}/end", cookie)
        if end_status in (200, 204):
            if failure is None:
                _ok("end")
        elif failure is None:
            failure = ("end", f"ending the session failed (http {end_status})")
    except Fail as exc:
        failure = failure or ("end", str(exc))
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
    try:
        failure = run(base, student, password, timeout)
    except Fail as exc:
        failure = ("login", str(exc))
    if failure:
        print(f"smoke failed: {failure[0]}: {failure[1]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
