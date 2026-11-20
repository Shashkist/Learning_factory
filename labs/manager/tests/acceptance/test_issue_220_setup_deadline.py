"""Acceptance tests for issue #220 item 1: the request deadline covers every task kind.

No Docker, no database. `_do_show(ctx, task)` is called with a context whose request budget
(`app.db.REQUEST_DEADLINE_S`, measured from `ctx.started`) is already used up. For a quiz
task the answer is the same JSON error the hands-on path gives when no time is left
(HTTP 504, detail code `setup_timeout`) and the database is not touched.
"""
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _Pool:
    def __init__(self):
        self.calls = []

    def _hit(self, name):
        self.calls.append(name)
        raise AssertionError(f"database touched: {name}")

    async def acquire(self, *a, **k):
        self._hit("acquire")

    async def fetchrow(self, *a, **k):
        self._hit("fetchrow")

    fetch = fetchval = execute = fetchrow

    async def release(self, *a, **k):
        self._hit("release")


def _ctx(task, pool):
    from app import db

    lab = SimpleNamespace(id="lab", path=Path("."), tasks=[task])
    return SimpleNamespace(
        pool=pool,
        lab=lab,
        sid="00000000-0000-0000-0000-000000000000",
        student="s",
        current=None,
        started=time.monotonic() - db.REQUEST_DEADLINE_S - 1,
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
    )


@pytest.mark.parametrize("kind", ["quiz", "checklist"])
def test_show_of_a_non_hands_on_task_with_the_budget_used_up_answers_the_json_error(kind):
    from app import tasks
    from app.content import Task

    task = Task(id="t1", kind=kind)
    pool = _Pool()
    with pytest.raises(HTTPException) as err:
        asyncio.run(tasks._do_show(_ctx(task, pool), task))
    assert err.value.status_code == 504
    assert isinstance(err.value.detail, dict)
    assert err.value.detail.get("code") == "setup_timeout"
    assert pool.calls == []
