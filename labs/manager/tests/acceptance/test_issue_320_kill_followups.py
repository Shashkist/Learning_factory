"""Acceptance tests for issue #320 (split from #308, D102): follow-ups of the bounded kill.

Criterion 2 (cancellation) with the fake engine of test_issue_9_runner.py: an `exec_script` that
is cancelled (`asyncio.CancelledError`: shutdown, an outer timeout such as `wait_ready`) still
runs the bounded kill (under `asyncio.shield`, so the cancel does not cut it), and then the
cancellation goes on (CancelledError, or the TimeoutError an outer `wait_for` makes of it).
Criterion 3 (log wording) is pinned in test_issue_151_kill_followups.py, criterion 4
(`is_kill_exec` by shape) in test_issue_9_runner.py, criterion 1 in
test_issue_151_kill_followups_docker.py.
"""
# ruff: noqa: F811
import asyncio
import time

import pytest
import test_issue_9_runner as t9
from test_issue_9_runner import (  # noqa: F401  (engine is a fixture, used by name)
    engine,
    runtime,
)

from app import runtime as rt_mod

SLEEPER = "#!/bin/sh\nexec >/dev/null 2>&1\nsleep 600\n"


def _hang(engine):
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [], "delay": 600.0}


async def _wait_for_exec(engine, seconds=5.0):
    end = time.monotonic() + seconds
    while not engine.creates and time.monotonic() < end:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)  # the script is running


async def _settle(engine, seconds=6.0):
    end = time.monotonic() + seconds
    while "exec1" not in engine.finished and time.monotonic() < end:
        await asyncio.sleep(0.05)


def test_a_cancelled_exec_script_runs_the_kill_and_reraises_cancellederror(engine):
    _hang(engine)

    async def main():
        task = asyncio.ensure_future(runtime().exec_script("lab-exam-1", SLEEPER, 30.0))
        await _wait_for_exec(engine)
        task.cancel()
        outcome = None
        try:
            await task
        except asyncio.CancelledError:
            outcome = "cancelled"
        except BaseException as exc:  # noqa: BLE001
            outcome = type(exc).__name__
        # the kill ran in full, the cancel did not cut it
        await _settle(engine)
        return outcome

    outcome = asyncio.run(main())
    assert len(engine.kill_runs) == 1, f"kill execs after a cancel: {len(engine.kill_runs)}"
    assert t9.is_kill_exec(engine.kill_runs[0][0]["Cmd"])
    assert "exec1" in engine.finished, "the cancelled script is still running"
    assert outcome == "cancelled", f"the cancellation must be re-raised, got {outcome}"


def test_an_outer_wait_for_timeout_still_kills_the_script(engine):
    _hang(engine)

    async def main():
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(runtime().exec_script("lab-exam-1", SLEEPER, 30.0), 1.0)
        took = time.monotonic() - started
        await _settle(engine)
        return took

    took = asyncio.run(main())
    assert len(engine.kill_runs) == 1, f"kill execs after an outer timeout: {len(engine.kill_runs)}"
    assert "exec1" in engine.finished
    assert took < 1.0 + rt_mod.KILL_BOUND_S + 2.0, f"{took:.1f}s"


def test_a_cancel_with_a_kill_that_hangs_is_still_bounded_and_reraises(engine):
    _hang(engine)
    engine.kill_delay = 600.0

    async def main():
        task = asyncio.ensure_future(runtime().exec_script("lab-exam-1", SLEEPER, 30.0))
        await _wait_for_exec(engine)
        started = time.monotonic()
        task.cancel()
        try:
            await asyncio.wait_for(task, rt_mod.KILL_BOUND_S + 3.0)
        except asyncio.CancelledError:
            return "cancelled", time.monotonic() - started
        except BaseException as exc:  # noqa: BLE001
            return type(exc).__name__, time.monotonic() - started
        return "returned", time.monotonic() - started

    outcome, took = asyncio.run(main())
    assert len(engine.kill_runs) == 1, f"kill execs: {len(engine.kill_runs)}"
    assert outcome == "cancelled", outcome
    assert took < rt_mod.KILL_BOUND_S + 2.0, f"an unbounded kill held the cancel: {took:.1f}s"


def test_an_exec_script_that_is_not_cancelled_and_ends_normally_runs_no_kill(engine):
    engine.behaviour = lambda b, s: {"exit": 0, "frames": []}
    res = asyncio.run(runtime().exec_script("lab-exam-1", "#!/bin/sh\ntrue\n", 5.0))
    assert res.exit_code == 0 and engine.kill_runs == []
