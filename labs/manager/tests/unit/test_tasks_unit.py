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
        w.write(b"HTTP/1.1 200 OK\r\n\r\n")
        await w.drain()
        await r.read()  # the script, until the client closes its write side
        w.write(frames)
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


def test_navigation_is_refused_when_the_current_task_changed_before_the_lock(monkeypatch):
    import asyncio
    import uuid
    from types import SimpleNamespace

    from fastapi import HTTPException

    ran = []

    async def fake_show(ctx, task):
        ran.append(task)
        return {"ok": True}

    class Pool:
        async def fetchval(self, sql, sid):
            return "03-net"  # another request already moved the session on

    monkeypatch.setattr(tasks, "_do_show", fake_show)
    ctx = SimpleNamespace(sid=uuid.uuid4(), pool=Pool())
    with pytest.raises(HTTPException) as err:
        asyncio.run(tasks._show(ctx, "04-last", moving_from="02-files"))
    assert err.value.status_code == 409 and err.value.detail == "task_changed"
    assert ran == [] and ctx.sid not in tasks._LOCKS
    assert asyncio.run(tasks._show(ctx, "04-last", moving_from="03-net")) == {"ok": True}


def test_no_free_exec_slot_is_a_504_setup_timeout_and_runs_nothing(monkeypatch, tmp_path: Path):
    import asyncio
    import uuid
    from types import SimpleNamespace

    from fastapi import HTTPException

    (tmp_path / "tasks" / "t1").mkdir(parents=True)
    (tmp_path / "tasks" / "t1" / "setup.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
    calls = []

    class Runtime:
        async def exec_script(self, name, script, timeout):
            calls.append(name)

    async def go():
        monkeypatch.setattr(tasks, "_EXECS", asyncio.Semaphore(1))
        monkeypatch.setattr(tasks, "SLOT_WAIT_S", 0.05)
        await tasks._EXECS.acquire()  # every slot busy
        ctx = SimpleNamespace(
            sid=uuid.uuid4(),
            lab=SimpleNamespace(path=tmp_path),
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=Runtime()))),
        )
        with pytest.raises(HTTPException) as err:
            await tasks._do_show(ctx, SimpleNamespace(id="t1", kind="hands-on"))
        return err.value

    exc = asyncio.run(go())
    assert exc.status_code == 504 and exc.detail["code"] == "setup_timeout"
    assert calls == []


@pytest.mark.parametrize(
    "output,found,clean",
    [
        ("PASS 1\nFAIL 2 no good\n", {1: (True, None), 2: (False, "no good")}, True),
        ("\r\nPASS 1\r\n\n", {1: (True, None)}, True),
        ("FAIL 1\n", {1: (False, None)}, True),
        ("PASS 1\nFAIL 1 x\n", {1: (False, "x")}, True),
        ("FAIL 1 x\nPASS 1\n", {1: (False, "x")}, True),
        ("PASS 1\nPASS 1\n", {1: (True, None)}, True),
        ("PASS 3\n", {}, False),
        ("PASS 0\n", {}, False),
        ("PASS x\n", {}, False),
        ("OK 1\nPASS 1\n", {1: (True, None)}, False),
        ("PASS 1 trailing\n", {}, False),
        ("FAIL 2 " + "h" * 400 + "\n", {2: (False, "h" * 300)}, True),
    ],
)
def test_parse_check(output, found, clean):
    assert tasks.parse_check(output, 2) == (found, clean)
