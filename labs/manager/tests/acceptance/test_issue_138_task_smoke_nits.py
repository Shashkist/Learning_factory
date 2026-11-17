"""Acceptance tests for issue #138: follow-ups of the task smoke (#128, review of #135).

Three items:
  1. The `client` step of .github/workflows/server-smoke.yml streams the task smoke's lines to
     the log as they happen, keeps its exit status, bounds the run with a time limit shorter
     than the job's, and records `tasks_sid` as soon as `ok start <uuid>` appears, so the
     container-gone check still looks for that session when the smoke hangs and is cut off.
  2. tasks.py no longer has its private `_Early` exception (it raises `StepFail`).
  3. Login and start are shared: terminal.py and tasks.py import one helper module that lives
     in labs/deploy/smoke/.

Item 1 is tested by running the step's real `run:` script with bash under stubbed `ssh`,
`openssl`, `python` and `timeout` (the stub keeps the requested limit and shortens it to two
seconds, so the exam is fast whatever limit the step asks for). All values are synthetic.
"""

import ast
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "server-smoke.yml"
SMOKE_DIR = REPO_ROOT / "labs" / "deploy" / "smoke"
TASKS = SMOKE_DIR / "tasks.py"
TERMINAL = SMOKE_DIR / "terminal.py"

TERMINAL_SID = "11111111-1111-4111-8111-111111111111"
TASKS_SID = "22222222-2222-4222-8222-222222222222"
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
CUT = 2  # seconds the `timeout` stub really waits


# ---------------------------------------------------------------- the workflow step


def _load() -> dict:
    yaml = pytest.importorskip("yaml")
    assert WORKFLOW.is_file(), "server-smoke.yml is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _client_step() -> tuple[str, int]:
    """The `run:` script of the step with id `client`, and the job's timeout in seconds."""
    job = _load()["jobs"]["smoke"]
    for step in job["steps"]:
        if step.get("id") == "client":
            return step["run"], int(job.get("timeout-minutes", 360)) * 60
    raise AssertionError("no step with id `client`")


def _find_bash() -> str | None:
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    if sys.platform == "win32":
        return None  # the System32 bash is WSL: not usable here
    return shutil.which("bash")


BASH = _find_bash()
REAL_TIMEOUT = shutil.which("timeout") if sys.platform != "win32" else None
if sys.platform == "win32" and BASH:
    gb = Path(BASH).parent
    for d in (gb, gb.parent / "usr" / "bin"):
        if (d / "timeout.exe").is_file():
            REAL_TIMEOUT = "/usr/bin/timeout"
            break

needs_bash = pytest.mark.skipif(
    not (BASH and REAL_TIMEOUT), reason="needs a POSIX bash with coreutils `timeout`"
)

PYTHON_STUB = f"""#!/bin/bash
case "$1" in
  *terminal.py)
    case "$FAKE_TERMINAL" in
      hang) echo "{TERMINAL_SID}"; exec sleep 60 ;;
      *) echo "{TERMINAL_SID}"; exit 0 ;;
    esac ;;
  *tasks.py)
    case "$FAKE_TASKS" in
      pass)
        echo "ok start {TASKS_SID}"; echo "ok terminal"; echo "ok end"; exit 0 ;;
      fail)
        echo "ok start {TASKS_SID}"; echo "smoke failed: check-fail: boom" >&2; exit 3 ;;
      hang)
        echo "ok start {TASKS_SID}"; exec sleep 3600 ;;
    esac ;;
esac
echo "unexpected python call: $*" >&2
exit 99
"""

# Records the limit the step asks for, then runs the command with a limit of CUT seconds.
TIMEOUT_STUB = f"""#!/bin/bash
args=("$@")
i=0
while [ $i -lt ${{#args[@]}} ]; do
  a="${{args[$i]}}"
  case "$a" in
    -k|-s|--kill-after|--signal) i=$((i+2)); continue ;;
    -*) i=$((i+1)); continue ;;
    *) break ;;
  esac
done
echo "${{args[$i]}}" >> "$STUB_DIR/timeout.log"
echo "$*" >> "$STUB_DIR/timeout.cmds"
args[$i]={CUT}
exec {REAL_TIMEOUT or "timeout"} "${{args[@]}}"
"""

SSH_STUB = "#!/bin/bash\ncat >/dev/null 2>&1 </dev/stdin || true\nexit 0\n"
OPENSSL_STUB = "#!/bin/bash\necho 0123456789abcdef0123456789abcdef0123456789abcdef\n"


def _prepare(tmp_path: Path, mode: str, terminal: str = "pass"):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (
        ("python", PYTHON_STUB),
        ("python3", PYTHON_STUB),
        ("ssh", SSH_STUB),
        ("openssl", OPENSSL_STUB),
        ("timeout", TIMEOUT_STUB),
    ):
        f = stubs / name
        f.write_bytes(body.encode())  # LF endings
        f.chmod(0o755)
    runner = tmp_path / "runner"
    runner.mkdir()
    (runner / "ssh_config").write_text("", encoding="utf-8")
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    script, job_seconds = _client_step()
    sh = tmp_path / "step.sh"
    sh.write_bytes(script.encode())
    env = dict(os.environ)
    env.update(
        PATH=str(stubs) + os.pathsep + env.get("PATH", ""),
        RUNNER_TEMP=str(runner),
        GITHUB_OUTPUT=str(out),
        STUB_DIR=str(stubs),
        FAKE_TASKS=mode,
        FAKE_TERMINAL=terminal,
        CHECK="terminal",
    )
    return sh, env, out, stubs, job_seconds


def _run_step(tmp_path: Path, mode: str, terminal: str = "pass"):
    """Run the step like Actions does (`bash -e`), timestamping each stdout line."""
    sh, env, out, stubs, job_seconds = _prepare(tmp_path, mode, terminal)
    assert BASH
    proc = subprocess.Popen(
        # Git Bash puts its own /usr/bin first: put the stubs in front again, then run the step
        [
            BASH,
            "-e",
            "-c",
            'export PATH="$(cygpath -u "$STUB_DIR" 2>/dev/null || echo "$STUB_DIR"):$PATH"; . "$0"',
            str(sh),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    seen: list[tuple[float, str]] = []

    def reader() -> None:
        assert proc.stdout
        for raw in proc.stdout:
            seen.append((time.monotonic(), raw.decode(errors="replace").rstrip("\r\n")))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        proc.kill()
        t.join(5)
        pytest.fail(f"the step did not end within 20 s (mode {mode}): no time limit on the smoke?")
    ended = time.monotonic()
    t.join(10)
    stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    outputs = out.read_text(encoding="utf-8").splitlines()
    limits = (stubs / "timeout.log").read_text().split() if (stubs / "timeout.log").exists() else []
    return proc.returncode, seen, ended, stderr, outputs, limits, job_seconds


def _seconds(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", text)
    assert m, f"cannot read the time limit {text!r}"
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def _sids(outputs: list[str]) -> set[str]:
    return {o.split("=", 1)[1] for o in outputs if o.startswith("tasks_sid=")}


# ---------------------------------------------------------------- item 1


@pytest.fixture(scope="module")
def hung(tmp_path_factory):
    """One run of the step where tasks.py prints `ok start` and then hangs."""
    return _run_step(tmp_path_factory.mktemp("hang"), "hang")


@needs_bash
def test_task_smoke_lines_stream_to_the_log_before_the_smoke_ends(hung):
    rc, seen, ended, *_ = hung
    starts = [ts for ts, line in seen if line == f"ok start {TASKS_SID}"]
    assert starts, f"the `ok start` line never reached the log; got {[ln for _, ln in seen]}"
    assert ended - starts[0] >= CUT - 1, (
        "the `ok start` line only showed when the smoke ended: it is not streamed (tee)"
    )


@needs_bash
def test_task_smoke_run_is_bounded_by_a_limit_shorter_than_the_job(hung):
    rc, seen, ended, stderr, outputs, limits, job_seconds = hung
    assert limits, "the task smoke is not run under `timeout`"
    assert all(0 < _seconds(x) < job_seconds for x in limits), (
        f"limits {limits} must be shorter than the job's {job_seconds} s"
    )


@needs_bash
def test_tasks_sid_is_written_when_the_smoke_is_cut_off_by_the_time_limit(hung):
    rc, seen, ended, stderr, outputs, *_ = hung
    assert _sids(outputs) == {TASKS_SID}, f"GITHUB_OUTPUT has {outputs}"


@needs_bash
def test_step_fails_when_the_smoke_is_cut_off_by_the_time_limit(hung):
    rc, *_ = hung
    assert rc != 0, "a task smoke cut off by the time limit must fail the step"


@needs_bash
def test_the_terminal_session_id_is_still_written_when_the_task_smoke_hangs(hung):
    *_, outputs, _limits, _job = hung
    assert f"sid={TERMINAL_SID}" in outputs


@needs_bash
def test_passing_task_smoke_keeps_exit_zero_and_records_its_session(tmp_path):
    rc, seen, ended, stderr, outputs, *_ = _run_step(tmp_path, "pass")
    assert rc == 0, f"exit {rc}; stderr: {stderr}"
    lines = [line for _, line in seen]
    for want in (f"ok start {TASKS_SID}", "ok terminal", "ok end"):
        assert want in lines, f"{want!r} missing from the log {lines}"
    assert _sids(outputs) == {TASKS_SID}


@needs_bash
def test_failing_task_smoke_keeps_its_exit_status_and_records_its_session(tmp_path):
    rc, seen, ended, stderr, outputs, *_ = _run_step(tmp_path, "fail")
    assert rc == 3, f"the smoke's exit status 3 must be the step's, got {rc}"
    assert f"ok start {TASKS_SID}" in [line for _, line in seen]
    assert _sids(outputs) == {TASKS_SID}


def test_container_gone_check_still_reads_tasks_sid_output():
    job = _load()["jobs"]["smoke"]
    gone = next(s for s in job["steps"] if "container is gone" in s.get("name", ""))
    assert "steps.client.outputs.tasks_sid" in str(gone.get("if", "")) + str(gone.get("env", ""))


# ---------------------------------------------------------------- items 2 and 3


def test_tasks_py_no_longer_has_its_own_early_exception():
    src = TASKS.read_text(encoding="utf-8")
    assert "_Early" not in src, "_Early duplicates StepFail: raise StepFail instead"
    assert "StepFail" in src


def _local_imports(path: Path) -> set[str]:
    local = {p.stem for p in SMOKE_DIR.glob("*.py")}
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
    return found & local


def test_both_clients_import_one_shared_helper_module_from_the_smoke_directory():
    assert TERMINAL.is_file() and TASKS.is_file(), "terminal.py must stay next to tasks.py"
    helpers = {"terminal", "tasks"}
    shared = (_local_imports(TASKS) & _local_imports(TERMINAL)) - helpers
    assert shared, (
        "terminal.py and tasks.py must both import a shared helper module (login and start) "
        f"from labs/deploy/smoke/; tasks imports {_local_imports(TASKS)}, "
        f"terminal imports {_local_imports(TERMINAL)}"
    )


def test_login_and_start_requests_live_in_the_shared_helper_only():
    helpers = {"terminal", "tasks"}
    shared = (_local_imports(TASKS) & _local_imports(TERMINAL)) - helpers
    assert shared, "no shared helper module (see the previous test)"
    text = "\n".join((SMOKE_DIR / f"{m}.py").read_text(encoding="utf-8") for m in shared)
    assert "/api/auth/login" in text and "/api/sessions" in text
    for client in (TASKS, TERMINAL):
        assert "/api/auth/login" not in client.read_text(encoding="utf-8"), (
            f"{client.name} still carries its own login call"
        )


# ---------------------------------------------------------------- issue #156 (follow-ups of #152)
#
# 1. The terminal smoke is bounded like the task smoke, streamed, and its session id is read from
#    a file (not from `$(...)`, where a hang leaves nothing in the log and no `sid`).
# 2. A run cut off by the time limit (exit 124 or 137) says so in the log.
# 3. tasks.py ends its session when it gets SIGTERM (the `timeout` stop signal).
# 4. The unreachable `except Fail` in tasks.py `main` is gone (it would label a failure `login`).


def _timeout_commands(tmp: Path) -> list[list[str]]:
    f = tmp / "stubs" / "timeout.cmds"
    return [ln.split() for ln in f.read_text().splitlines()] if f.exists() else []


def _command_for(cmds: list[list[str]], script: str) -> list[str]:
    for tokens in cmds:
        if any(t.endswith(script) for t in tokens):
            return tokens
    raise AssertionError(f"{script} is not run under `timeout`; timeout calls: {cmds}")


def _timeout_options(tokens: list[str]) -> list[str]:
    """What the step passes to `timeout` before the command (kill-after and the limit)."""
    for i, t in enumerate(tokens):
        if t in ("python", "python3") or t.endswith("/python"):
            return tokens[:i]
    raise AssertionError(f"no python command in {tokens}")


@pytest.fixture(scope="module")
def terminal_hung(tmp_path_factory):
    """One run of the step where terminal.py prints its session id and then hangs."""
    tmp = tmp_path_factory.mktemp("termhang")
    return tmp, _run_step(tmp, "pass", "hang")


@needs_bash
def test_terminal_smoke_runs_under_the_same_timeout_as_the_task_smoke(tmp_path):
    rc, *_ = _run_step(tmp_path, "pass")
    assert rc == 0
    cmds = _timeout_commands(tmp_path)
    terminal = _timeout_options(_command_for(cmds, "terminal.py"))
    tasks = _timeout_options(_command_for(cmds, "tasks.py"))
    assert terminal == tasks, (
        f"terminal.py runs under `timeout {' '.join(terminal)}`, tasks.py under "
        f"`timeout {' '.join(tasks)}`: use the same limit and kill-after"
    )
    assert any(t in ("-k", "--kill-after") or t.startswith("--kill-after=") for t in terminal)


def test_terminal_smoke_is_not_run_inside_a_command_substitution():
    script, _ = _client_step()
    for line in script.splitlines():
        code = line.split("#", 1)[0]
        assert not re.search(r"\$\([^)]*terminal\.py", code) and not re.search(
            r"`[^`]*terminal\.py", code
        ), f"terminal.py runs inside $(...): a hang leaves nothing in the log and no sid: {line!r}"


@needs_bash
def test_a_hanging_terminal_smoke_is_cut_off_and_fails_the_step(terminal_hung):
    _tmp, (rc, *_rest) = terminal_hung
    assert rc != 0, "a terminal smoke cut off by the time limit must fail the step"


@needs_bash
def test_the_terminal_session_id_is_recorded_when_the_terminal_smoke_hangs(terminal_hung):
    _tmp, (_rc, _seen, _ended, _stderr, outputs, *_rest) = terminal_hung
    assert f"sid={TERMINAL_SID}" in outputs, f"GITHUB_OUTPUT has {outputs}"


@needs_bash
def test_terminal_smoke_output_streams_to_the_log_while_it_runs(terminal_hung):
    _tmp, (_rc, seen, ended, *_rest) = terminal_hung
    shown = [ts for ts, line in seen if TERMINAL_SID in line]
    assert shown, f"the session id never reached the log; got {[ln for _, ln in seen]}"
    assert ended - shown[0] >= CUT - 1, "the terminal smoke's output is only shown when it ends"


@needs_bash
def test_task_smoke_does_not_start_after_a_terminal_smoke_that_was_cut_off(terminal_hung):
    _tmp, (_rc, seen, _ended, _stderr, outputs, *_rest) = terminal_hung
    assert f"ok start {TASKS_SID}" not in [line for _, line in seen]
    assert _sids(outputs) == set()


@needs_bash
def test_passing_terminal_smoke_still_records_a_clean_session_id(tmp_path):
    rc, _seen, _ended, _stderr, outputs, *_rest = _run_step(tmp_path, "pass")
    assert rc == 0
    assert f"sid={TERMINAL_SID}" in outputs
    assert not [o for o in outputs if o.startswith("sid=") and o != f"sid={TERMINAL_SID}"]


CUT_OFF_LINE = re.compile(r"task smoke cut off after (\d+) ?s", re.IGNORECASE)


def _log_text(seen, stderr: str) -> str:
    return "\n".join(line for _, line in seen) + "\n" + stderr


@needs_bash
def test_a_task_smoke_cut_off_by_the_time_limit_says_so_in_the_log(hung):
    _rc, seen, _ended, stderr, *_rest = hung
    assert CUT_OFF_LINE.search(_log_text(seen, stderr)), (
        f"no `task smoke cut off after <n> s` line; log: {_log_text(seen, stderr)!r}"
    )


def test_the_cut_off_line_names_the_limit_the_task_smoke_runs_under():
    script, _ = _client_step()
    m = re.search(r"timeout\s+(?:-k\s*\S+\s+)?(\d+[smhd]?)\s+python3?\s+\S*tasks\.py", script)
    assert m, "the task smoke is not run under `timeout <limit> python ... tasks.py`"
    printed = re.search(r"cut off after (\d+) ?s", script)
    assert printed, "the step has no `cut off after <n> s` message"
    assert _seconds(printed.group(1)) == _seconds(m.group(1))


@needs_bash
def test_a_failing_task_smoke_is_not_reported_as_cut_off(tmp_path):
    rc, seen, _ended, stderr, *_rest = _run_step(tmp_path, "fail")
    assert rc == 3
    assert not CUT_OFF_LINE.search(_log_text(seen, stderr))


@needs_bash
def test_a_passing_task_smoke_is_not_reported_as_cut_off(tmp_path):
    rc, seen, _ended, stderr, *_rest = _run_step(tmp_path, "pass")
    assert rc == 0
    assert not CUT_OFF_LINE.search(_log_text(seen, stderr))


# ---- tasks.py against a local fake lab manager

import http.server  # noqa: E402
import json  # noqa: E402
import signal  # noqa: E402

FAKE_SID = "33333333-3333-4333-8333-333333333333"
FAKE_PASSWORD = "synthetic-class-password"  # noqa: S105


class _FakeManager:
    """Logs in, starts a session, then either hangs or refuses the terminal WebSocket."""

    def __init__(self, ws_mode: str, start_status: int = 200) -> None:
        self.calls: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.ws_seen = threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silence
                pass

            def _send(self, status, body=b"", headers=()):
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _record(self):
                with outer.lock:
                    outer.calls.append((self.command, self.path))

            def do_POST(self):  # noqa: N802
                self._record()
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                if self.path == "/api/auth/login":
                    self._send(200, b"{}", [("Set-Cookie", "lf_session=synthetic; Path=/")])
                elif self.path == "/api/sessions":
                    if start_status != 200:
                        self._send(start_status)
                    else:
                        self._send(200, json.dumps({"id": FAKE_SID}).encode())
                elif self.path == f"/api/sessions/{FAKE_SID}/end":
                    self._send(200, b"{}")
                else:
                    self._send(404)

            def do_GET(self):  # noqa: N802
                self._record()
                outer.ws_seen.set()
                if ws_mode == "hang":
                    outer.release.wait(60)
                    self.close_connection = True
                    return
                self._send(404)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def ends(self) -> int:
        end = f"/api/sessions/{FAKE_SID}/end"
        with self.lock:
            return sum(1 for m, p in self.calls if m == "POST" and p == end)

    def close(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


def _spawn_tasks(base: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SMOKE_")}
    env.update(SMOKE_ID="990000000000000000000000000001", SMOKE_PASSWORD=FAKE_PASSWORD)
    env["SMOKE_TIMEOUT"] = "60"
    return subprocess.Popen(
        [sys.executable, str(TASKS), base],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@pytest.fixture
def fake_hanging():
    srv = _FakeManager("hang")
    yield srv
    srv.close()


@pytest.fixture
def fake_refusing():
    srv = _FakeManager("refuse")
    yield srv
    srv.close()


def _sigterm_run(srv: _FakeManager):
    proc = _spawn_tasks(srv.base)
    try:
        assert srv.ws_seen.wait(20), "tasks.py never reached the terminal step"
        proc.send_signal(signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            pytest.fail("tasks.py did not exit within 15 s of SIGTERM")
    finally:
        if proc.poll() is None:
            proc.kill()
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


no_sigterm = pytest.mark.skipif(
    sys.platform == "win32", reason="Windows has no catchable SIGTERM (terminate() is a hard kill)"
)


@no_sigterm
def test_sigterm_ends_the_session_and_exits_non_zero(fake_hanging):
    rc, out, err = _sigterm_run(fake_hanging)
    assert fake_hanging.ends() == 1, (
        f"SIGTERM must end the session exactly once, calls: {fake_hanging.calls}"
    )
    assert rc != 0, "a smoke stopped by SIGTERM must not exit 0"
    assert "Traceback" not in out + err, "no traceback may reach the log"
    assert FAKE_PASSWORD not in out + err


@no_sigterm
def test_sigterm_keeps_the_session_id_line_in_the_log(fake_hanging):
    _rc, out, _err = _sigterm_run(fake_hanging)
    assert f"ok start {FAKE_SID}" in out.splitlines()


def test_a_failed_step_ends_the_session_exactly_once(fake_refusing):
    proc = _spawn_tasks(fake_refusing.base)
    _out, err = proc.communicate(timeout=30)
    text = err.decode(errors="replace")
    assert proc.returncode == 1, text
    assert fake_refusing.ends() == 1, f"end must be called once, calls: {fake_refusing.calls}"
    assert "smoke failed: terminal:" in text


def test_a_failed_start_is_labelled_start_not_login():
    srv = _FakeManager("refuse", start_status=500)
    try:
        proc = _spawn_tasks(srv.base)
        _out, err = proc.communicate(timeout=30)
    finally:
        srv.close()
    text = err.decode(errors="replace")
    assert proc.returncode == 1
    assert "smoke failed: start:" in text and "login" not in text
    assert srv.ends() == 0, "no session was started, so none is ended"


def test_a_failed_login_is_labelled_login():
    srv = _FakeManager("refuse")
    srv.close()  # nothing listens any more: login cannot succeed
    proc = _spawn_tasks(srv.base)
    _out, err = proc.communicate(timeout=60)
    assert proc.returncode == 1
    assert "smoke failed: login:" in err.decode(errors="replace")


def _main_function() -> ast.FunctionDef:
    tree = ast.parse(TASKS.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    t = handler.type
    if t is None:
        return {"<bare>"}
    nodes = t.elts if isinstance(t, ast.Tuple) else [t]
    return {n.id if isinstance(n, ast.Name) else getattr(n, "attr", "?") for n in nodes}


def test_main_has_no_unreachable_except_fail_branch():
    handlers = [
        h
        for n in ast.walk(_main_function())
        if isinstance(n, ast.Try)
        for h in n.handlers
        if "Fail" in _handler_names(h)
    ]
    assert not handlers, "`except Fail` in tasks.py main can no longer be reached: remove it"
