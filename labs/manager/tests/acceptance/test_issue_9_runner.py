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
    nothing that writes a file into the container (no archive upload, no `cat >`). (#117: the
    kill exec of mechanism (b) uses these same endpoints, so this list does not change.)
  - The create body: `AttachStdin`, `AttachStdout`, `AttachStderr` true; `User` root
    (`root`, `0` or `0:0`); `Privileged` absent or false; no capability field; `WorkingDir`
    `/`; `Env` holds exactly `PATH=...` and `HOME=/root`; `Cmd` is the interpreter named by
    the script's shebang followed by `-s` (`#!/bin/sh` gives `["/bin/sh", "-s"]`; a script
    without a shebang runs under `/bin/sh`; `#!/usr/bin/env python3` gives
    `["/usr/bin/env", "python3", "-s"]`). The script text appears only on stdin.
  - A script that outlives `timeout` seconds returns `ExecResult(exit_code=None or non-zero,
    timed_out=True)` shortly after the deadline; it does not raise and does not hang.
  - (#117) A timed-out script is killed, not abandoned. Two mechanisms are accepted, the build
    picks one: (a) the `Cmd` is wrapped as `timeout -s KILL <n> <interpreter> -s` (`<n>` at
    least `timeout` and at most `timeout + 5` seconds), or (b) after the deadline a second exec
    (same endpoints, same no-privilege rules) kills the first one's processes with SIGKILL.
    Either way `exec_script` returns only once the first exec has stopped running. Where the
    shebang interpreter is checked below, a leading `timeout ...` wrapper is ignored: the rest
    of `Cmd` is still exactly the interpreter and `-s`. test_issue_117_kill_timeouts.py has the
    kill exam.
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


def timeout_wrapper(cmd: list[str]) -> tuple[str, float] | None:
    """(signal, seconds) when `cmd` starts with `timeout [-s SIG] [-k D] <n> ...`, else None."""
    if not cmd or cmd[0].rsplit("/", 1)[-1] != "timeout":
        return None
    sig, i = "TERM", 1
    while i < len(cmd) and cmd[i].startswith("-"):
        if cmd[i] in ("-s", "--signal") and i + 1 < len(cmd):
            sig, i = cmd[i + 1], i + 2
        elif cmd[i].startswith("--signal="):
            sig, i = cmd[i].split("=", 1)[1], i + 1
        elif cmd[i] in ("-k", "--kill-after") and i + 1 < len(cmd):
            i += 2
        elif cmd[i].startswith("-s") and len(cmd[i]) > 2:
            sig, i = cmd[i][2:], i + 1
        else:
            i += 1
    try:
        return sig.removeprefix("SIG"), float(cmd[i].rstrip("s"))
    except (IndexError, ValueError):
        return None


def inner_cmd(cmd: list[str]) -> list[str]:
    """`cmd` without a leading `timeout ... <n>` wrapper (#117 mechanism (a))."""
    if timeout_wrapper(cmd) is None:
        return cmd
    i = 1
    while cmd[i].startswith("-") or not cmd[i].rstrip("s").replace(".", "").isdigit():
        i += 2 if cmd[i] in ("-s", "--signal", "-k", "--kill-after") else 1
    return cmd[i + 1 :]


def is_kill_exec(cmd: list[str]) -> bool:
    """#117/#151: the kill exec (mechanism (b)) is recognised by its command alone. A script exec
    always ends in `-s` (with or without a `timeout` wrapper); the kill exec runs a shell
    program (`sh -c <program> ...`) that mentions kill and does not end in a bare `-s`. What a
    script says on stdin (a script may well contain the word kill) never decides."""
    return (
        timeout_wrapper(cmd) is None
        and bool(cmd)
        and cmd[-1] != "-s"
        and "kill" in " ".join(cmd).lower()
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
        # #117: a kill exec (mechanism (b)) is recognised by its Cmd (`is_kill_exec`), not stdin.
        self.kill_runs: list[tuple[dict, bytes]] = []
        # #151 knobs: a kill that does nothing (the script stays alive), and an exec inspect
        # that answers garbage (HTTP 500, not JSON) until a kill exec has been started.
        self.kill_effective = True
        self.json_fails_until_kill = False
        self.kill_delay = 0.0  # seconds the kill exec itself takes
        self.gone_delay = 0.0  # seconds from the kill until the killed process is really gone
        self.finished_at: dict[str, float] = {}
        self._wake: dict[str, asyncio.Event] = {}
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

    async def _stop_when_killed(self, eid: str, evt: asyncio.Event) -> None:
        await evt.wait()
        await asyncio.sleep(self.gone_delay)
        self.exit_codes[eid] = 137
        self.finished.add(eid)
        self.finished_at[eid] = time.monotonic()

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
            if is_kill_exec(create.get("Cmd", [])):
                self.kill_runs.append((create, stdin))
                await asyncio.sleep(self.kill_delay)
                for other, evt in self._wake.items():  # the killed processes die now
                    if self.kill_effective and other not in self.finished:
                        evt.set()
                self.exit_codes[eid] = 0
                self.finished.add(eid)
                self.finished_at[eid] = time.monotonic()
                return True
            self.stdin.append(stdin)
            plan = self.behaviour(create, stdin)
            delay, code = plan.get("delay", 0.0), plan.get("exit", 0)
            if plan.get("stream") in ("ends_early", "reset"):
                # #151: the output stream ends (or the connection is reset) while the script
                # lives on: it stops only when a kill exec reaches it.
                evt = self._wake[eid] = asyncio.Event()
                if plan["stream"] == "reset":
                    w.transport.abort()
                asyncio.ensure_future(self._stop_when_killed(eid, evt))
                return True
            wrapped = timeout_wrapper(create.get("Cmd", []))
            if wrapped and delay > wrapped[1]:  # `timeout -s KILL n` ends the script at n
                delay, code = wrapped[1] + 0.3, 137
            evt = self._wake[eid] = asyncio.Event()
            frames = plan.get("frames", [])
            try:
                if delay:
                    try:
                        await asyncio.wait_for(evt.wait(), delay)
                        code, frames = 137, []  # killed by a kill exec
                        await asyncio.sleep(self.gone_delay)
                    except TimeoutError:
                        pass
                for fr in frames:
                    w.write(fr)
                    await w.drain()
            except (ConnectionError, OSError):
                pass  # the client stopped reading: the process still ends below
            finally:
                self.exit_codes[eid] = code
                self.finished.add(eid)
                self.finished_at[eid] = time.monotonic()
            return True
        m = re.fullmatch(r"/exec/([^/]+)/json", path)
        if m and method == "GET":
            eid = m.group(1)
            if self.json_fails_until_kill and not self.kill_runs and eid == "exec1":
                w.write(b"HTTP/1.1 500 Error\r\nContent-Length: 4\r\n\r\nboom")
                await w.drain()
                return False
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
    assert inner_cmd(engine.creates[0]["Cmd"]) == cmd


def test_an_env_shebang_keeps_its_interpreter_and_adds_dash_s(engine):
    run(engine, "#!/usr/bin/env python3\nprint(1)\n")
    assert inner_cmd(engine.creates[0]["Cmd"]) == ["/usr/bin/env", "python3", "-s"]


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
    assert len(inner_cmd(cmd)) <= 3 and "-c" not in cmd, cmd
    assert not any(a in ("tee", "cat", "dd") for a in cmd)


def test_stdin_is_attached_and_closed_so_the_interpreter_sees_end_of_input(engine):
    run(engine, SCRIPT_SH)
    body = engine.creates[0]
    assert body["AttachStdin"] is True
    assert body["AttachStdout"] is True and body["AttachStderr"] is True
    assert engine.stdin, "the fake saw no end of input: the client did not close its write side"


# ---------------------------------------------------------------- the fake itself (#151 item 7)


def test_a_script_that_mentions_kill_is_not_taken_for_a_kill_exec(engine):
    """The fake tells the kill exec by its command; the script's own text is just a script."""
    script = "#!/bin/sh\n# kill it with fire\npkill -f nothing-at-all\necho kill\n"
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [frame(1, b"kill\n")]}
    res = run(engine, script, timeout=5)
    assert res.exit_code == 0 and res.timed_out is False and "kill" in res.output
    assert engine.kill_runs == [] and engine.stdin == [script.encode()]


def test_a_timed_out_script_that_mentions_kill_still_gets_exactly_one_kill_exec(engine):
    script = "#!/bin/sh\npkill -f nothing-at-all\nsleep 600\n"
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [], "delay": 600.0}
    res = run(engine, script, timeout=1)
    assert res.timed_out is True
    assert len(engine.kill_runs) == 1 and engine.stdin == [script.encode()]
    assert is_kill_exec(engine.kill_runs[0][0]["Cmd"])
    assert not is_kill_exec(engine.creates[0]["Cmd"])


def test_the_fake_inspect_answer_has_no_host_pid(engine):
    """The runner never needs the exec's Pid (a host pid under runsc, D56): the fake sends none."""
    import urllib.request

    run(engine, SCRIPT_SH)
    with urllib.request.urlopen(f"http://127.0.0.1:{engine.port}/exec/exec1/json") as r:
        assert "Pid" not in json.loads(r.read())


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
    # #306 (D98): one extra variable, the timeout kill's marker for this exec, nothing else.
    marker = env.pop("LF_EXEC_ID", None)
    assert marker is None or re.fullmatch(r"[0-9a-f]{32}", marker), marker
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
