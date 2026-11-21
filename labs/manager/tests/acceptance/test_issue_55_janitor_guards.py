"""Acceptance tests for issue #55: janitor guards and recovery logging (review of #49).

Spec: docs/superpowers/specs/2026-11-14-lab-platform-design.md section 5 and 6; D24.

Harness: the database case reuses the fixtures of the #7 exam (needs LF_TEST_DATABASE_URL, skips
locally, CI fails on any skip). Everything else runs without a database or Docker.

Choices beyond the issue text (the build must follow them or change the exam via the manager):

* Guarded end: the exam flips the row `starting` -> `running` (with its container) right after
  the janitor's second snapshot of the live sessions, i.e. after the first `pool.fetch` that
  follows the container listing. The session must stay `running` and the container must stay.
* `LF_START_DEADLINE_S` validation is checked in a fresh interpreter per value (so a
  "warn once per process" memory cannot hide the line): `janitor._sweep_containers` runs 4 times
  on a recording pool; every numeric query argument must be finite and positive and at least one
  must exist; one or two WARNING lines name `LF_START_DEADLINE_S` over the 4 passes. A valid
  `90` is passed on as 90 and logs no such line.
* Recovery logging goes through `janitor._step(name, coro)` on a fake clock. The clock is
  injected by replacing the name `time` in the janitor module with a proxy whose `monotonic()`,
  `time()` and `perf_counter()` return the fake clock (so `time.monotonic()` and `time.time()`
  both work; `from time import monotonic` would not). Passes are 60 s apart.
  - the first failure logs one ERROR; no ERROR before 15 min; the next ERROR comes on the first
    pass at or after 15 min (at most two passes late), carries the outage duration (a number and
    a word like min/outage/since/for), the next one 15 min after that;
  - a later success logs an INFO line with `recovered` and the step name (and none if the step
    never failed); a new failure after that logs an ERROR at once again;
  - the first ERROR of an unexpected exception has `exc_info`, the repeats do not.
* Loop cadence: `janitor.loop` with a pool that fails for 4 passes then works sleeps the
  interval (`LF_JANITOR_INTERVAL_S`) after every pass from the first healthy one on, and logs
  `recovered`.
"""
# ruff: noqa: F811, F401  (fixtures imported from the #7 and #49 exams are used by name)
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_issue_7_session_lifecycle import (  # noqa: F401
    SCHEMA,
    FakeRuntime,
    _db,
    _insert_session,
    env,
)
from test_issue_49_lifecycle_hardening import _app, quiet_host  # noqa: F401

MANAGER_DIR = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------- guarded start-deadline end


class FlippingPool:
    """Wraps the real pool; right after the snapshot that follows the listing, flips the row."""

    def __init__(self, real, sid: str, flip: bool):
        self._real, self._sid, self._flip = real, sid, flip
        self.listed = False
        self.flipped = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def fetch(self, *a, **k):
        rows = await self._real.fetch(*a, **k)
        if self.listed and self._flip and not self.flipped:
            self.flipped = True
            await self._real.execute(
                f"update {SCHEMA}.lab_sessions set status = 'running', "
                f"container = 'lab-{self._sid}' where id = $1",
                self._sid,
            )
        return rows


class ListingRuntime(FakeRuntime):
    def __init__(self, pool_holder):
        super().__init__()
        self.holder = pool_holder

    def list_lab_containers(self):
        self.holder[0].listed = True
        return super().list_lab_containers()


def _stale_starting(env, monkeypatch, flip: bool):
    monkeypatch.setenv("LF_START_DEADLINE_S", "30")
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    env.update(sid, "started_at = now() - interval '120 seconds'")
    holder: list = [None]
    rt = ListingRuntime(holder)
    rt.add_foreign(f"lab-{sid}", sid, running=False)
    pool = FlippingPool(env.app.state.pool, sid, flip)
    holder[0] = pool
    env.app.state.runtime = rt
    env.app.state.pool = pool
    env.janitor()
    return sid, rt, pool


def test_a_row_that_flips_to_running_after_the_second_snapshot_is_not_ended_and_keeps_its_container(
    env, monkeypatch
):
    sid, rt, pool = _stale_starting(env, monkeypatch, flip=True)
    assert pool.flipped, "the janitor never took a snapshot after listing the containers"
    row = env.row(sid)
    assert row["status"] == "running", f"a running session was ended: {row['end_reason']!r}"
    assert row["end_reason"] is None
    assert f"lab-{sid}" in rt.containers
    assert f"lab-{sid}" not in rt.removed


def test_a_row_that_is_still_starting_and_past_the_deadline_is_still_ended_as_orphan(
    env, monkeypatch
):
    sid, rt, pool = _stale_starting(env, monkeypatch, flip=False)
    row = env.row(sid)
    assert row["status"] in ("ended", "expired")
    assert row["end_reason"] == "orphan"
    assert f"lab-{sid}" in rt.removed


# ---------------------------------------------------------------- LF_START_DEADLINE_S validation

_DRIVER = r"""
import asyncio, json, logging, math, sys
sys.path.insert(0, ".")
records = []
class H(logging.Handler):
    def emit(self, r):
        records.append([r.levelno, r.getMessage()])
logging.getLogger().addHandler(H())
logging.getLogger().setLevel(logging.DEBUG)
from app import janitor

class Pool:
    def __init__(self): self.args = []
    async def fetch(self, sql, *a, **k):
        self.args.append([x for x in a if isinstance(x, (int, float)) and not isinstance(x, bool)])
        return []
    async def fetchval(self, *a, **k): return None
    async def execute(self, *a, **k): return None
class RT:
    async def list_lab_containers(self): return []
    async def remove(self, name): return None

pool = Pool()
async def go():
    for _ in range(4):
        await janitor._sweep_containers(pool, RT())
asyncio.run(go())
def num(x):
    if isinstance(x, float) and math.isnan(x): return "nan"
    if isinstance(x, float) and math.isinf(x): return "inf"
    return x
print("RESULT" + json.dumps({"args": [[num(x) for x in a] for a in pool.args], "records": records}))
"""


def _run_driver(value: str) -> dict:
    envv = {k: v for k, v in os.environ.items() if k != "LF_START_DEADLINE_S"}
    envv["LF_START_DEADLINE_S"] = value
    out = subprocess.run(
        [sys.executable, "-c", _DRIVER],
        cwd=MANAGER_DIR,
        env=envv,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    line = next(x for x in out.stdout.splitlines() if x.startswith("RESULT"))
    return json.loads(line[len("RESULT"):])


def _warnings(result: dict) -> list[str]:
    return [
        m
        for lvl, m in result["records"]
        if lvl >= logging.WARNING and "LF_START_DEADLINE_S" in m
    ]


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "0", "-5", "abc"])
def test_an_invalid_start_deadline_falls_back_to_a_positive_value_with_one_warning(bad):
    result = _run_driver(bad)
    nums = [x for args in result["args"] for x in args]
    assert nums, "the sweep passed no deadline to the database"
    for x in nums:
        shown = f"LF_START_DEADLINE_S={bad!r} reached the query as {x!r}"
        assert isinstance(x, (int, float)), shown
        assert x > 0, shown
    warns = _warnings(result)
    assert 1 <= len(warns) <= 2, f"{len(warns)} warning lines over 4 passes: {warns}"


def test_a_valid_start_deadline_is_used_without_a_warning():
    result = _run_driver("90")
    nums = [x for args in result["args"] for x in args]
    assert 90 in nums, f"90 was not passed to the query: {nums}"
    assert _warnings(result) == []


# ---------------------------------------------------------------- recovery logging (_step)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    time = perf_counter = monotonic


@pytest.fixture
def clock(monkeypatch):
    from app import janitor

    c = FakeClock()
    real_time = janitor.time

    class Proxy:
        monotonic = staticmethod(c.monotonic)
        time = staticmethod(c.time)
        perf_counter = staticmethod(c.perf_counter)

        def __getattr__(self, name):
            return getattr(real_time, name)

    monkeypatch.setattr(janitor, "time", Proxy())
    return c


class Step:
    def __init__(self):
        self.name = "s" + uuid.uuid4().hex[:8]  # unique: no state shared with other cases
        self.fail_with: Exception | None = None

    async def _body(self):
        if self.fail_with is not None:
            raise self.fail_with

    def run(self):
        from app import janitor

        return asyncio.run(janitor._step(self.name, self._body()))


def _mine(caplog, step: Step):
    return [r for r in caplog.records if step.name in r.getMessage()]


def test_a_failing_step_that_succeeds_again_logs_an_info_line_with_recovered(caplog, clock):
    step = Step()
    step.fail_with = ConnectionError("db down")
    with caplog.at_level(logging.DEBUG):
        assert step.run() is False
        clock.t += 120
        step.fail_with = None
        assert step.run() is True
    rec = [r for r in _mine(caplog, step) if "recovered" in r.getMessage()]
    assert len(rec) == 1, [r.getMessage() for r in _mine(caplog, step)]
    assert rec[0].levelno == logging.INFO


def test_a_step_that_never_failed_logs_no_recovered_line(caplog, clock):
    step = Step()
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            assert step.run() is True
            clock.t += 60
    assert [r for r in caplog.records if "recovered" in r.getMessage()] == []


def test_a_failure_after_a_recovery_is_logged_as_an_error_at_once(caplog, clock):
    step = Step()
    with caplog.at_level(logging.DEBUG):
        step.fail_with = ConnectionError("db down")
        step.run()
        clock.t += 60
        step.fail_with = None
        step.run()
        clock.t += 60
        step.fail_with = ConnectionError("db down")
        step.run()
    errors = [r for r in _mine(caplog, step) if r.levelno >= logging.ERROR]
    assert len(errors) == 2


def _outage(caplog, clock, step: Step, minutes: int):
    """Fail on every pass, one pass a minute; returns [(minutes since start, record)]."""
    step.fail_with = ValueError("boom")
    seen = []
    with caplog.at_level(logging.DEBUG):
        for i in range(minutes + 1):
            before = len(caplog.records)
            step.run()
            for r in caplog.records[before:]:
                if r.levelno >= logging.ERROR and step.name in r.getMessage():
                    seen.append((i, r))
            clock.t += 60
    return seen


def test_the_error_is_repeated_about_every_15_minutes_and_not_before(caplog, clock):
    seen = _outage(caplog, clock, Step(), 47)
    minutes = [m for m, _ in seen]
    assert minutes[0] == 0
    assert len(minutes) == 4, f"expected errors at about 0, 15, 30, 45 minutes, got {minutes}"
    for got, want in zip(minutes[1:], (15, 30, 45), strict=True):
        assert want <= got <= want + 2, f"repeat at minute {got}, expected about {want}: {minutes}"


def test_the_repeated_error_carries_the_outage_duration(caplog, clock):
    seen = _outage(caplog, clock, Step(), 31)
    assert len(seen) >= 2
    msg = seen[1][1].getMessage()
    assert re.search(r"\d", msg), msg
    assert re.search(r"min|outage|since|for\b|\bs\b|seconds", msg, re.I), msg
    assert msg != seen[0][1].getMessage(), "the repeat must tell something new (the duration)"


def test_only_the_first_error_of_an_unexpected_exception_has_a_traceback(caplog, clock):
    seen = _outage(caplog, clock, Step(), 31)
    assert len(seen) >= 2
    assert seen[0][1].exc_info, "the first error must carry the traceback"
    assert all(not r.exc_info for _, r in seen[1:]), "repeats must not repeat the traceback"


def test_no_error_is_logged_before_15_minutes_even_after_many_passes(caplog, clock):
    seen = _outage(caplog, clock, Step(), 14)
    assert [m for m, _ in seen] == [0]


# ---------------------------------------------------------------- loop cadence after recovery


class RecoveringPool:
    def __init__(self):
        self.broken = True

    async def fetch(self, *a, **k):
        if self.broken:
            raise ConnectionError("database is down")
        return []

    async def fetchval(self, *a, **k):
        if self.broken:
            raise ConnectionError("database is down")
        return None


def test_after_the_pool_recovers_the_loop_sleeps_the_normal_interval_again(
    monkeypatch, quiet_host, caplog
):
    from app import janitor

    broken_passes, total = 4, 9
    pool = RecoveringPool()
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *a, **k):
        delays.append(float(delay))
        if len(delays) >= broken_passes:
            pool.broken = False
        if len(delays) >= total:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def go():
        try:
            await janitor.loop(_app(pool, FakeRuntime()))
        except asyncio.CancelledError:
            pass

    with caplog.at_level(logging.DEBUG):
        asyncio.run(go())
    assert len(delays) == total, delays
    assert all(d <= 60 for d in delays[:broken_passes]), delays
    assert delays[broken_passes:] == [60.0] * (total - broken_passes), (
        f"cadence did not return to LF_JANITOR_INTERVAL_S after recovery: {delays}"
    )
    assert any("recovered" in r.getMessage() for r in caplog.records), "no recovery line logged"
