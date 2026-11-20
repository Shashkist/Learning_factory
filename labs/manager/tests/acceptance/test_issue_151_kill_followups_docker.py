"""Acceptance tests for issue #151, Docker part (CI, under runsc).

Criterion 5: the lab image has what the kill of a timed-out script needs: procps `ps` (with the
`ruid`/`rgid` columns), util-linux `setpriv` and `awk`. Same harness as test_issue_2 (the image
under test is the one `lab_image.ensure_lab_image()` builds).

Criteria 1 and 4 against the real container (the harness of test_issue_117_kill_timeouts_docker):
a script that closes its own output (`exec >/dev/null 2>&1`) and then blocks must not outlive
`exec_script`, and a process whose effective uid differs from its real uid is killed.
"""
# ruff: noqa: F811
import time

import pytest
from test_issue_2_lab_image_term import _exec, container, image  # noqa: F401
from test_issue_9_runner_docker import (  # noqa: F401  (fixtures are used by name)
    box,
    call,
    docker,
    loop,
    network,
    run,
    runtime,
)
from test_issue_117_kill_timeouts_docker import sleepers, wait_gone  # noqa: F401

pytestmark = pytest.mark.docker


# ---------------------------------------------------------------- 5. the image


@pytest.mark.parametrize("tool", ["ps", "setpriv", "awk"])
def test_the_lab_image_has_the_tools_the_kill_needs(container, tool):
    res = _exec(container, "root", "sh", "-c", f"command -v {tool}")
    assert res.returncode == 0 and res.stdout.strip().startswith("/"), f"{tool} is missing"


def test_ps_in_the_lab_image_prints_real_ids(container):
    res = _exec(container, "root", "ps", "-eo", "pid=,ppid=,ruid=,rgid=,args=")
    assert res.returncode == 0, res.stderr
    first = res.stdout.split()
    assert len(first) >= 5 and all(f.isdigit() for f in first[:4]), res.stdout[:200]


def test_setpriv_in_the_lab_image_can_become_the_student_and_clear_groups(container):
    res = _exec(
        container, "root", "setpriv", "--reuid=1001", "--regid=1001", "--clear-groups", "id", "-u"
    )
    assert res.returncode == 0 and res.stdout.strip() == "1001", (res.stdout, res.stderr)


def test_awk_in_the_lab_image_runs_a_program(container):
    res = _exec(container, "root", "awk", "BEGIN { print 6 * 7 }")
    assert res.returncode == 0 and res.stdout.strip() == "42", (res.stdout, res.stderr)


# ---------------------------------------------------------------- 1. early failure, real container


def test_a_script_that_closes_its_output_and_blocks_is_gone_when_exec_script_returns(
    runtime, call, box
):
    script = "#!/bin/sh\nexec >/dev/null 2>&1\nsleep 6101\n"
    started = time.monotonic()
    try:
        res = run(runtime, call, box, script, timeout=20)
        failed = res.timed_out or res.exit_code != 0
    except Exception:  # noqa: BLE001 - raising is the other accepted way to report a failure
        failed = True
    assert failed, "a script that never ended was reported as a success"
    assert time.monotonic() - started < 30
    assert sleepers(box, 6101) == 0, "exec_script returned (lock released) with the script alive"


# ---------------------------------------------------------------- 4. real owner, real container


def test_a_process_with_a_changed_effective_uid_is_killed_on_timeout(runtime, call, box):
    """ruid 0, euid 1001: `ps uid=` says 1001, but only a root-owned signal reaches it."""
    script = (
        "#!/bin/sh\n"
        "setpriv --euid=1001 --clear-groups sleep 6102 </dev/null >/dev/null 2>&1 &\n"
        "sleep 6103\n"
    )
    res = run(runtime, call, box, script, timeout=2)
    assert res.timed_out is True
    assert sleepers(box, 6102) == 0, "the euid-changed sleeper outlived exec_script"
    assert wait_gone(box, 6102) == 0 and wait_gone(box, 6103) == 0
