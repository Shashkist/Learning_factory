"""The task routes' remaining budget and the db deadline are one clock (#257)."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from app import db, tasks


@pytest.mark.parametrize("with_started", [True, False])
def test_tasks_left_matches_db_deadline_for_the_same_request(monkeypatch, with_started):
    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 20.0)

    async def run():
        db.mark_request_start()
        started = time.monotonic() - 6.0
        db._request_started.set(started)
        ctx = SimpleNamespace(started=started) if with_started else SimpleNamespace()
        db_left = started + db.REQUEST_DEADLINE_S - time.monotonic()
        return tasks._left(ctx), db_left

    left, db_left = asyncio.run(run())
    assert left == pytest.approx(db_left, abs=0.1)
    assert left == pytest.approx(14.0, abs=0.5)


def test_real_start_and_ctx_share_the_middleware_start(monkeypatch):
    """`_start` adopts the middleware's start, so `_left` and the db deadline are one clock."""
    import uuid

    monkeypatch.setattr(db, "REQUEST_DEADLINE_S", 55.0)

    async def run():
        db.mark_request_start()
        db._request_started.set(time.monotonic() - 6.0)
        request = SimpleNamespace(
            state=SimpleNamespace(),
            app=SimpleNamespace(state=SimpleNamespace(pool=object())),
        )
        await tasks._start(request)
        await asyncio.sleep(0.05)  # a later start point would show here
        ctx = tasks._Ctx(request, uuid.uuid4(), "s", SimpleNamespace(tasks=[]), None)
        return tasks._left(ctx)

    assert asyncio.run(run()) == pytest.approx(55.0 - 6.0, abs=0.5)
