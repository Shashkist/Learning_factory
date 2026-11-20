"""Acceptance tests for issue #117 (criteria 1 to 3): kill timed-out scripts, authoring rules.

No Docker here (the real thing under runsc is test_issue_117_kill_timeouts_docker.py). The
runner part drives `DockerRuntime.exec_script` against the fake Docker Engine of
test_issue_9_runner.py, whose contract allows two kill mechanisms (see its docstring):
(a) `Cmd` wrapped as `timeout -s KILL <n> ...`, or (b) a second exec that sends SIGKILL.

Criteria covered:

1. A script that outlives its timeout is killed, not abandoned.
2. The setup lock of #9 stays held until the killed process is gone. The lock (D49/D51) is
   per session, never waited for (a second Restart/show/check gets 409, runs nothing) and is
   held for as long as `exec_script` runs. So the runner-level promise is: `exec_script` does
   not return before the killed process has stopped. Nothing changes in the 409 behaviour.
3. Authoring rules as content validator WARNINGS (the lab is still served):
   `LoadResult.warnings` is a dict like `errors`: key = lab dir relative to the content root
   (`test/good`), value = list of non-empty strings. A lab without findings has no entry.
   Each warning names the task id and the script (`setup.sh` or `check.sh`) and:
   - STDIN rule: mentions `</dev/null`. Fires for a command that reads stdin and has no
     stdin redirect. Exactly these commands count as readers: the builtin `read`, and `cat`
     with no operand other than options (`cat`, `cat -`). A command is the first word of a
     line or the first word after `;`, `&&` or `||` (not after a single `|`: it then reads
     the pipe). Any `<` on that command (file, `</dev/null`, heredoc `<<`) clears it.
     Comment lines are ignored.
   - BACKGROUND rule: mentions `>/dev/null 2>&1`. Fires for a line whose last token is a
     single `&` (background job; not `&&`) unless the line contains `>/dev/null 2>&1`
     (also `> /dev/null 2>&1`, `&>/dev/null`, `&> /dev/null`). Comment lines are ignored.
   The spec §7 note itself is checked by the reviewer, not here.
"""
# ruff: noqa: F811
import logging
import time

import pytest
from test_issue_5_content_loader import make_lab
from test_issue_9_runner import (  # noqa: F401  (engine is a fixture, used by name)
    FakeEngine,
    engine,
    frame,
    run,
    timeout_wrapper,
)

from app.content import load_labs

SLEEPER = "#!/bin/sh\necho started\nsleep 600\n"
TASK = "02-make-file"


# ================================================================ criteria 1 and 2: the runner


def _timed_out_run(engine, script=SLEEPER, timeout=1):
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [], "delay": 600.0}
    started = time.monotonic()
    res = run(engine, script, timeout=timeout)
    return res, time.monotonic() - started


def _killed_by_wrapper(engine, timeout) -> bool:
    wrapped = timeout_wrapper(engine.creates[0]["Cmd"])
    if wrapped is None:
        return False
    sig, seconds = wrapped
    assert sig in ("KILL", "9"), f"the wrapper must send SIGKILL, not {sig}"
    assert timeout <= seconds <= timeout + 5, f"wrapper deadline {seconds}s for timeout {timeout}"
    return True


def _kill_text(engine) -> str:
    body, stdin = engine.kill_runs[0]
    return (" ".join(body["Cmd"]) + " " + stdin.decode()).upper()


def _killed_by_second_exec(engine) -> bool:
    if len(engine.kill_runs) != 1 or len(engine.creates) != 2:
        return False
    body, _ = engine.kill_runs[0]
    text = _kill_text(engine)
    assert "KILL" in text or "-9" in text, text  # SIGKILL: a script may trap TERM
    assert "-TERM" not in text and "-15" not in text, text
    assert body["User"] in ("root", "0", "0:0")
    assert not body.get("Privileged")
    assert not any("cap" in k.lower() for k in body), body
    return True


def test_a_script_that_outlives_its_timeout_is_killed_not_abandoned(engine):
    res, _ = _timed_out_run(engine, timeout=1)
    assert res.timed_out is True
    assert _killed_by_wrapper(engine, 1) or _killed_by_second_exec(engine), (
        f"no kill: creates={[c['Cmd'] for c in engine.creates]}, paths={engine.paths()}"
    )


def test_a_timed_out_script_is_still_reported_as_timed_out_within_the_deadline(engine):
    res, took = _timed_out_run(engine, timeout=1)
    assert res.timed_out is True and res.exit_code != 0
    assert took < 8, "the runner waited for the script, not the timeout plus the kill"


def test_a_script_that_finishes_in_time_is_not_killed(engine):
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [frame(1, b"fine\n")]}
    res = run(engine, "#!/bin/sh\necho fine\n", timeout=5)
    assert res.timed_out is False and res.exit_code == 0
    assert engine.kill_runs == [] and len(engine.creates) == 1


def test_a_timed_out_script_does_not_make_the_next_run_fail(engine):
    _timed_out_run(engine, timeout=1)
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [frame(1, b"again\n")]}
    res = run(engine, "#!/bin/sh\necho again\n", timeout=5)
    assert res.exit_code == 0 and res.timed_out is False and "again" in res.output


@pytest.mark.parametrize("script", ["#!/bin/sh\nsleep 600\n", "#!/bin/bash\nsleep 600 &\nwait\n"])
def test_every_kind_of_script_goes_through_the_same_kill_path(engine, script):
    res, _ = _timed_out_run(engine, script=script, timeout=1)
    assert res.timed_out is True
    assert _killed_by_wrapper(engine, 1) or _killed_by_second_exec(engine)


def test_the_setup_lock_stays_held_until_the_killed_process_is_gone(engine):
    """exec_script (which the session lock wraps) returns only after the exec stopped running."""
    engine.gone_delay = 1.5  # the process needs 1.5 s after the kill to really be gone
    res, _ = _timed_out_run(engine, timeout=1)
    returned = time.monotonic()
    assert res.timed_out is True
    assert "exec1" in engine.finished, "exec_script returned while the script still ran"
    assert returned >= engine.finished_at["exec1"] - 0.05, (
        "exec_script returned before the killed process was gone: a Restart could run on top"
    )


def test_a_slow_kill_delays_the_return_it_never_leaves_the_script_running(engine):
    engine.kill_delay = 1.0
    engine.gone_delay = 1.0
    _timed_out_run(engine, timeout=1)
    returned = time.monotonic()
    assert "exec1" in engine.finished
    assert returned >= engine.finished_at["exec1"] - 0.05


class _KillFailsEngine(FakeEngine):
    """Every exec create after the first (the kill exec of mechanism (b)) is refused with a 500."""

    async def _route(self, method, path, body, headers, r, w) -> bool:
        if method == "POST" and path.endswith("/exec") and self.creates:
            await self._reply(w, "500 Error", {"message": "kill refused"})
            return False
        return await super()._route(method, path, body, headers, r, w)


@pytest.fixture
def broken_kill_engine(monkeypatch):
    eng = _KillFailsEngine()
    eng.start()
    monkeypatch.setenv("LF_DOCKER_HOST", f"tcp://127.0.0.1:{eng.port}")
    yield eng
    eng.stop()


def test_a_failing_kill_still_reports_timed_out_not_an_error(broken_kill_engine):
    res, took = _timed_out_run(broken_kill_engine, timeout=1)
    assert res.timed_out is True and res.exit_code != 0
    assert took < 20, "a failing kill must not stall exec_script"


def test_a_hanging_kill_is_bounded_and_still_reports_timed_out(engine):
    engine.kill_delay = 600.0  # the kill exec never answers
    res, took = _timed_out_run(engine, timeout=1)
    assert res.timed_out is True and res.exit_code != 0
    assert took < 30, "an unbounded kill held the setup lock for good"


# ================================================================ criterion 3: authoring rules


def lab_with_script(tmp_path, content: str, name: str = "setup.sh"):
    make_lab(tmp_path, "test/good")
    script = tmp_path / "test" / "good" / "tasks" / TASK / name
    script.write_text(content, encoding="utf-8")
    script.chmod(0o755)
    return load_labs(tmp_path)


def warnings_of(result) -> list[str]:
    assert hasattr(result, "warnings"), "LoadResult has no `warnings`"
    return [w.lower() for w in result.warnings.get("test/good", [])]


def stdin_warnings(result):
    return [w for w in warnings_of(result) if "</dev/null" in w]


def background_warnings(result):
    return [w for w in warnings_of(result) if ">/dev/null 2>&1" in w]


def test_loadresult_warnings_defaults_to_an_empty_dict():
    from app.content import LoadResult

    assert LoadResult().warnings == {}


def test_a_lab_without_findings_has_no_warnings(tmp_path):
    result = lab_with_script(tmp_path, "#!/bin/sh\nexit 0\n")
    assert "good" in result.labs
    assert result.warnings.get("test/good", []) == []
    assert result.errors == {}


STDIN_READERS = [
    "read answer",
    "read -r line",
    "cat",
    "cat -",
    "cat -n",
    "true && read x",
    "false || cat",
    "echo hi; read x",
    "  read x",
]


@pytest.mark.parametrize("name", ["setup.sh", "check.sh"])
@pytest.mark.parametrize("line", STDIN_READERS)
def test_a_command_that_reads_stdin_without_dev_null_is_a_warning(tmp_path, name, line):
    result = lab_with_script(tmp_path, f"#!/bin/sh\n{line}\nexit 0\n", name)
    found = stdin_warnings(result)
    assert found, f"no </dev/null warning for {line!r}: {result.warnings}"
    assert any(name in w and TASK in w for w in found), found


STDIN_SAFE = [
    "read x </dev/null",
    "read x < /dev/null",
    "read -r line < /tmp/input",
    "cat </dev/null",
    "cat /etc/hostname",
    "cat -n /etc/hostname",
    "cat > /tmp/f <<EOF\ntext\nEOF",
    "cat <<EOF\ntext\nEOF",
    "echo hi | cat",
    "echo hi | read x",
    "echo read me",
    'echo "cat"',
    "grep read /etc/hostname",
    "reader --now",
    "mycat",
    "# read the answer",
    "# cat",
]


@pytest.mark.parametrize("name", ["setup.sh", "check.sh"])
@pytest.mark.parametrize("line", STDIN_SAFE)
def test_a_command_that_does_not_read_stdin_gets_no_stdin_warning(tmp_path, name, line):
    result = lab_with_script(tmp_path, f"#!/bin/sh\n{line}\nexit 0\n", name)
    assert stdin_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"


BACKGROUND_BAD = [
    "python3 -m http.server 8080 &",
    "nohup sleep 600 &",
    "sleep 600 &   ",
    "sleep 600 >/dev/null &",
    "sleep 600 2>&1 &",
    "sleep 600 >/tmp/log &",
    "setsid foo >/dev/null &",
    "sleep 600 </dev/null &",
]


@pytest.mark.parametrize("name", ["setup.sh", "check.sh"])
@pytest.mark.parametrize("line", BACKGROUND_BAD)
def test_a_background_command_without_dev_null_redirects_is_a_warning(tmp_path, name, line):
    result = lab_with_script(tmp_path, f"#!/bin/sh\n{line}\nexit 0\n", name)
    found = background_warnings(result)
    assert found, f"no >/dev/null 2>&1 warning for {line!r}: {result.warnings}"
    assert any(name in w and TASK in w for w in found), found


BACKGROUND_OK = [
    "sleep 600 >/dev/null 2>&1 &",
    "sleep 600 > /dev/null 2>&1 &",
    "sleep 600 </dev/null >/dev/null 2>&1 &",
    "true && echo done",
    "true || echo failed",
    "echo hi 2>&1",
    "# sleep 600 &",
    'echo "tom & jerry"',
]


@pytest.mark.parametrize("name", ["setup.sh", "check.sh"])
@pytest.mark.parametrize("line", BACKGROUND_OK)
def test_a_quiet_background_command_or_no_background_gets_no_warning(tmp_path, name, line):
    result = lab_with_script(tmp_path, f"#!/bin/sh\n{line}\nexit 0\n", name)
    assert background_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"


def test_warnings_do_not_stop_the_lab_from_being_served(tmp_path):
    result = lab_with_script(tmp_path, "#!/bin/sh\nread x\nsleep 600 &\nexit 0\n")
    assert "good" in result.labs, "an authoring warning must never take a lab offline"
    assert "test/good" not in result.errors
    assert stdin_warnings(result) and background_warnings(result)


def test_each_script_is_reported_with_its_own_name(tmp_path):
    result = lab_with_script(tmp_path, "#!/bin/sh\nread x\nexit 0\n", "check.sh")
    found = stdin_warnings(result)
    assert found and all("check.sh" in w for w in found)
    assert not any("setup.sh" in w for w in found)


def test_the_shipped_sample_labs_follow_the_authoring_rules():
    from pathlib import Path

    root = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
    result = load_labs(root / "labs" / "content")
    assert result.labs, result.errors
    assert not result.warnings, dict(result.warnings)


def test_the_loaders_warnings_are_logged_one_line_each_at_warning_level(
    tmp_path, monkeypatch, caplog
):
    """Observable: starting the app (lifespan, no database) with LF_CONTENT_DIR on a lab that
    has findings. Every entry of `LoadResult.warnings` shows up as one WARNING record of the
    manager's logger ("lab-manager") whose text contains that warning."""
    from fastapi.testclient import TestClient

    lab_with_script(tmp_path, "#!/bin/sh\nread x\nsleep 600 &\nexit 0\n")
    expected = [w for ws in load_labs(tmp_path).warnings.values() for w in ws]
    assert len(expected) >= 2, expected
    monkeypatch.setenv("LF_CONTENT_DIR", str(tmp_path))
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    monkeypatch.setenv("LF_DOCKER_HOST", f"unix://{tmp_path / 'no-docker.sock'}")
    monkeypatch.setenv("LF_JANITOR_INTERVAL_S", "3600")
    from app.main import app

    with caplog.at_level(logging.WARNING, logger="lab-manager"), TestClient(app):
        pass
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "lab-manager" and r.levelno == logging.WARNING
    ]
    for w in expected:
        hits = [ln for ln in lines if w in ln]
        assert len(hits) == 1, f"warning {w!r} logged {len(hits)} times; lines={lines}"
