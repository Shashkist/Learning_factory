"""Acceptance tests for issue #128, part 2: the task smoke client `labs/deploy/smoke/tasks.py`.

Run as a program against a fake lab manager started in this process (as #86 did for
terminal.py). `terminal.py` itself stays as it is (its exam, test_issue_86_terminal_client,
pins the `hello` lab); the new client is a sibling module that the post-deploy smoke runs after
it. Same conventions as terminal.py.

Interface fixed by this exam (the implementer must follow it):
  * `python labs/deploy/smoke/tasks.py [BASE_URL]`; default https://labs.shashkist.com.
  * env SMOKE_ID, SMOKE_PASSWORD (credentials, never argv), SMOKE_TIMEOUT (seconds, default 60,
    for the prompt and for each terminal answer). Missing credential: exit 2 naming the variable,
    no request made.
  * Flow, one session of lab `smoke-tasks` (D54), terminal over `/s/<sid>/term/ws` (ttyd, as in
    terminal.py; wait for the `$ ` prompt before typing, input before it is dropped, D33):
      login, start, terminal (type `echo hi`, see a line that is just `hi`),
      show (POST .../tasks/02-touch-file/show),
      check-fail (POST .../tasks/02-touch-file/check: must answer passed false and exactly the
        one checklist item failed with a hint that contains `smoke-ok`),
      fix (type `touch ~/smoke-ok` in the terminal and wait for the next prompt),
      check-pass (check again: must answer passed true),
      quiz-show (POST .../tasks/01-quiz/show),
      answer-wrong (POST .../tasks/01-quiz/answer {"option": 0}: must be correct false),
      answer-right (same with {"option": 1}: must be correct true),
      end (POST /api/sessions/<sid>/end).
    The quiz is shown before it is answered (a task never shown is 409 task_not_shown).
  * The quiz leak rule: the correct option must be in no response. The client fails if the quiz
    detail contains a key named `correct`, or any answer/check response has a key outside the
    contract (answer: `correct`, `attempts`; check: `passed`, `attempts`, `reason`,
    `checklist`), or the quiz detail or an answer response carries an unexpected key such as
    `answer` or `correct_option`.
  * Output: each step prints exactly one stdout line `ok <step>` (optionally followed by a space
    and detail), steps named as above, in that order; every stdout line has that form. Never the
    password, the cookie value, the student id or the quiz's correct index as a bare word.
  * Failure: exit 1, the failing step is not reported as ok on stdout, and the last stderr
    line is `smoke failed: <step>: <reason>` (names the step that failed); never a traceback.
    The session is ended (the fake sees the end call, with the cookie) whenever it was started,
    even when a step failed. A failure to start never calls end.
All values are synthetic.
"""
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CLIENT = REPO_ROOT / "labs" / "deploy" / "smoke" / "tasks.py"
SMOKE_ID = "990000000000000000000000000001"
PASSWORD = "smoke-tasks-" + "pw-0001"  # throwaway
COOKIE_NAME = "lf_session"
LAB_ID = "smoke-tasks"
QUIZ, HANDS_ON = "01-quiz", "02-touch-file"
CORRECT = 1
STEPS = [
    "terminal", "show", "check-fail", "fix", "check-pass",
    "quiz-show", "answer-wrong", "answer-right", "end",
]
HINT = "File ~/smoke-ok does not exist yet"
STEP_LINE = re.compile(r"ok [a-z][a-z-]*( .*)?")


class Fake:
    def __init__(self) -> None:
        self.mode = "good"
        self.events: list[tuple] = []
        self.sid = str(uuid.uuid4())
        self.token = "tok-" + uuid.uuid4().hex
        self.made = False
        self.shown: set[str] = set()
        self.early_input = False
        self.prompt_delay = 0.3

    def reset(self, mode: str) -> None:
        self.__init__()  # type: ignore[misc]
        self.mode = mode

    def names(self) -> list[str]:
        return [e[0] for e in self.events]


def _build_app(fake: Fake):
    from fastapi import FastAPI, Request, Response, WebSocket
    from fastapi.responses import JSONResponse
    from starlette.websockets import WebSocketDisconnect

    app = FastAPI()

    def has_cookie(headers) -> bool:
        return f"{COOKIE_NAME}={fake.token}" in (headers.get("cookie") or "")

    def err(code: int, detail):
        return JSONResponse({"detail": detail}, status_code=code)

    @app.post("/api/auth/login")
    async def login(request: Request):
        body = await request.json()
        fake.events.append(("login", body.get("id"), body.get("password") == PASSWORD))
        bad = body.get("id") != SMOKE_ID or body.get("password") != PASSWORD
        if fake.mode == "login_401" or bad:
            return err(401, "invalid_credentials")
        resp = JSONResponse({"kind": "student", "name": "Smoke Test"})
        resp.set_cookie(COOKIE_NAME, fake.token, httponly=True, secure=True, samesite="lax")
        return resp

    @app.post("/api/sessions")
    async def create(request: Request):
        body = await request.json()
        fake.events.append(("create", body.get("lab_id"), has_cookie(request.headers)))
        if not has_cookie(request.headers):
            return err(401, "unauthorized")
        if fake.mode == "create_404" or body.get("lab_id") != LAB_ID:
            return err(404, "unknown_lab")
        made = {"id": fake.sid, "lab_id": LAB_ID, "status": "running"}
        return JSONResponse(made, status_code=201)

    @app.post("/api/sessions/{sid}/end")
    async def end(sid: str, request: Request):
        fake.events.append(("end", sid, has_cookie(request.headers)))
        if not has_cookie(request.headers):
            return err(401, "unauthorized")
        return Response(status_code=204)

    def guard(sid: str, tid: str, request: Request):
        if not has_cookie(request.headers):
            return err(401, "unauthorized")
        if sid != fake.sid:
            return err(404, "no_session")
        if tid not in (QUIZ, HANDS_ON):
            return err(404, "unknown_task")
        return None

    @app.post("/api/sessions/{sid}/tasks/{tid}/show")
    async def show(sid: str, tid: str, request: Request):
        fake.events.append(("show", tid))
        bad = guard(sid, tid, request)
        if bad:
            return bad
        if fake.mode == "show_500" and tid == HANDS_ON:
            return err(502, {"code": "setup_failed", "message": "setup failed"})
        fake.shown.add(tid)
        if tid == HANDS_ON:
            return JSONResponse({
                "id": HANDS_ON, "index": 2, "total": 2, "kind": "hands-on",
                "title": {"en": "Create the file", "he": "ליצור"}, "state": "shown",
                "text": {"en": "touch ~/smoke-ok", "he": "touch ~/smoke-ok"},
                "checklist": [{"en": "The file exists", "he": "הקובץ קיים"}],
                "solution_available": False,
            })
        body = {
            "id": QUIZ, "index": 1, "total": 2, "kind": "quiz", "title": {"en": "Q", "he": "ש"},
            "state": "shown", "question": {"en": "Which?", "he": "איזה?"},
            "options": [{"en": "zero", "he": "0"}, {"en": "one", "he": "1"},
                        {"en": "two", "he": "2"}],
            "solution_available": False,  # every task detail carries it since #154 (D128, #452)
        }
        if fake.mode == "quiz_solution_available":
            body["solution_available"] = True
        if fake.mode == "leak_in_show":
            body["correct"] = CORRECT
        if fake.mode == "leak_answer_key_in_show":
            body["answer"] = CORRECT
        return JSONResponse(body)

    @app.post("/api/sessions/{sid}/tasks/{tid}/check")
    async def check(sid: str, tid: str, request: Request):
        fake.events.append(("check", tid, fake.made))
        bad = guard(sid, tid, request)
        if bad:
            return bad
        if tid not in fake.shown:
            return err(409, "task_not_shown")
        passed = fake.made or fake.mode == "passes_early"
        if fake.mode == "never_passes":
            passed = False
        hint = HINT if fake.mode != "wrong_hint" else "Something else is missing"
        item = {"n": 1, "text": {"en": "The file exists", "he": "הקובץ קיים"},
                "passed": passed, "hint": None if passed else hint}
        return JSONResponse({
            "passed": passed, "attempts": sum(1 for e in fake.events if e[0] == "check"),
            "reason": None, "checklist": [item],
        })

    @app.post("/api/sessions/{sid}/tasks/{tid}/answer")
    async def answer(sid: str, tid: str, request: Request):
        body = await request.json()
        option = body.get("option")
        fake.events.append(("answer", tid, option))
        bad = guard(sid, tid, request)
        if bad:
            return bad
        if tid not in fake.shown:
            return err(409, "task_not_shown")
        correct = option == CORRECT
        if fake.mode == "wrong_is_right":
            correct = True
        if fake.mode == "right_is_wrong":
            correct = False
        out = {"correct": correct, "attempts": sum(1 for e in fake.events if e[0] == "answer")}
        if fake.mode == "leak_in_answer":
            out["correct_option"] = CORRECT
        return JSONResponse(out)

    @app.websocket("/s/{sid}/term/ws")
    async def term(ws: WebSocket, sid: str):
        offered = ws.headers.get("sec-websocket-protocol", "")
        fake.events.append(("ws", sid, has_cookie(ws.headers), "tty" in offered))
        if fake.mode == "ws_refused" or sid != fake.sid or not has_cookie(ws.headers):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="tty")
        try:
            first = await ws.receive()
            raw = first.get("bytes") or (first.get("text") or "").encode()
            try:
                assert "AuthToken" in json.loads(raw)
            except Exception:  # noqa: BLE001
                await ws.close(code=1002)
                return
            await ws.send_bytes(b"1" + b"student@lab: /bin/bash")
            deadline = time.monotonic() + fake.prompt_delay
            while time.monotonic() < deadline:  # input before the prompt is dropped (D33)
                try:
                    msg = await asyncio.wait_for(ws.receive(), deadline - time.monotonic())
                except TimeoutError:
                    break
                if msg["type"] == "websocket.disconnect":
                    return
                data = msg.get("bytes") or (msg.get("text") or "").encode()
                if data[:1] == b"0":
                    fake.early_input = True
            await ws.send_bytes(b"0\x1b[?2004hstudent@lab:~$ ")
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                data = msg.get("bytes") or (msg.get("text") or "").encode()
                if data[:1] != b"0":
                    continue
                line = data[1:].decode("utf-8", "replace")
                if not line.rstrip("\x00").endswith(("\r", "\n")):
                    continue
                fake.events.append(("input", line.strip()))
                await ws.send_bytes(b"0" + line.strip().encode() + b"\r\n")
                if "echo hi" in line and fake.mode != "no_hi":
                    await ws.send_bytes(b"0\x1b[?2004l\rhi\r\n")
                if "touch ~/smoke-ok" in line and fake.mode != "touch_ignored":
                    fake.made = True
                await ws.send_bytes(b"0\x1b[?2004hstudent@lab:~$ ")
        except WebSocketDisconnect:
            return

    return app


@pytest.fixture(scope="module")
def fake_server():
    import uvicorn

    fake = Fake()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        _build_app(fake), host="127.0.0.1", port=port, log_level="error", ws="auto"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline, "the fake manager did not start"
        time.sleep(0.05)
    yield fake, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def run_client(
    base: str | None,
    *,
    sid_env: str | None = SMOKE_ID,
    password: str | None = PASSWORD,
    timeout: str = "8",
    limit: int = 120,
) -> subprocess.CompletedProcess:
    assert CLIENT.is_file(), "labs/deploy/smoke/tasks.py is missing"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SMOKE_", "LF_"))}
    env["SMOKE_TIMEOUT"] = timeout
    if sid_env is not None:
        env["SMOKE_ID"] = sid_env
    if password is not None:
        env["SMOKE_PASSWORD"] = password
    args = [sys.executable, str(CLIENT)] + ([base] if base else [])
    return subprocess.run(
        args, capture_output=True, text=True, env=env, timeout=limit, cwd=REPO_ROOT
    )


def assert_clean(res: subprocess.CompletedProcess, fake: Fake | None = None) -> None:
    out = res.stdout + res.stderr
    assert PASSWORD not in out, "the password was printed"
    assert SMOKE_ID not in out, "the student id was printed"
    if fake is not None:
        assert fake.token not in out, "the session cookie was printed"
    assert "Traceback" not in out, out


def reason_line(res: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
    assert lines, "a failure must say why on stderr"
    assert lines[-1].startswith("smoke failed: "), lines[-1]
    return lines[-1]


def step_names(res: subprocess.CompletedProcess) -> list[str]:
    names = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        assert STEP_LINE.fullmatch(line.strip()), f"not a step line: {line!r}"
        names.append(line.split()[1])
    return names


def fails_at(fake_server, mode: str, step: str, *, timeout: str = "8"):
    """Run against a fake in `mode`; the run must fail naming `step` and still end the session."""
    fake, base = fake_server
    fake.reset(mode)
    res = run_client(base, timeout=timeout)
    assert res.returncode == 1, res.stdout + res.stderr
    assert_clean(res, fake)
    assert f"smoke failed: {step}:" in reason_line(res), reason_line(res)
    assert ("end", fake.sid, True) in fake.events, fake.events
    assert step not in step_names(res), "the failing step must not be reported as ok"
    return fake, res


# ---------------------------------------------------------------- success path


def test_the_full_run_exits_0_and_drives_every_step_in_order(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert_clean(res, fake)
    names = step_names(res)
    positions = [names.index(s) for s in STEPS]
    assert positions == sorted(positions), names
    interesting = [e[0] for e in fake.events if e[0] != "ws"]
    assert interesting == [
        "login", "create", "input", "show", "check", "input", "check", "show", "answer",
        "answer", "end",
    ], fake.events


def test_it_starts_the_smoke_lab_and_shows_the_hands_on_task_first(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    assert next(e for e in fake.events if e[0] == "create")[1] == LAB_ID
    assert next(e for e in fake.events if e[0] == "show")[1] == HANDS_ON


def test_it_does_the_terminal_check_first_by_typing_echo_hi_after_the_prompt(fake_server):
    fake, base = fake_server
    fake.reset("good")
    fake.prompt_delay = 2.0
    res = run_client(base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert fake.early_input is False, "the client typed before the prompt"
    assert next(e for e in fake.events if e[0] == "input")[1] == "echo hi"
    assert fake.names().index("input") < fake.names().index("show")


def test_the_first_check_fails_before_the_change_and_the_second_passes_after_it(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    checks = [e for e in fake.events if e[0] == "check"]
    assert [(c[1], c[2]) for c in checks] == [(HANDS_ON, False), (HANDS_ON, True)]


def test_the_change_is_made_through_the_terminal_between_the_two_checks(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    names = fake.names()
    first_check = names.index("check")
    second_check = len(names) - 1 - names[::-1].index("check")
    typed = [(i, e[1]) for i, e in enumerate(fake.events) if e[0] == "input"]
    assert any(
        first_check < i < second_check and cmd == "touch ~/smoke-ok" for i, cmd in typed
    ), fake.events


def test_the_quiz_is_shown_then_answered_wrongly_then_rightly(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    quiz = [e for e in fake.events if e[0] in ("show", "answer") and e[1] == QUIZ]
    assert [e[0] for e in quiz] == ["show", "answer", "answer"]
    first, second = quiz[1][2], quiz[2][2]
    assert first in (0, 2) and second == CORRECT, quiz


def test_it_ends_its_session_with_the_cookie_after_the_last_answer(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base)
    assert res.returncode == 0
    assert ("end", fake.sid, True) in fake.events
    assert fake.names()[-1] == "end"
    assert step_names(res)[-1] == "end"


def test_it_prints_one_line_per_step_and_nothing_secret(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base)
    assert res.returncode == 0
    assert_clean(res, fake)
    names = step_names(res)
    assert len(names) == len(set(names)), f"a step is reported twice: {names}"
    assert len([ln for ln in res.stdout.splitlines() if ln.strip()]) == len(names)


def test_it_carries_the_cookie_on_every_request_and_offers_tty(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    for name in ("create", "ws", "end"):
        event = next(e for e in fake.events if e[0] == name)
        assert event[2] is True, f"{name} was sent without the lf_session cookie"
    assert next(e for e in fake.events if e[0] == "ws")[3] is True


# ---------------------------------------------------------------- deviations fail with a clear line


def test_a_first_check_that_passes_is_a_failure(fake_server):
    fake, _ = fails_at(fake_server, "passes_early", "check-fail")
    assert "input" in fake.names()  # the terminal check ran first
    assert not any(e[0] == "input" and e[1] == "touch ~/smoke-ok" for e in fake.events)


def test_a_failing_check_without_the_expected_hint_is_a_failure(fake_server):
    fails_at(fake_server, "wrong_hint", "check-fail")


def test_a_second_check_that_still_fails_is_a_failure(fake_server):
    fake, _ = fails_at(fake_server, "never_passes", "check-pass")
    assert "answer" not in fake.names(), "the quiz must not run after a failed check"


def test_a_change_that_does_not_take_effect_is_a_failure_at_the_second_check(fake_server):
    fails_at(fake_server, "touch_ignored", "check-pass")


def test_a_wrong_answer_that_is_graded_correct_is_a_failure(fake_server):
    fake, _ = fails_at(fake_server, "wrong_is_right", "answer-wrong")
    assert [e for e in fake.events if e[0] == "answer"][1:] == []


def test_a_right_answer_that_is_graded_wrong_is_a_failure(fake_server):
    fails_at(fake_server, "right_is_wrong", "answer-right")


def test_a_quiz_detail_that_carries_the_correct_index_is_a_failure(fake_server):
    fake, _ = fails_at(fake_server, "leak_in_show", "quiz-show")
    assert "answer" not in fake.names()


def test_a_quiz_detail_with_an_answer_key_is_a_failure(fake_server):
    fails_at(fake_server, "leak_answer_key_in_show", "quiz-show")


def test_a_quiz_detail_that_says_a_solution_is_available_is_a_failure(fake_server):
    """#452: a quiz never has a solution (D128), so `solution_available` must be false on it."""
    fails_at(fake_server, "quiz_solution_available", "quiz-show")


def test_an_answer_response_that_carries_the_correct_option_is_a_failure(fake_server):
    _, res = fails_at(fake_server, "leak_in_answer", "answer-wrong")
    assert str(CORRECT) not in reason_line(res).split(": ", 2)[-1].split()


def test_a_failed_setup_when_showing_the_task_is_a_failure(fake_server):
    fake, _ = fails_at(fake_server, "show_500", "show")
    assert "check" not in fake.names()


def test_when_hi_never_appears_it_fails_at_the_terminal_step(fake_server):
    fake, _ = fails_at(fake_server, "no_hi", "terminal", timeout="4")
    assert "show" not in fake.names()


def test_a_refused_terminal_fails_at_the_terminal_step_and_ends_the_session(fake_server):
    fails_at(fake_server, "ws_refused", "terminal", timeout="4")


def test_a_failure_line_never_contains_the_password_or_the_student_id(fake_server):
    fake, res = fails_at(fake_server, "wrong_hint", "check-fail")
    assert PASSWORD not in res.stderr and SMOKE_ID not in res.stderr


# ---------------------------------------------------------------- start-up failures


def test_a_rejected_login_fails_and_starts_nothing(fake_server):
    fake, base = fake_server
    fake.reset("login_401")
    res = run_client(base)
    assert res.returncode == 1
    assert fake.names() == ["login"], fake.events
    assert_clean(res, fake)
    reason_line(res)


def test_an_unknown_lab_answer_fails_without_an_end_call(fake_server):
    fake, base = fake_server
    fake.reset("create_404")
    res = run_client(base)
    assert res.returncode == 1
    assert "end" not in fake.names() and "ws" not in fake.names()
    assert_clean(res, fake)
    reason_line(res)


def test_an_unreachable_server_fails_with_a_one_line_reason():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    res = run_client(f"http://127.0.0.1:{port}", timeout="4")
    assert res.returncode == 1
    assert_clean(res)
    reason_line(res)


def test_missing_credentials_exit_2_naming_the_variable_without_any_request(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base, password=None)
    assert res.returncode == 2 and "SMOKE_PASSWORD" in res.stderr
    res = run_client(base, sid_env=None)
    assert res.returncode == 2 and "SMOKE_ID" in res.stderr
    assert fake.events == []


def test_credentials_are_not_read_from_the_command_line(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert CLIENT.is_file(), "labs/deploy/smoke/tasks.py is missing"
    res = subprocess.run(
        [sys.executable, str(CLIENT), base, SMOKE_ID, PASSWORD],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith(("SMOKE_", "LF_"))},
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert res.returncode != 0
    assert fake.events == []
    assert PASSWORD not in res.stdout + res.stderr


def test_the_default_base_url_is_the_public_labs_host():
    assert CLIENT.is_file(), "labs/deploy/smoke/tasks.py is missing"
    assert "https://labs.shashkist.com" in CLIENT.read_text(encoding="utf-8")


def test_the_client_never_hardcodes_a_student_id_or_a_password():
    assert CLIENT.is_file(), "labs/deploy/smoke/tasks.py is missing"
    source = CLIENT.read_text(encoding="utf-8")
    assert SMOKE_ID not in source
    assert not re.search(r"\b99\d{28}\b", source), "a student id literal in the client"
