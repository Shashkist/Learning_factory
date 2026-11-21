"""Acceptance tests for issue #306: a stale timeout killer must not outlive its exec_script call.

Background (D56, D97): the kill of a timed-out script runs `setpriv --reuid=student kill ...`
for the student's processes. A student SIGSTOP loop also stops that child, the kill's bound
gives up, `exec_script` returns `timed_out`, but the killer exec itself lives on. When the
student's processes are continued it resumes, walks `ps` again, matches the NEXT exec's
interpreter (`<interp> -s`, uid 0, PPid 0) and kills it (the next setup.sh ended with 137).

Every case here needs a real container (real SIGSTOP, real `ps`, real signal delivery), so
they are all `docker` cases: they run in the CI `docker` job and skip without Docker. A fake
Engine cannot show the behaviour honestly (it has no processes to stop or to signal) and any
fake-based assertion would pin one fix (a `timeout` wrapper, a pid file, an exec id marker)
instead of the behaviour, so there is no non-docker case. On current main the cases are
expected RED (they reproduce the issue); the case of criterion 2 is red by the same cause.

Criteria (the numbers are the issue's):

1. A killer exec never outlives `exec_script`'s return; a late killer cannot signal anything
   that is not part of the timed-out exec.
   - test_no_root_killer_process_survives_exec_script_under_a_stop_loop
     Mutation: the bound gives up and returns while the killer (a root sh) stays alive.
   - test_a_late_killer_cannot_kill_the_exec_started_after_the_stop_loop_ends
     Mutation: the killer survives and resumes its rounds; the next long exec dies with 137.
2. The timed-out exec is identified unambiguously.
   - test_a_timeout_kill_does_not_touch_another_exec_with_the_same_interpreter
     Mutation: matching by `<interp> -s` + PPid 0 only (the current program): a concurrent
     exec of the same interpreter dies together with the timed-out one.
3. The stop-loop case from the issue.
   (a) test_no_stopped_student_reader_is_left_five_seconds_after_the_timed_out_check
       Mutation: the stopped `/bin/sh -s` reader of the check is left behind, as today. A
       D-entry that records this as accepted risk for #306 makes the case accept it.
   (b) test_setup_and_check_after_a_stop_loop_run_normally_in_the_same_container
       Mutation: the stale killer kills the next setup.sh (137) or spoils the next check.
"""
# ruff: noqa: F811
import asyncio
import re
import time
from pathlib import Path

import pytest
from test_issue_9_runner_docker import (  # noqa: F401  (fixtures are used by name)
    box,
    call,
    docker,
    loop,
    network,
    run,
    runtime,
)
from test_issue_12_linux_1 import (  # noqa: F401
    _task_dir,
    _text,
    docker_available,
    image,
    machine,
    runtime_flag,
)
from test_issue_126_linux_1_check_privacy_docker import (  # noqa: F401
    CHECK_TIMEOUT_S,
    _alive,
    _wait_loop_gone,
    fresh_box,
)

pytestmark = pytest.mark.docker

TASK = "02-directory-tree"
STOP_FILE = "/tmp/stop-stopper126"  # the STOPPER of the #126 exam ends when this file exists
REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
DECISIONS = REPO_ROOT / "docs" / "factory" / "DECISIONS.md"


# The #126 STOPPER, plus a handshake (issue #391). A check that is merely started "after the
# stopper" can still finish before the loop reaches its processes (a pass walks all of /proc
# and CI runners are slow), and then the case tested nothing. exec_script runs check.sh as
# ROOT, and the stopper runs as student, so it cannot signal that root shell (EPERM). A gate
# in the root part would open without anything having been stopped. So the gate goes into the
# STUDENT reader (`su student ... /bin/sh -s <<'READER'`, see _gated): it writes `READY`, then
# spins until the stopper writes `SEEN`. The stopper writes `SEEN` only at the end of a pass
# that STARTED after `READY` existed; such a pass saw the reader in /proc and sent it SIGSTOP
# (same uid, allowed). The reader therefore cannot get past its gate before it has been
# stopped, and a stopped reader cannot finish: the check times out deterministically,
# whatever the runner speed. Builtins only (`:>`, `[`), so no helper process can be missed.
# The loop keeps the `stop-stopper126` stop file name, which `_alive` / `_wait_loop_gone` of
# the #126 exam look for; the #126 STOPPER itself is untouched.
READY = "/tmp/ready-check306"
SEEN = "/tmp/seen-check306"
STOPPER = rf"""
while [ ! -e {STOP_FILE} ]; do
  flag=
  [ -e {READY} ] && flag=1
  for p in /proc/[0-9]*; do
    p=${{p#/proc/}}
    [ "$p" = "$$" ] || kill -STOP "$p" 2>/dev/null
  done
  [ -n "$flag" ] && : > {SEEN}
done
"""


def _gated(script: str) -> str:
    """The check script with the handshake as the first lines of its student reader."""
    lines = script.split("\n")
    idx = [i for i, ln in enumerate(lines) if ln.rstrip().endswith("<<'READER'")]
    assert len(idx) == 1, f"expected exactly one <<'READER' line in the check, found {len(idx)}"
    gate = [f": > {READY}", f"while [ ! -e {SEEN} ]; do :; done"]
    return "\n".join(lines[: idx[0] + 1] + gate + lines[idx[0] + 1 :])


def _assert_gate_held(box: str) -> None:
    """A timed-out gated check proves something only if the reader was stopped. The reader is
    either stopped before it wrote READY (valid), or it wrote READY and then waited for SEEN,
    which only a live stopper writes. READY without SEEN means the stopper died or stalled and
    the timeout came from the reader's own spin: the case tested nothing (review of #401)."""
    res = docker(
        "exec", "-u", "root", box, "sh", "-c", f"[ ! -e {READY} ] || [ -e {SEEN} ]", check=False
    )
    assert res.returncode == 0, (
        "the reader passed READY but the stopper never wrote SEEN (a dead or stalled stop "
        "loop), so the timeout stopped nothing and the case tested nothing"
    )


def _ps(box: str) -> list[tuple[int, int, str, str, str]]:
    """(pid, ppid, real user, state, args) of all processes, as root, minus this call's own."""
    script = 'echo "$$"; ps -eo pid=,ppid=,ruser=,stat=,args='
    out = docker("exec", "-u", "root", box, "sh", "-c", script, check=False).stdout.splitlines()
    me = int(out[0])
    rows = []
    for line in out[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid, ppid = int(parts[0]), int(parts[1])
        if pid == me or ppid == me:
            continue
        rows.append((pid, ppid, parts[2], parts[3], parts[4]))
    return rows


def _start_stopper(box: str):
    docker("exec", "-u", "root", box, "rm", "-f", STOP_FILE, READY, SEEN, check=False)
    docker("exec", "-d", "-u", "student", box, "sh", "-c", STOPPER)
    time.sleep(1.0)
    assert _alive(box, "stop-stopper126") >= 1, "the stopping loop did not start"


def _end_stopper_and_continue(box: str):
    """The loop ends and the student's processes are continued (not killed: whatever was
    stopped, a stale killer included, must be able to run on)."""
    docker("exec", "-u", "root", box, "touch", STOP_FILE, check=False)
    _wait_loop_gone(box, "stop-stopper126")
    docker("exec", "-u", "student", box, "sh", "-c", "kill -CONT -1", check=False)


def _cleanup(box: str):
    """End the loop FIRST (it would stop this very cleanup), then continue and kill."""
    _end_stopper_and_continue(box)
    docker(
        "exec", "-u", "student", box, "sh", "-c", "kill -CONT -1; kill -KILL -1",
        check=False, timeout=30,
    )
    docker("exec", "-u", "root", box, "rm", "-f", STOP_FILE, READY, SEEN, check=False, timeout=30)


def _timed_out_check_under_stopper(runtime, call, box, before: set | None = None) -> float:
    """Setup, start the SIGSTOP loop, run the check. Returns the monotonic time it returned.
    `before`, when given, is filled with the pids that exist when the check starts."""
    task = _task_dir(TASK)
    setup = call(runtime.exec_script, box, _text(task / "setup.sh"), 60)
    assert setup.exit_code == 0 and not setup.timed_out, setup.output
    _start_stopper(box)
    if before is not None:
        before.update(r[0] for r in _ps(box))
    res = call(runtime.exec_script, box, _gated(_text(task / "check.sh")), CHECK_TIMEOUT_S)
    assert res.timed_out is True, (
        f"the stop loop did not make the check time out (exit {res.exit_code}), so the case "
        f"tested nothing:\n{res.output}"
    )
    returned = time.monotonic()
    _assert_gate_held(box)
    return returned


# ---------------- 1. the killer never outlives the call


def test_no_root_killer_process_survives_exec_script_under_a_stop_loop(
    docker_available, runtime, call, fresh_box
):
    """After exec_script returns (plus 1.5 s of grace) no root process exists that was not
    there before the check ran: the killer is gone, the stopped student child cannot hold it."""
    task = _task_dir(TASK)
    setup = call(runtime.exec_script, fresh_box, _text(task / "setup.sh"), 60)
    assert setup.exit_code == 0, setup.output
    _start_stopper(fresh_box)
    before = {r[0] for r in _ps(fresh_box)}
    try:
        res = call(
            runtime.exec_script, fresh_box, _gated(_text(task / "check.sh")), CHECK_TIMEOUT_S
        )
        assert res.timed_out is True, f"the case tested nothing (exit {res.exit_code})"
        _assert_gate_held(fresh_box)
        time.sleep(1.5)
        left = [r for r in _ps(fresh_box) if r[2] == "root" and r[0] not in before]
    finally:
        _cleanup(fresh_box)
    assert not left, f"root processes left after exec_script returned (a stale killer?): {left}"


def test_a_late_killer_cannot_kill_the_exec_started_after_the_stop_loop_ends(
    docker_available, runtime, call, fresh_box
):
    """The loop ends, the student's processes are continued, and at once a long exec starts
    (same interpreter, uid 0, PPid 0: exactly what a resumed killer would match). It must run
    to its end with exit 0, not 137."""
    try:
        _timed_out_check_under_stopper(runtime, call, fresh_box)
        _end_stopper_and_continue(fresh_box)
        started = time.monotonic()
        res = call(runtime.exec_script, fresh_box, "#!/bin/sh\nsleep 9\necho later-ok\n", 40)
        took = time.monotonic() - started
    finally:
        _cleanup(fresh_box)
    assert res.timed_out is False, f"the next exec timed out: {res.output}"
    assert res.exit_code == 0, f"the next exec was killed by the stale killer: exit {res.exit_code}"
    assert "later-ok" in res.output and took >= 8.5, (res.output, took)


# ---------------- 2. unambiguous identification


def test_a_timeout_kill_does_not_touch_another_exec_with_the_same_interpreter(
    docker_available, runtime, call, fresh_box
):
    """Exec A (`sleep 6301`, 2 s timeout) and exec B (the same `/bin/sh -s`, root, PPid 0, 6 s
    of work) run side by side. The kill of A takes A's processes only; B ends on its own."""
    a_script = "#!/bin/sh\nsleep 6301\n"
    b_script = "#!/bin/sh\nsleep 6\necho b-ok\n"  # outlives A's kill (2 s plus the kill)

    async def both():
        a = asyncio.ensure_future(runtime.exec_script(fresh_box, a_script, 2))
        await asyncio.sleep(0.3)  # B is the later exec and is still running when A is killed
        b = asyncio.ensure_future(runtime.exec_script(fresh_box, b_script, 40))
        return await asyncio.gather(a, b)

    res_a, res_b = call(both)
    assert res_a.timed_out is True
    assert res_b.timed_out is False and res_b.exit_code == 0, (
        f"exec B was killed together with the timed-out exec A: exit {res_b.exit_code}"
    )
    assert "b-ok" in res_b.output
    left = [r for r in _ps(fresh_box) if r[4].startswith("sleep 6301")]
    assert not left, "the timed-out exec's own sleep was not killed"


# ---------------- 3. the stop loop of the issue


def test_no_stopped_student_reader_is_left_five_seconds_after_the_timed_out_check(
    docker_available, runtime, call, fresh_box
):
    try:
        before: set = set()
        returned = _timed_out_check_under_stopper(runtime, call, fresh_box, before)
        time.sleep(max(0.0, 5.0 - (time.monotonic() - returned)))
        # Any new stopped student process of the check: the reader may still read `su student
        # -s /bin/sh -c /bin/sh -s` (stopped right after the uid switch), not just `/bin/sh -s`.
        readers = [
            r
            for r in _ps(fresh_box)
            if r[2] == "student"
            and r[3].startswith("T")
            and r[0] not in before
            and "stop-stopper126" not in r[4]
        ]
    finally:
        _cleanup(fresh_box)
    if readers:  # the issue allows "accepted risk in a D-entry" instead of a fix
        text = DECISIONS.read_text(encoding="utf-8")
        entries = re.split(r"(?m)^#+ (?=.*\bD\d+\b)", text)
        accepted = any("#306" in e and "accepted risk" in e.lower() for e in entries)
        assert accepted, (
            f"{len(readers)} stopped student processes (readers) of the timed-out check are "
            f"left 5 s after exec_script returned, and no D-entry records it as accepted risk "
            f"for #306: {readers}"
        )


def test_setup_and_check_after_a_stop_loop_run_normally_in_the_same_container(
    docker_available, runtime, call, fresh_box
):
    task = _task_dir(TASK)
    try:
        _timed_out_check_under_stopper(runtime, call, fresh_box)
        _end_stopper_and_continue(fresh_box)
        time.sleep(0.2)  # start at once: a resumed stale killer is still walking its rounds
        setup = call(runtime.exec_script, fresh_box, _text(task / "setup.sh"), 60)
        started = time.monotonic()
        check = call(runtime.exec_script, fresh_box, _text(task / "check.sh"), CHECK_TIMEOUT_S)
        took = time.monotonic() - started
    finally:
        _cleanup(fresh_box)
    assert setup.timed_out is False and setup.exit_code == 0, (
        f"the next setup.sh did not run normally (exit {setup.exit_code}): {setup.output}"
    )
    assert check.timed_out is False, f"the next check timed out: {check.output}"
    assert check.exit_code == 1, f"untouched task 02 must fail: exit {check.exit_code}"
    assert "FAIL 1" in check.output, check.output
    assert took < 15, f"the next check took {took:.1f} s"
