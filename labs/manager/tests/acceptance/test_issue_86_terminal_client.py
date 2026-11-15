"""Acceptance tests for issue #86, part 2: the smoke client `labs/deploy/smoke/terminal.py` (D36).

The client is run as a program (`python labs/deploy/smoke/terminal.py [BASE_URL]`) against a fake
lab manager started in this process on 127.0.0.1. The fake speaks what the real one speaks to a
browser: `POST /api/auth/login` (sets a `Secure` `lf_session` cookie, so a client has to carry
the cookie itself over plain http), `POST /api/sessions`, `POST /api/sessions/<id>/end`, and the
ttyd 1.7 WebSocket on `/s/<id>/term/ws` (subprotocol `tty`; first client message is JSON with
`AuthToken`; client input is prefixed `0`; server output is prefixed `0`).

The fake ttyd behaves like the real one under gVisor (D33): input that arrives before the prompt
is dropped, so only a client that waits for the prompt (`$ ` at the end of the output) gets `hi`.
The echoed command line `echo hi` must not count as the answer; a line that is just `hi` must.

Interface fixed by this exam (the implementer must follow it):
  * argv[1] (optional): base URL, default https://labs.shashkist.com. http:// and ws:// forms
    must work for an `http://` base URL.
  * env SMOKE_ID and SMOKE_PASSWORD: the credentials. Never argv.
  * env SMOKE_TIMEOUT (seconds, default 60): how long to wait for the prompt and for `hi`, each.
  * the lab id sent to `POST /api/sessions` is `hello` (the id in labs/content/sample/hello/
    lab.yaml; the manager keys labs by id, not by path).
  * exit 0 on success and the session id on stdout; otherwise non-zero and a last stderr line
    that says why; never a traceback; never the password or the cookie value, on any stream.
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
CLIENT = REPO_ROOT / "labs" / "deploy" / "smoke" / "terminal.py"
SMOKE_ID = "990000000000000000000000000001"
PASSWORD = "smoke-client-" + "pw-0001"  # throwaway
COOKIE_NAME = "lf_session"
LAB_ID = "hello"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class Fake:
    """State and switches of the fake manager."""

    def __init__(self) -> None:
        self.mode = "good"  # good | no_hi | login_401 | create_503 | ws_refused | hangs_up
        self.events: list[tuple] = []
        self.sid = str(uuid.uuid4())
        self.token = "tok-" + uuid.uuid4().hex  # the cookie value; must never be printed
        self.early_input = False
        self.prompt_delay = 1.0

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

    @app.post("/api/auth/login")
    async def login(request: Request):
        body = await request.json()
        fake.events.append(("login", body.get("id"), body.get("password") == PASSWORD))
        bad = body.get("id") != SMOKE_ID or body.get("password") != PASSWORD
        if fake.mode == "login_401" or bad:
            return JSONResponse({"detail": "invalid_credentials"}, status_code=401)
        resp = JSONResponse({"kind": "student", "name": "Smoke Test"})
        resp.set_cookie(COOKIE_NAME, fake.token, httponly=True, secure=True, samesite="lax")
        return resp

    @app.post("/api/sessions")
    async def create(request: Request):
        body = await request.json()
        fake.events.append(("create", body.get("lab_id"), has_cookie(request.headers)))
        if not has_cookie(request.headers):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if fake.mode == "create_503":
            return JSONResponse({"detail": "capacity_full"}, status_code=503)
        if body.get("lab_id") != LAB_ID:
            return JSONResponse({"detail": "unknown_lab"}, status_code=404)
        return JSONResponse(
            {"id": fake.sid, "lab_id": LAB_ID, "status": "running"}, status_code=201
        )

    @app.post("/api/sessions/{sid}/end")
    async def end(sid: str, request: Request):
        fake.events.append(("end", sid, has_cookie(request.headers)))
        if not has_cookie(request.headers):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return Response(status_code=204)

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
            await ws.send_bytes(b"2" + b"{}")
            if fake.mode == "hangs_up":
                await ws.close(code=1011)
                return
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
                fake.events.append(("input", data[1:]))
                if b"echo hi" in data and data.rstrip(b"\x00").endswith((b"\r", b"\n")):
                    await ws.send_bytes(b"0echo hi\r\n")
                    if fake.mode != "no_hi":
                        await ws.send_bytes(b"0\x1b[?2004l\rhi\r\n")
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
    limit: int = 90,
) -> subprocess.CompletedProcess:
    assert CLIENT.is_file(), "labs/deploy/smoke/terminal.py is missing"
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
    if fake is not None:
        assert fake.token not in out, "the session cookie was printed"
    assert "Traceback" not in out, out


def reason_line(res: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
    assert lines, "a failure must say why on stderr"
    return lines[-1]


# ---------------------------------------------------------------- success path


def test_the_client_runs_echo_hi_end_to_end_and_exits_0(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert_clean(res, fake)
    assert fake.names()[:4] == ["login", "create", "ws", "input"], fake.events
    assert any(e[0] == "input" and e[1].startswith(b"echo hi") for e in fake.events)


def test_it_logs_in_with_the_id_and_password_from_the_environment(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    assert fake.events[0] == ("login", SMOKE_ID, True)


def test_it_starts_the_sample_lab_by_its_id(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    create = next(e for e in fake.events if e[0] == "create")
    assert create[1] == LAB_ID


def test_it_carries_the_session_cookie_on_every_request_including_the_websocket(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    for name in ("create", "ws", "end"):
        event = next(e for e in fake.events if e[0] == name)
        assert event[2] is True, f"{name} was sent without the lf_session cookie"


def test_it_offers_the_tty_subprotocol(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    assert next(e for e in fake.events if e[0] == "ws")[3] is True


def test_it_opens_the_terminal_of_the_session_it_created(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert run_client(base).returncode == 0
    assert next(e for e in fake.events if e[0] == "ws")[1] == fake.sid


def test_it_waits_for_the_prompt_before_typing(fake_server):
    fake, base = fake_server
    fake.reset("good")
    fake.prompt_delay = 2.5  # a client that types at once would lose its input (D33)
    res = run_client(base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert fake.early_input is False, "the client typed before the prompt"


def test_it_ends_the_session_it_started_and_prints_its_id(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base)
    assert res.returncode == 0
    assert ("end", fake.sid, True) in fake.events
    assert fake.names().index("end") > fake.names().index("input")
    assert fake.sid in res.stdout
    assert UUID_RE.findall(res.stdout) == [fake.sid] or fake.sid in UUID_RE.findall(res.stdout)


def test_it_prints_neither_the_password_nor_the_cookie(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base)
    assert res.returncode == 0
    assert_clean(res, fake)


# ---------------------------------------------------------------- failures


def test_when_hi_never_appears_it_fails_and_still_ends_the_session(fake_server):
    fake, base = fake_server
    fake.reset("no_hi")  # the echoed `echo hi` line is there, a line that is just `hi` is not
    res = run_client(base, timeout="4")
    assert res.returncode != 0, res.stdout + res.stderr
    assert "end" in fake.names(), fake.events
    assert ("end", fake.sid, True) in fake.events
    assert_clean(res, fake)
    assert "hi" in reason_line(res).lower()


def test_when_the_websocket_is_refused_it_fails_and_still_ends_the_session(fake_server):
    fake, base = fake_server
    fake.reset("ws_refused")
    res = run_client(base, timeout="4")
    assert res.returncode != 0, res.stdout + res.stderr
    assert ("end", fake.sid, True) in fake.events
    assert_clean(res, fake)
    reason_line(res)


def test_when_the_terminal_hangs_up_it_fails_and_still_ends_the_session(fake_server):
    fake, base = fake_server
    fake.reset("hangs_up")
    res = run_client(base, timeout="4")
    assert res.returncode != 0, res.stdout + res.stderr
    assert ("end", fake.sid, True) in fake.events
    assert_clean(res, fake)
    reason_line(res)


def test_a_rejected_login_fails_with_a_reason_and_starts_nothing(fake_server):
    fake, base = fake_server
    fake.reset("login_401")
    res = run_client(base)
    assert res.returncode != 0
    assert fake.names() == ["login"], fake.events
    assert_clean(res, fake)
    reason_line(res)


def test_a_wrong_password_is_a_failure(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base, password="not-the-" + "password")
    assert res.returncode != 0
    assert "create" not in fake.names()
    assert "not-the-password" not in res.stdout + res.stderr
    reason_line(res)


def test_a_refused_session_start_fails_with_a_reason(fake_server):
    fake, base = fake_server
    fake.reset("create_503")
    res = run_client(base)
    assert res.returncode != 0
    assert "ws" not in fake.names()
    assert_clean(res, fake)
    reason_line(res)


def test_an_unreachable_server_fails_with_a_one_line_reason():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    res = run_client(f"http://127.0.0.1:{port}", timeout="4")
    assert res.returncode != 0
    assert_clean(res)
    reason_line(res)


def test_missing_credentials_in_the_environment_fail_without_any_request(fake_server):
    fake, base = fake_server
    fake.reset("good")
    res = run_client(base, password=None)
    assert res.returncode != 0
    assert "SMOKE_PASSWORD" in res.stderr
    res = run_client(base, sid_env=None)
    assert res.returncode != 0
    assert "SMOKE_ID" in res.stderr
    assert fake.events == []


def test_credentials_are_not_read_from_the_command_line(fake_server):
    fake, base = fake_server
    fake.reset("good")
    assert CLIENT.is_file(), "labs/deploy/smoke/terminal.py is missing"
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
    assert CLIENT.is_file(), "labs/deploy/smoke/terminal.py is missing"
    assert "https://labs.shashkist.com" in CLIENT.read_text(encoding="utf-8")
