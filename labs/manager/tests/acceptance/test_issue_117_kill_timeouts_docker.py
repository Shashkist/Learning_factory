"""Acceptance tests for issue #117, Docker part: a timed-out script is killed (CI, under runsc).

Same harness as test_issue_9_runner_docker.py (real lab container from the production spec).
A script that outlives its timeout must leave no process of that exec behind: `sleep 600` with
a 2 s timeout, a background child, and a script that ignores SIGTERM. Because the setup lock
(#9, D49, D51) is held while `exec_script` runs, "no process left the moment `exec_script`
returns" is also the proof that the lock is released only after the killed process is gone.
Each test uses its own sleep duration as a marker, so the module's container is shared.
"""
# ruff: noqa: F811
import time

import pytest
from test_issue_9_runner_docker import (  # noqa: F401  (fixtures are used by name)
    box,
    call,
    docker,
    image,
    loop,
    network,
    run,
    runtime,
)

pytestmark = pytest.mark.docker

COUNT = r"""
n=0
for d in /proc/[0-9]*; do
  c=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)
  case "$c" in "sleep MARK"*) n=$((n+1));; esac
done
echo $n
"""


def sleepers(box: str, mark: int) -> int:
    res = docker("exec", "-u", "root", box, "sh", "-c", COUNT.replace("MARK", str(mark)))
    return int(res.stdout.strip())


def wait_gone(box: str, mark: int, seconds: float = 5.0) -> int:
    deadline = time.monotonic() + seconds
    left = sleepers(box, mark)
    while left and time.monotonic() < deadline:
        time.sleep(0.25)
        left = sleepers(box, mark)
    return left


def test_a_sleep_600_script_with_a_2s_timeout_leaves_no_sleep_behind(runtime, call, box):
    started = time.monotonic()
    res = run(runtime, call, box, "#!/bin/sh\nsleep 6001\n", timeout=2)
    assert res.timed_out is True
    assert time.monotonic() - started < 15
    assert wait_gone(box, 6001) == 0, "the timed-out script's sleep is still running"


def test_the_killed_process_is_already_gone_when_exec_script_returns(runtime, call, box):
    """The lock is held while exec_script runs: no process may outlive the return."""
    res = run(runtime, call, box, "#!/bin/sh\nsleep 6002\n", timeout=2)
    assert res.timed_out is True
    assert sleepers(box, 6002) == 0, "exec_script returned (lock released) with the script alive"


def test_a_background_child_of_the_script_is_killed_too(runtime, call, box):
    script = "#!/bin/sh\nsleep 6003 &\nsleep 6004 &\nwait\n"
    res = run(runtime, call, box, script, timeout=2)
    assert res.timed_out is True
    assert wait_gone(box, 6003) == 0 and wait_gone(box, 6004) == 0


def test_a_script_that_ignores_sigterm_is_killed_anyway(runtime, call, box):
    script = "#!/bin/sh\ntrap '' TERM HUP INT\nsleep 6005\nsleep 6005\n"
    res = run(runtime, call, box, script, timeout=2)
    assert res.timed_out is True
    assert wait_gone(box, 6005) == 0


def test_a_bash_script_is_killed_too(runtime, call, box):
    res = run(runtime, call, box, "#!/bin/bash\nsleep 6006\n", timeout=2)
    assert res.timed_out is True
    assert wait_gone(box, 6006) == 0


def test_repeated_timeouts_do_not_pile_up_processes(runtime, call, box):
    for _ in range(4):
        assert run(runtime, call, box, "#!/bin/sh\nsleep 6007\n", timeout=1).timed_out is True
    assert sleepers(box, 6007) == 0


def test_after_a_kill_the_container_still_runs_the_next_script(runtime, call, box):
    run(runtime, call, box, "#!/bin/sh\nsleep 6008\n", timeout=1)
    again = run(runtime, call, box, "#!/bin/sh\necho still-alive\n")
    assert again.exit_code == 0 and "still-alive" in again.output and again.timed_out is False


def test_a_script_that_finishes_in_time_is_unaffected(runtime, call, box):
    res = run(runtime, call, box, "#!/bin/sh\nsleep 1\necho done\n", timeout=20)
    assert res.timed_out is False and res.exit_code == 0 and "done" in res.output
