import asyncio
import logging

import pytest

from app import janitor
from app.sessions import end_session


@pytest.mark.parametrize("bad", ["nan", "inf", "0", "-3", "x"])
def test_invalid_start_deadline_falls_back_and_warns_once(monkeypatch, caplog, bad):
    monkeypatch.setenv("LF_START_DEADLINE_S", bad)
    janitor._warned_deadlines.clear()
    with caplog.at_level(logging.WARNING):
        assert janitor._start_deadline() == 180.0
        assert janitor._start_deadline() == 180.0
    assert len([r for r in caplog.records if "LF_START_DEADLINE_S" in r.getMessage()]) == 1


def test_valid_start_deadline_is_used(monkeypatch, caplog):
    monkeypatch.setenv("LF_START_DEADLINE_S", "90")
    with caplog.at_level(logging.WARNING):
        assert janitor._start_deadline() == 90.0
    assert caplog.records == []


class _Pool:
    def __init__(self, result):
        self.calls = []
        self.result = result

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self.result


class _RT:
    def __init__(self):
        self.removed = []

    async def remove(self, name):
        self.removed.append(name)


def test_end_session_guard_is_in_the_update_and_skips_removal_when_not_matched():
    pool, rt = _Pool(None), _RT()
    assert asyncio.run(end_session(pool, rt, "sid", "orphan", stale_start_s=30)) is False
    sql, args = pool.calls[0]
    assert "status = 'starting'" in sql and "make_interval(secs => $3)" in sql
    assert args == ("sid", "orphan", 30)
    assert rt.removed == []


def test_end_session_without_guard_is_unchanged():
    pool = _Pool(None)
    asyncio.run(end_session(pool, _RT(), "sid", "orphan"))
    sql, args = pool.calls[0]
    assert "$3" not in sql and args == ("sid", "orphan")


def test_step_repeat_and_recovery_name_the_outage(monkeypatch, caplog):
    t = [0.0]

    class Clock:
        monotonic = staticmethod(lambda: t[0])

    monkeypatch.setattr(janitor, "time", Clock)

    async def bad():
        raise OSError("x")

    async def good():
        return None

    async def run():
        for _ in range(17):
            await janitor._step("u", bad())
            t[0] += 60
        await janitor._step("u", good())

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    msgs = [(r.levelno, r.getMessage()) for r in caplog.records if r.levelno >= logging.INFO]
    assert [m for lvl, m in msgs if lvl == logging.ERROR][1].endswith("for 15 min")
    assert msgs[-1] == (logging.INFO, "janitor step u recovered after 17 min")
