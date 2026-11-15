"""Acceptance tests for issue #49: session lifecycle hardening (janitor races, deadline, DB-down).

Spec: docs/superpowers/specs/2026-11-14-lab-platform-design.md section 5 and 6; D24, D26.
The Docker half (the `student` write surface) is in test_issue_49_lifecycle_docker.py.

Harness: the database tests reuse the fixtures and fakes of the #7 exam (`env`, `_db`,
`FakeRuntime`, `_insert_session`), imported from the sibling module; they need
LF_TEST_DATABASE_URL and skip locally, CI fails on any skip. The tests without a database run
everywhere. Student ids are fake.

Choices beyond the issue text (the build must follow them or change the exam via the manager):

* Race: the exam flips a row from `starting` to `running` from inside
  `runtime.list_lab_containers()`, i.e. between the janitor's two queries of one pass. A
  container the runtime reports as not running (what `created` looked like before the fix) must
  survive when the row was `starting` in either snapshot.
* `DockerRuntime.list_lab_containers` is checked with a fake Engine API: the exam replaces
  `DockerRuntime._client` with an `httpx.AsyncClient` on a `MockTransport`. States `created`,
  `running`, `restarting`, `paused` report `running=True`; only `exited` and `dead` report False.
* Start deadline: `LF_START_DEADLINE_S` (default 180), read per pass, measured from
  `started_at`; ended with `end_reason = 'orphan'`, container removed by name `lab-<id>`.
* DB down: `app.janitor.loop(app)` must call `asyncio.sleep` (the exam replaces it with a
  virtual clock). With `app.state.pool = None` the expiry-label sweep runs at most 6 times in 120
  virtual seconds at `LF_JANITOR_INTERVAL_S=60` (a fixed 2 s retry runs about 60 times), the
  first sweep still runs at once. The same message is logged at ERROR at most twice in ten
  intervals (log text is compared as formatted, so do not put a changing counter in it).
"""
# ruff: noqa: F811, F401  (fixtures imported from the #7 exams are used by name)
import asyncio
import datetime as dt
import logging
import uuid
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest

# fixtures and helpers of the #7 exam
from test_issue_7_session_lifecycle import (  # noqa: F401
    SCHEMA,
    FakeRuntime,
    _BrokenPool,
    _db,
    _insert_session,
    env,
)

# ---------------------------------------------------------------- created counts as running


def _engine_list(states: dict[str, str]):
    """A fake Docker Engine: GET /containers/json lists one lab container per state."""
    items = []
    for state, session in states.items():
        items.append(
            {
                "Id": uuid.uuid4().hex,
                "Names": [f"/lab-{session}"],
                "State": state,
                "Labels": {"lf": "1", "lf.session": session},
            }
        )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/containers/json")
        return httpx.Response(200, json=items)

    return handler


def _list_with(monkeypatch, handler):
    from app.runtime import DockerRuntime

    monkeypatch.setattr(
        DockerRuntime,
        "_client",
        lambda self: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://docker"
        ),
    )
    return asyncio.run(DockerRuntime().list_lab_containers())


@pytest.mark.parametrize("state", ["created", "running", "restarting", "paused"])
def test_docker_state_that_is_not_exited_or_dead_counts_as_running(monkeypatch, state):
    session = str(uuid.uuid4())
    got = _list_with(monkeypatch, _engine_list({state: session}))
    assert len(got) == 1
    name, running, sid = got[0][0], got[0][1], got[0][2]
    assert name == f"lab-{session}" and sid == session
    assert running is True, f"Docker state {state!r} must count like running"


@pytest.mark.parametrize("state", ["exited", "dead"])
def test_only_exited_and_dead_count_as_stopped(monkeypatch, state):
    session = str(uuid.uuid4())
    got = _list_with(monkeypatch, _engine_list({state: session}))
    assert len(got) == 1
    assert got[0][1] is False


# ---------------------------------------------------------------- starting is protected


class FlipRuntime(FakeRuntime):
    """Flips a row from `starting` to `running` once, inside the container listing."""

    def __init__(self, env_, session_id: str):
        super().__init__()
        self._env, self._sid, self.flipped = env_, session_id, False

    def list_lab_containers(self):
        if not self.flipped:
            self.flipped = True
            import threading

            t = threading.Thread(
                target=lambda: self._env.update(
                    self._sid, f"status = 'running', container = 'lab-{self._sid}'"
                )
            )
            t.start()
            t.join(30)
        return super().list_lab_containers()


def test_janitor_keeps_a_created_container_whose_row_flips_from_starting_to_running(env):
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    flip = FlipRuntime(env, sid)
    # `created` as the pre-fix runtime reported it: present but not running
    flip.add_foreign(f"lab-{sid}", sid, running=False)
    env.app.state.runtime = flip
    env.janitor()
    assert flip.flipped, "the janitor never listed the containers"
    assert env.row(sid)["status"] == "running", "the flip did not happen between the queries"
    assert f"lab-{sid}" in flip.containers, "the container of a starting session was removed"
    assert f"lab-{sid}" not in flip.removed


def test_janitor_keeps_a_listed_running_container_whose_row_flips_from_starting_to_running(env):
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    flip = FlipRuntime(env, sid)
    flip.add_foreign(f"lab-{sid}", sid, running=True)
    env.app.state.runtime = flip
    env.janitor()
    assert f"lab-{sid}" in flip.containers
    assert env.row(sid)["status"] == "running"


def test_a_starting_row_whose_container_is_not_there_yet_is_not_ended_as_orphan(env):
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    env.janitor()  # young starting row, no container yet: the start path is still working
    assert env.row(sid)["status"] == "starting"


# ---------------------------------------------------------------- start deadline


def _age(env, sid: str, seconds: int) -> None:
    env.update(sid, f"started_at = now() - interval '{seconds} seconds'")


def test_a_starting_row_past_the_default_deadline_is_ended_as_orphan_and_container_removed(
    env, monkeypatch
):
    monkeypatch.delenv("LF_START_DEADLINE_S", raising=False)
    client, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    _age(env, sid, 4 * 60)
    env.runtime.add_foreign(f"lab-{sid}", sid, running=False)  # created, never confirmed
    env.janitor()
    row = env.row(sid)
    assert row["status"] in ("ended", "expired")
    assert row["end_reason"] == "orphan"
    assert f"lab-{sid}" in env.runtime.removed
    assert f"lab-{sid}" not in env.runtime.containers
    assert env.live_rows(h) == []
    assert env.start(client, "beta").status_code == 201  # the student is not locked out


def test_a_stale_starting_row_without_any_container_is_ended_as_orphan(env, monkeypatch):
    monkeypatch.delenv("LF_START_DEADLINE_S", raising=False)
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")  # manager crashed between insert and create
    _age(env, sid, 4 * 60)
    env.janitor()
    row = env.row(sid)
    assert row["status"] in ("ended", "expired")
    assert row["end_reason"] == "orphan"


def test_a_stale_starting_row_with_a_running_container_is_ended_too(env, monkeypatch):
    """DB error on the update to `running`: the container runs but the row never said so."""
    monkeypatch.delenv("LF_START_DEADLINE_S", raising=False)
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    _age(env, sid, 4 * 60)
    env.runtime.add_foreign(f"lab-{sid}", sid, running=True)
    env.janitor()
    assert env.row(sid)["end_reason"] == "orphan"
    assert f"lab-{sid}" in env.runtime.removed


def test_a_starting_row_younger_than_the_default_deadline_is_left_alone(env, monkeypatch):
    monkeypatch.delenv("LF_START_DEADLINE_S", raising=False)
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    _age(env, sid, 2 * 60)
    env.runtime.add_foreign(f"lab-{sid}", sid, running=False)
    env.janitor()
    assert env.row(sid)["status"] == "starting"
    assert f"lab-{sid}" in env.runtime.containers
    assert f"lab-{sid}" not in env.runtime.removed


def test_the_default_start_deadline_is_three_minutes(env, monkeypatch):
    monkeypatch.delenv("LF_START_DEADLINE_S", raising=False)
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    _age(env, sid, 170)
    env.janitor()
    assert env.row(sid)["status"] == "starting", "ended before 3 minutes"
    _age(env, sid, 190)
    env.janitor()
    assert env.row(sid)["end_reason"] == "orphan", "not ended after 3 minutes"


def test_the_start_deadline_is_configurable_with_lf_start_deadline_s(env, monkeypatch):
    monkeypatch.setenv("LF_START_DEADLINE_S", "30")
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    _age(env, sid, 10)
    env.janitor()
    assert env.row(sid)["status"] == "starting"
    _age(env, sid, 60)
    env.janitor()
    row = env.row(sid)
    assert row["status"] in ("ended", "expired")
    assert row["end_reason"] == "orphan"


def test_the_start_deadline_does_not_end_a_running_session(env, monkeypatch):
    monkeypatch.setenv("LF_START_DEADLINE_S", "30")
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    _age(env, sid, 600)
    env.update(sid, "last_heartbeat = now()")
    env.janitor()
    assert env.row(sid)["status"] == "running"
    assert f"lab-{sid}" in env.runtime.containers


# ---------------------------------------------------------------- database unavailable


class Clock:
    def __init__(self):
        self.t = 0.0


def _drive_loop(monkeypatch, app, seconds: float) -> Clock:
    """Run `janitor.loop(app)` on a virtual clock until `seconds` of sleeping have passed."""
    from app import janitor

    real_sleep = asyncio.sleep
    clock = Clock()

    async def fake_sleep(delay, *a, **k):
        clock.t += float(delay)
        if clock.t > seconds:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def go():
        try:
            await janitor.loop(app)
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    return clock


def _app(pool, runtime):
    return SimpleNamespace(state=SimpleNamespace(pool=pool, runtime=runtime))


class CountingRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.lists = 0

    def list_lab_containers(self):
        self.lists += 1
        return super().list_lab_containers()


@pytest.fixture
def quiet_host(monkeypatch, tmp_path):
    """Plenty of memory and disk, interval 60 s, so only the DB-down behaviour logs anything."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 33554432 kB\nMemFree: 16777216 kB\nMemAvailable: 16777216 kB\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LF_MEMINFO_PATH", str(meminfo))
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "0")
    monkeypatch.setenv("LF_JANITOR_INTERVAL_S", "60")
    monkeypatch.delenv("LF_HEARTBEAT_TIMEOUT_S", raising=False)


def test_without_a_pool_the_expiry_sweep_runs_at_the_janitor_interval_not_every_2_seconds(
    monkeypatch, quiet_host
):
    rt = CountingRuntime()
    _drive_loop(monkeypatch, _app(None, rt), 120)
    assert rt.lists >= 1, "the first sweep must run at once"
    assert rt.lists <= 6, f"{rt.lists} sweeps in 120 s without a database: far too often"


def test_the_first_sweep_without_a_pool_still_removes_an_expired_container(
    monkeypatch, quiet_host
):
    now = int(dt.datetime.now(dt.UTC).timestamp())
    old = str(uuid.uuid4())
    rt = CountingRuntime()
    rt.add_foreign(f"lab-{old}", old, labels={"lf.expires": str(now - 600)})
    _drive_loop(monkeypatch, _app(None, rt), 5)
    assert f"lab-{old}" in rt.removed


def test_with_a_broken_pool_the_sweep_does_not_run_every_2_seconds_either(
    monkeypatch, quiet_host
):
    rt = CountingRuntime()
    _drive_loop(monkeypatch, _app(_BrokenPool(), rt), 120)
    assert 1 <= rt.lists <= 6, rt.lists


def _identical_errors(caplog) -> Counter:
    return Counter(
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    )


def test_repeated_identical_database_errors_are_not_logged_every_pass(
    monkeypatch, quiet_host, caplog
):
    rt = CountingRuntime()
    with caplog.at_level(logging.DEBUG):
        _drive_loop(monkeypatch, _app(_BrokenPool(), rt), 600)
    assert rt.lists >= 3, "too few passes to tell (the janitor must keep trying)"
    errors = _identical_errors(caplog)
    assert errors, "a database outage must be logged at least once"
    worst, count = errors.most_common(1)[0]
    assert count <= 2, f"logged {count} times in {rt.lists} passes: {worst!r}"


def test_repeated_identical_errors_without_a_pool_are_not_logged_every_pass(
    monkeypatch, quiet_host, caplog
):
    class Failing(CountingRuntime):
        def list_lab_containers(self):
            self.lists += 1
            raise RuntimeError("fake docker list failure")

    rt = Failing()
    with caplog.at_level(logging.DEBUG):
        _drive_loop(monkeypatch, _app(None, rt), 600)
    assert rt.lists >= 3
    errors = _identical_errors(caplog)
    assert errors
    worst, count = errors.most_common(1)[0]
    assert count <= 2, f"logged {count} times in {rt.lists} passes: {worst!r}"


def test_a_database_that_comes_back_is_swept_normally_again(monkeypatch, quiet_host, caplog):
    """After the outage the pool works again: the next pass uses it (no stuck backoff)."""
    from app import janitor

    class Pool:
        def __init__(self):
            self.calls = 0

        async def fetch(self, *a, **k):
            self.calls += 1
            return []

        async def fetchval(self, *a, **k):
            self.calls += 1
            return None

    rt = CountingRuntime()
    app = _app(None, rt)
    asyncio.run(janitor.run_once(app))  # outage pass
    app.state.pool = Pool()
    asyncio.run(janitor.run_once(app))
    assert app.state.pool.calls > 0, "a returned database was not used on the next pass"
