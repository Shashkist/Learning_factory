"""The timeout killer's command (#306): self-enforced deadline, per-exec marker."""

import shutil
import subprocess

import pytest

from app.runtime import KILL_SCRIPT, kill_command


def test_killer_runs_under_a_kill_deadline_and_gets_the_marker():
    cmd = kill_command("abc123", 4.4)
    assert cmd[:2] == ["/bin/sh", "-c"]
    assert "exec timeout -s KILL" in cmd[2]
    assert cmd[4:] == ["4.4", KILL_SCRIPT, "abc123"]


def test_killer_matches_by_environment_marker_not_by_interpreter_args():
    assert "LF_EXEC_ID=$id" in KILL_SCRIPT
    assert "pp[p] == 0" not in KILL_SCRIPT  # the old `<interp> -s` + PPid 0 match is gone


def test_student_kill_children_are_not_waited_for():
    lines = KILL_SCRIPT.splitlines()
    i = next(i for i, x in enumerate(lines) if "setpriv" in x)
    line = lines[i] + lines[i + 1]  # the command continues on the next line
    assert line.rstrip().endswith("&")
    assert ">/dev/null" in line  # a stopped child must not hold the killer's output open


def test_student_groups_are_signalled_before_root_parents():
    assert KILL_SCRIPT.index("setpriv") < KILL_SCRIPT.index('kill -"$1" $rootpids')


@pytest.mark.skipif(shutil.which("sh") is None, reason="no sh")
def test_kill_script_is_valid_shell():
    out = subprocess.run(["sh", "-n", "-c", KILL_SCRIPT], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
