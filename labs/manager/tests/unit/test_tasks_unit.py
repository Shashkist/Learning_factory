"""Unit tests for the task engine helpers (#9); the HTTP behaviour is in the acceptance exam."""
from pathlib import Path

import pytest

from app import tasks
from app.content import Lab, Task
from app.runtime import exec_command


@pytest.mark.parametrize(
    "script,cmd",
    [
        ("#!/bin/sh\ntrue\n", ["/bin/sh", "-s"]),
        ("#!/usr/bin/env python3\n", ["/usr/bin/env", "python3", "-s"]),
        ("echo hi\n", ["/bin/sh", "-s"]),
        ("", ["/bin/sh", "-s"]),
        ("#!\nx\n", ["/bin/sh", "-s"]),
    ],
)
def test_exec_command(script, cmd):
    assert exec_command(script) == cmd


@pytest.mark.parametrize(
    "raw,want",
    [(None, 50.0), ("", 50.0), ("abc", 50.0), ("0", 50.0), ("-5", 50.0), ("7", 7.0)],
)
def test_setup_timeout(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv("LF_SETUP_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("LF_SETUP_TIMEOUT_S", raw)
    assert tasks.setup_timeout() == want


def test_state():
    row = {"shown_at": None, "passed_at": None}
    assert tasks._state(None) == "not_started"
    assert tasks._state(row) == "not_started"
    assert tasks._state({"shown_at": 1, "passed_at": None}) == "shown"
    assert tasks._state({"shown_at": 1, "passed_at": 2}) == "passed"


def test_detail_has_no_answer_and_title_comes_from_the_first_heading(tmp_path: Path):
    d = tmp_path / "tasks" / "t1"
    d.mkdir(parents=True)
    (d / "task.en.md").write_text("intro\n## Make files\nbody\n", encoding="utf-8")
    (d / "task.he.md").write_text("", encoding="utf-8")
    hands = Task(id="t1", kind="hands-on", checklist=[{"en": "a", "he": "b"}])
    quiz = Task(
        id="q",
        kind="quiz",
        question={"en": "Q", "he": "S"},
        options=[{"en": "x", "he": "y"}],
        correct=0,
    )
    lab = Lab(
        id="l", title={}, status="published", image="term", module=1, lesson=5,
        path=tmp_path, tasks=[quiz, hands],
    )
    got = tasks._detail(lab, hands, None)
    assert got["title"] == {"en": "Make files", "he": "t1"}
    assert got["index"] == 2 and got["total"] == 2 and got["state"] == "not_started"
    assert "correct" not in tasks._detail(lab, quiz, None)


def test_show_is_serialised_per_session_and_the_lock_entry_is_dropped(monkeypatch):
    import asyncio
    import uuid
    from types import SimpleNamespace

    from fastapi import HTTPException

    sid = uuid.uuid4()
    started, release, ran = asyncio.Event(), asyncio.Event(), []

    async def fake_show(ctx, task):
        ran.append(task)
        started.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(tasks, "_do_show", fake_show)
    ctx = SimpleNamespace(sid=sid)

    async def go():
        first = asyncio.create_task(tasks._show(ctx, "a"))
        await started.wait()
        with pytest.raises(HTTPException) as err:
            await tasks._show(ctx, "b")
        assert err.value.status_code == 409 and err.value.detail == "setup_running"
        release.set()
        assert await first == {"ok": True}

    asyncio.run(go())
    assert ran == ["a"] and sid not in tasks._LOCKS


def test_exec_output_is_capped_and_the_stream_still_drains():
    import asyncio
    import struct

    from app import runtime

    big = b"x" * (runtime.MAX_OUTPUT + 5000)
    frames = struct.pack(">BxxxI", 1, len(big)) + big + struct.pack(">BxxxI", 1, 3) + b"end"

    async def conn(r, w):
        await r.readuntil(b"\r\n\r\n")
        w.write(b"HTTP/1.1 200 OK\r\n\r\n" + frames)
        await w.drain()
        w.close()

    async def go():
        srv = await asyncio.start_server(conn, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        rt = runtime.DockerRuntime()
        rt._tcp = f"127.0.0.1:{port}"
        out = bytearray()
        await rt._stream_exec("e", "true", out)
        srv.close()
        return bytes(out)

    out = asyncio.run(go())
    assert out.startswith(b"x" * runtime.MAX_OUTPUT)
    assert len(out) == runtime.MAX_OUTPUT + len(runtime.TRUNCATED)
    assert out.endswith(runtime.TRUNCATED)
