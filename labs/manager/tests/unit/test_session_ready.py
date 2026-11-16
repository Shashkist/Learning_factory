import asyncio

import pytest

from app import sessions
from app.runtime import DockerRuntime, ExecResult


def _runtime(results):
    rt = DockerRuntime.__new__(DockerRuntime)
    calls = []

    async def exec_script(name, script, timeout):
        calls.append((name, script))
        r = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    rt.exec_script = exec_script  # type: ignore[method-assign]
    return rt, calls


def test_ready_once_ttyd_runs():
    rt, calls = _runtime([ExecResult(1, ""), RuntimeError("down"), ExecResult(0, "")])
    asyncio.run(rt.wait_ready("lab-x", 5))
    assert len(calls) == 3
    assert "ttyd" in calls[0][1]


def test_never_ready_times_out():
    rt, _ = _runtime([ExecResult(1, "")])
    with pytest.raises(TimeoutError):
        asyncio.run(rt.wait_ready("lab-x", 0.5))


def test_runtime_without_wait_ready_is_ready():
    asyncio.run(sessions.wait_ready(object(), "lab-x"))


def test_wait_is_bounded_by_half_the_start_deadline(monkeypatch):
    monkeypatch.setenv("LF_START_DEADLINE_S", "40")
    seen = []

    class R:
        async def wait_ready(self, name, timeout):
            seen.append(timeout)

    asyncio.run(sessions.wait_ready(R(), "lab-x"))
    assert seen == [20.0]
