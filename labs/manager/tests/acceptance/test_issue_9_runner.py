"""Acceptance tests for issue #9, runner part: run a script in a session's container (D45).

No Docker and no database: the manager's `DockerRuntime` talks to a fake Docker Engine that
this module serves on 127.0.0.1 (raw HTTP, including the connection upgrade `exec start`
needs for stdin). The real thing, under runsc, is in test_issue_9_runner_docker.py.

Contract this exam fixes (the build must follow it or change the exam through the manager):

* `app.runtime.ExecResult(exit_code, output, timed_out=False)`, a dataclass-like object with
  those three attributes. `output` is a `str`: stdout and stderr, demultiplexed from Docker's
  8-byte frame headers (no header bytes in it), decoded as UTF-8 with replacement.
* `DockerRuntime.exec_script(name, script, timeout)` (sync or async, like the other methods)
  runs `script` (a `str`) in the running container `name` and returns an `ExecResult`.
  `LabRuntime` gets the same method, so the API layer can use a fake runtime.
  - The Engine API calls are exactly: `POST /containers/<name>/exec`, `POST /exec/<id>/start`
    (a connection upgrade; the script is written on that connection, then the write side is
    closed so the interpreter sees end of input) and `GET /exec/<id>/json` for the exit code.
    A `/v<version>` prefix on those paths is accepted. Nothing else is called: in particular
    nothing that writes a file into the container (no archive upload, no `cat >`).
  - The create body: `AttachStdin`, `AttachStdout`, `AttachStderr` true; `User` root
    (`root`, `0` or `0:0`); `Privileged` absent or false; no capability field; `WorkingDir`
    `/`; `Env` holds exactly `PATH=...` and `HOME=/root`; `Cmd` is the interpreter named by
    the script's shebang followed by `-s` (`#!/bin/sh` gives `["/bin/sh", "-s"]`; a script
    without a shebang runs under `/bin/sh`; `#!/usr/bin/env python3` gives
    `["/usr/bin/env", "python3", "-s"]`). The script text appears only on stdin.
  - A script that outlives `timeout` seconds returns `ExecResult(exit_code=None or non-zero,
    timed_out=True)` shortly after the deadline; it does not raise and does not hang.
  - An engine that cannot be reached, or that refuses the exec create, raises (any
    `Exception`): a runner that returned exit code 0 there would let a task look set up.
* `LF_DOCKER_HOST` also accepts `tcp://host:port` (next to `unix://path`); this is the seam
  the exam uses. It is read when `DockerRuntime()` is created, as for `unix://`.
"""
import asyncio
import inspect
import json
import re
import socket
import struct
import threading
import time

import pytest

SCRIPT_SH = "#!/bin/sh\necho hello from setup\nexit 0\n"
ALLOWED = (
    re.compile(r"^POST /containers/[^/]+/exec$"),
    re.compile(r"^POST /exec/[^/]+/start$"),
    re.compile(r"^GET /exec/[^/]+/json$"),
)


def frame(stream: int, data: bytes) -> bytes:
    return struct.pack(">BxxxI", stream, len(data)) + data


class FakeEngine:
    """A just-enough Docker Engine: exec create, hijacked exec start, exec inspect."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes]] = []  # method, path (no version), body
        self.creates: list[dict] = []
        self.stdin: list[bytes] = []
        self.exit_codes: dict[str, int] = {}
        self.finished: set[str] = set()
        self.create_status = 201
        # behaviour(create_body, stdin) -> dict(exit=, frames=[bytes], delay=)
        self.behaviour = lambda body, stdin: {"exit": 0, "frames": [], "delay": 0.0}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.AbstractServer | None = None
        self._ready = threading.Event()
        self.port = 0

    def paths(self) -> list[str]:
        return [f"{m} {p}" for m, p, _ in self.requests]

    # -- server plumbing
    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()
        assert self._ready.wait(10), "fake engine did not start"

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def boot() -> None:
            self._server = await asyncio.start_server(self._conn, "127.0.0.1", 0)
            self.port = self._server.sockets[0].getsockname()[1]
            self._ready.set()

        self._loop.run_until_complete(boot())
        self._loop.run_forever()

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _reply(self, w, status: str, body: dict | None = None) -> None:
        raw = json.dumps(body or {}).encode()
        w.write(
            f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw
        )
        await w.drain()

    async def _conn(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await r.readuntil(b"\r\n\r\n")
                lines = head.decode("latin-1").split("\r\n")
                method, target, _ = lines[0].split(" ", 2)
                headers = {
                    k.strip().lower(): v.strip()
                    for k, v in (ln.split(":", 1) for ln in lines[1:] if ":" in ln)
                }
                body = b""
                if int(headers.get("content-length", "0")):
                    body = await r.readexactly(int(headers["content-length"]))
                path = re.sub(r"^/v\d+(\.\d+)?", "", target.split("?", 1)[0])
                self.requests.append((method, path, body))
                if await self._route(method, path, body, headers, r, w):
                    return
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            w.close()

    async def _route(self, method, path, body, headers, r, w) -> bool:
        """Returns True when the connection is finished."""
        if re.fullmatch(r"/containers/[^/]+/exec", path) and method == "POST":
            if self.create_status != 201:
                await self._reply(w, f"{self.create_status} Error", {"message": "refused"})
                return False
            create = json.loads(body or b"{}")
            self.creates.append(create)
            await self._reply(w, "201 Created", {"Id": f"exec{len(self.creates)}"})
            return False
        m = re.fullmatch(r"/exec/([^/]+)/start", path)
        if m and method == "POST":
            eid = m.group(1)
            create = self.creates[int(eid.removeprefix("exec")) - 1]
            upgrade = "upgrade" in headers.get("connection", "").lower()
            if upgrade:
                w.write(
                    b"HTTP/1.1 101 UPGRADED\r\nContent-Type: application/vnd.docker.raw-stream\r\n"
                    b"Connection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
                )
                await w.drain()
                stdin = await r.read()  # until the client closes its write side
            else:
                stdin = b""
                w.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/vnd.docker.raw-stream\r\n"
                    b"Connection: close\r\n\r\n"
                )
            self.stdin.append(stdin)
            plan = self.behaviour(create, stdin)
            if plan.get("delay"):
                await asyncio.sleep(plan["delay"])
            for fr in plan.get("frames", []):
                w.write(fr)
                await w.drain()
            self.exit_codes[eid] = plan.get("exit", 0)
            self.finished.add(eid)
            return True
        m = re.fullmatch(r"/exec/([^/]+)/json", path)
        if m and method == "GET":
            eid = m.group(1)
            await self._reply(
                w,
                "200 OK",
                {
                    "ID": eid,
                    "Running": eid not in self.finished,
                    "ExitCode": self.exit_codes.get(eid, 0) if eid in self.finished else None,
                },
            )
            return False
        await self._reply(w, "404 Not Found", {"message": "fake engine: no such route"})
        return False


@pytest.fixture
def engine(monkeypatch):
    eng = FakeEngine()
    eng.start()
    monkeypatch.setenv("LF_DOCKER_HOST", f"tcp://127.0.0.1:{eng.port}")
    yield eng
    eng.stop()


def runtime():
    from app.runtime import DockerRuntime

    return DockerRuntime()


def run(engine_obj, script: str, timeout: float = 10, name: str = "lab-exam-1"):
    rt = runtime()
    res = rt.exec_script(name, script, timeout)
    if inspect.isawaitable(res):
        res = asyncio.run(_await(res))
    return res


async def _await(aw):
    return await aw


# ---------------------------------------------------------------- the interface exists


def test_exec_result_has_exit_code_output_and_timed_out():
    from app.runtime import ExecResult

    ok = ExecResult(0, "out")
    assert (ok.exit_code, ok.output, ok.timed_out) == (0, "out", False)
    late = ExecResult(None, "", True)
    assert late.timed_out is True


def test_lab_runtime_protocol_and_docker_runtime_have_exec_script():
    from app.runtime import DockerRuntime, LabRuntime

    assert callable(getattr(DockerRuntime, "exec_script", None))
    assert "exec_script" in dir(LabRuntime)


# ---------------------------------------------------------------- streamed on stdin


def test_the_script_is_streamed_on_stdin_exactly_as_given(engine):
    run(engine, SCRIPT_SH)
    assert engine.stdin == [SCRIPT_SH.encode()]


def test_the_exec_is_created_on_the_named_container(engine):
    run(engine, SCRIPT_SH, name="lab-0123abcd")
    assert ("POST", "/containers/lab-0123abcd/exec") in [(m, p) for m, p, _ in engine.requests]


@pytest.mark.parametrize(
    "script,cmd",
    [
        ("#!/bin/sh\ntrue\n", ["/bin/sh", "-s"]),
        ("#!/bin/bash\ntrue\n", ["/bin/bash", "-s"]),
        ("true\n", ["/bin/sh", "-s"]),
    ],
)
def test_the_shebang_interpreter_runs_the_script_with_dash_s(engine, script, cmd):
    run(engine, script)
    assert engine.creates[0]["Cmd"] == cmd


def test_an_env_shebang_keeps_its_interpreter_and_adds_dash_s(engine):
    run(engine, "#!/usr/bin/env python3\nprint(1)\n")
    assert engine.creates[0]["Cmd"] == ["/usr/bin/env", "python3", "-s"]


def test_the_script_text_never_appears_in_any_request_but_stdin(engine):
    script = "#!/bin/sh\n# UNIQUE-SETUP-MARKER-5521\ntouch /tmp/x\n"
    run(engine, script)
    for method, path, body in engine.requests:
        assert "UNIQUE-SETUP-MARKER-5521" not in path
        assert b"UNIQUE-SETUP-MARKER-5521" not in body, f"{method} {path} carried the script"
    assert b"UNIQUE-SETUP-MARKER-5521" in engine.stdin[0]


def test_nothing_is_written_into_the_container(engine):
    run(engine, SCRIPT_SH)
    for call in engine.paths():
        assert any(p.match(call) for p in ALLOWED), f"unexpected engine call: {call}"
    cmd = engine.creates[0]["Cmd"]
    assert len(cmd) <= 3 and "-c" not in cmd, cmd
    assert not any(a in ("tee", "cat", "dd") for a in cmd)


def test_stdin_is_attached_and_closed_so_the_interpreter_sees_end_of_input(engine):
    run(engine, SCRIPT_SH)
    body = engine.creates[0]
    assert body["AttachStdin"] is True
    assert body["AttachStdout"] is True and body["AttachStderr"] is True
    assert engine.stdin, "the fake saw no end of input: the client did not close its write side"


# ---------------------------------------------------------------- no extra privilege (D45)


def test_the_exec_runs_as_root(engine):
    run(engine, SCRIPT_SH)
    assert engine.creates[0]["User"] in ("root", "0", "0:0")


def test_the_exec_is_not_privileged_and_adds_no_capabilities(engine):
    run(engine, SCRIPT_SH)
    body = engine.creates[0]
    assert not body.get("Privileged")
    for key in body:
        assert "cap" not in key.lower(), f"capability field {key!r} in the exec create body"


def test_working_directory_is_root_and_env_is_path_and_home_only(engine):
    run(engine, SCRIPT_SH)
    body = engine.creates[0]
    assert body["WorkingDir"] == "/"
    env = dict(e.split("=", 1) for e in body["Env"])
    assert set(env) == {"PATH", "HOME"}, env
    assert env["HOME"] == "/root"
    assert env["PATH"]


# ---------------------------------------------------------------- result


def test_exit_code_and_output_are_returned(engine):
    engine.behaviour = lambda b, s: {
        "exit": 3,
        "frames": [frame(1, b"out-line\n"), frame(2, b"err-line\n")],
    }
    res = run(engine, SCRIPT_SH)
    assert res.exit_code == 3
    assert res.timed_out is False
    assert "out-line" in res.output and "err-line" in res.output
    assert "\x00" not in res.output and "\x01" not in res.output, "frame headers leaked"


def test_exit_code_zero_is_success(engine):
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [frame(1, b"fine\n")]}
    res = run(engine, SCRIPT_SH)
    assert res.exit_code == 0 and res.timed_out is False
    assert res.output.strip() == "fine"


def test_large_multi_frame_output_is_reassembled(engine):
    chunk = b"x" * 8192
    frames = [frame(1, chunk) for _ in range(25)] + [frame(2, b"tail")]
    engine.behaviour = lambda b, s: {"exit": 0, "frames": frames}
    res = run(engine, SCRIPT_SH)
    assert len(res.output) == 25 * 8192 + 4
    assert res.output.endswith("tail")


def test_output_that_is_not_utf8_does_not_crash_the_runner(engine):
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [frame(1, b"\xff\xfe ok\n")]}
    res = run(engine, SCRIPT_SH)
    assert "ok" in res.output


def test_a_script_with_no_output_has_empty_output(engine):
    res = run(engine, SCRIPT_SH)
    assert res.output == "" and res.exit_code == 0


# ---------------------------------------------------------------- timeout and failures


def test_a_script_that_outlives_the_timeout_is_reported_as_timed_out(engine):
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [], "delay": 30.0}
    started = time.monotonic()
    res = run(engine, "#!/bin/sh\nsleep 30\n", timeout=1)
    assert time.monotonic() - started < 8, "the runner waited for the script, not the timeout"
    assert res.timed_out is True
    assert res.exit_code != 0


def test_a_fast_script_is_not_reported_as_timed_out(engine):
    res = run(engine, SCRIPT_SH, timeout=5)
    assert res.timed_out is False


def test_an_unreachable_engine_raises(engine, monkeypatch):
    assert run(engine, SCRIPT_SH).exit_code == 0  # the runner works against a live engine
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    monkeypatch.setenv("LF_DOCKER_HOST", f"tcp://127.0.0.1:{port}")
    with pytest.raises(Exception):  # noqa: B017
        run(None, SCRIPT_SH, timeout=3)


@pytest.mark.parametrize("status", [404, 409, 500])
def test_an_engine_that_refuses_the_exec_raises(engine, status):
    assert run(engine, SCRIPT_SH).exit_code == 0  # the runner works while the engine accepts
    engine.stdin.clear()
    engine.create_status = status
    with pytest.raises(Exception):  # noqa: B017
        run(engine, SCRIPT_SH)
    assert engine.stdin == [], "the script must not be sent when the exec was refused"
