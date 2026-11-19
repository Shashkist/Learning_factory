"""The database step after a task setup is bounded by DB_STEP_BOUND_S (#202)."""
import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import tasks


class _Conn:
    async def execute(self, *a):
        await asyncio.sleep(30)

    fetchrow = execute

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def acquire(self):
        return _Conn()


def test_slow_database_step_is_cut_off_and_answers_503(monkeypatch):
    monkeypatch.setattr(tasks, "DB_STEP_BOUND_S", 0.2)
    ctx = SimpleNamespace(pool=_Pool(), student="s", sid="x", lab=SimpleNamespace(id="l"))
    task = SimpleNamespace(id="t", kind="quiz")
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks._do_show(ctx, task))
    assert exc.value.status_code == 503
    assert time.monotonic() - t0 < 2
