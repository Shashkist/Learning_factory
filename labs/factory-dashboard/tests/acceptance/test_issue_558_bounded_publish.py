"""Exam for issue #558: `scripts/factory_progress.py publish` is bounded in time (D147).

Observable behaviour only: `publish` runs as a real subprocess with a fake `ssh` first on PATH.

Contract pinned here:
  - LF_PUBLISH_TIMEOUT_S (default 30) bounds the push; on timeout the whole ssh process tree is
    killed, the state file (`<key>.last`) says `fail <time>`, and `publish` still exits 0.
  - ssh output goes to the null device, so a grandchild that inherits it cannot hold the caller's
    captured pipes open.
  - a watchdog ends the publish process at timeout + 15 s even when something before the push hangs.
  - the ssh command line carries ServerAliveInterval, ServerAliveCountMax, ConnectTimeout and
    BatchMode=yes.
  - a successful push records `<content hash> <time>` and sends the rendered live JSON on stdin.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="needs POSIX process groups, a shebang fake ssh and FIFOs; CI (Linux) is the judge")

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "factory_progress.py"
TIMEOUT_S = 2
SLACK_S = 8


def _load():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("factory_progress_exam558", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie nobody reaped is not running
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _gone(pid: int, wait: float = 5.0) -> bool:
    end = time.time() + wait
    while time.time() < end:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.key = tmp / "key"
        self.key.write_text("dummy, never a real key\n")
        self.state = Path(str(self.key) + ".last")
        self.events = tmp / "progress.jsonl"
        self.pids = tmp / "pids"
        self.pids.write_text("")
        self.env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                    "LF_PROGRESS_FILE": str(self.events), "LF_PUBLISH_TIMEOUT_S": str(TIMEOUT_S),
                    "PYTHONUNBUFFERED": "1"}
        self.env.pop("LF_CREW_DIR", None)

    def fake_ssh(self, body: str) -> None:
        f = self.bin / "ssh"
        f.write_text(f"#!{sys.executable}\nimport json, os, sys, time, subprocess\n"
                     f"def note(p):\n    open({str(self.pids)!r}, 'a').write(str(p) + '\\n')\n"
                     f"note(os.getpid())\n{body}")
        f.chmod(0o755)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True,
                              timeout=TIMEOUT_S + 15 + SLACK_S + 10)

    def publish(self) -> subprocess.CompletedProcess:
        return self.run_cli("publish", "--key", str(self.key), "--host", "x@example.invalid")

    def recorded_pids(self) -> list[int]:
        return [int(x) for x in self.pids.read_text().split()]


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    for args in (["pass-start", "--pass-no", "1", "--budget", "10"],
                 ["add", "--issue", "558", "--title", "bounded publish", "--agent", "exam-writer-558",  # noqa: E501
                  "--role", "exam-writer", "--station", "exam", "--substep", "write", "--status", "start"]):  # noqa: E501
        assert e.run_cli(*args).returncode == 0
    yield e
    for pid in e.recorded_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def test_hanging_ssh_returns_within_the_timeout_records_fail_and_leaves_no_process(env):
    env.fake_ssh("time.sleep(300)\n")
    t0 = time.time()
    r = env.publish()
    took = time.time() - t0
    assert r.returncode == 0
    assert took < TIMEOUT_S + SLACK_S, f"publish took {took:.1f}s, bound is {TIMEOUT_S}+{SLACK_S}"
    assert env.state.read_text().split()[0] == "fail"
    pids = env.recorded_pids()
    assert pids, "the fake ssh was never started"
    assert all(_gone(p) for p in pids), "a fake ssh process is still running"


def test_hanging_ssh_with_a_background_grandchild_does_not_hold_the_callers_pipes(env):
    env.fake_ssh("g = subprocess.Popen(['sleep', '300'])\nnote(g.pid)\ntime.sleep(300)\n")
    t0 = time.time()
    r = env.publish()   # capture_output=True with its own timeout: a regression raises TimeoutExpired  # noqa: E501
    assert time.time() - t0 < TIMEOUT_S + SLACK_S
    assert r.returncode == 0
    assert env.state.read_text().split()[0] == "fail"


def test_successful_ssh_records_the_content_hash_and_receives_the_rendered_live_json(env):
    got = env.tmp / "stdin.bin"
    env.fake_ssh(f"open({str(got)!r}, 'wb').write(sys.stdin.buffer.read())\n")
    r = env.publish()
    assert r.returncode == 0
    fp = _load()
    sent = json.loads(got.read_bytes().decode("utf-8"))
    os.environ["LF_PROGRESS_FILE"] = str(env.events)
    try:
        expected = fp.render(fp.read_events())
    finally:
        del os.environ["LF_PROGRESS_FILE"]
    assert set(sent) == set(expected)
    assert [i["issue"] for i in sent["issues"]] == [i["issue"] for i in expected["issues"]] == [558]
    first, stamp = env.state.read_text().split()
    assert first == fp.content_hash(sent)
    float(stamp)


def test_ssh_command_line_has_keepalive_connect_timeout_and_batch_mode(env):
    argv_file = env.tmp / "argv.json"
    env.fake_ssh(f"open({str(argv_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\nsys.stdin.buffer.read()\n")  # noqa: E501
    assert env.publish().returncode == 0
    argv = json.loads(argv_file.read_text())
    joined = " ".join(argv)
    for opt in ("ServerAliveInterval=", "ServerAliveCountMax=", "ConnectTimeout="):
        assert opt in joined, f"{opt} missing from {argv}"
    assert "BatchMode=yes" in joined
    assert "x@example.invalid" in argv


def test_watchdog_ends_a_publish_whose_render_hangs_and_records_fail(env):
    # A FIFO as the events file: reading it blocks forever, standing in for a hanging render.
    env.events.unlink()
    os.mkfifo(env.events)
    env.fake_ssh("sys.stdin.buffer.read()\n")
    t0 = time.time()
    p = subprocess.Popen([sys.executable, str(SCRIPT), "publish", "--key", str(env.key),
                          "--host", "x@example.invalid"], env=env.env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        p.wait(timeout=TIMEOUT_S + 15 + SLACK_S)
    except subprocess.TimeoutExpired:
        p.kill()
        pytest.fail("publish outlived LF_PUBLISH_TIMEOUT_S + 15 s: no watchdog")
    assert time.time() - t0 >= 1, "it must not exit before even trying"
    assert env.state.exists() and env.state.read_text().split()[0] == "fail"
