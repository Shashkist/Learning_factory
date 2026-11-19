"""Acceptance tests for issue #177: session start follow-ups from the #139 review.

Criterion 1: while the Docker socket is unreachable (transport error, connect error) during a
start, the readiness wait keeps waiting and `POST /api/sessions` succeeds once the container
answers. Today the liveness check inside the wait raises on a connect error, the start fails
with 502 and the container is removed. A container that is gone (404) or stopped
(`Running: false`) still ends the wait at once (existing #139 behaviour, kept); an unhelpful
answer (500) keeps waiting; the wait stays bounded by its timeout.

Criterion 2: the whole `POST /api/sessions` is bounded by one deadline taken when the request
starts, so nginx's 60 s (D56) holds even when earlier steps (forced end of the previous session,
create/start) are slow. Scaled down with LF_READY_TIMEOUT_S (a couple of seconds, no real 60 s).

Criteria 3 to 5 (DECISIONS wording, typing, unit-test nits) are docs/typing/unit-test changes
with no observable behaviour: no exam.

No database and no Docker: the Docker Engine API is a fake HTTP transport and the pool a tiny
in-memory fake. No student data.
"""

import asyncio
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app import sessions
from app.runtime import DockerRuntime

CONNECT_ERROR = httpx.ConnectError("docker socket unreachable")


# ================================================================ criterion 1: runtime


class FakeDocker:
    """A Docker Engine API over httpx.MockTransport.

    The first `outage` requests raise a transport error. After that the container answers:
    `inspect` is the answer of GET /containers/<name>/json, and ttyd is running (the probe exec
    exits 0) when `ttyd_up` is true. Every request is recorded in `seen`.
    """

    def __init__(self, outage=0, inspect=(200, {"State": {"Running": True}}), ttyd_up=True):
        self.outage, self.inspect, self.ttyd_up = outage, inspect, ttyd_up
        self.seen: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((request.method, request.url.path))
        if len(self.seen) <= self.outage:
            raise CONNECT_ERROR
        path = request.url.path
        if request.method == "POST" and path.endswith("/exec"):
            return httpx.Response(201, json={"Id": "exec-1"})
        if path.startswith("/exec/"):
            code = 0 if self.ttyd_up else 1
            return httpx.Response(200, json={"Running": False, "ExitCode": code})
        if request.method == "POST" and path == "/containers/create":
            return httpx.Response(201, json={"Id": "c1"})
        if request.method == "POST" and path.endswith("/start"):
            return httpx.Response(204)
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.startswith("/containers/"):
            status, body = self.inspect
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"message": "no such thing"})

    def runtime(self) -> DockerRuntime:
        rt = DockerRuntime.__new__(DockerRuntime)
        fake = self

        def client() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(fake.handle), base_url="http://docker"
            )

        async def stream_exec(exec_id, script, out):
            return None  # the probe's output is not looked at, only its exit code

        rt._client = client  # type: ignore[method-assign]
        rt._stream_exec = stream_exec  # type: ignore[method-assign]
        return rt

    @property
    def removed(self) -> bool:
        return any(m == "DELETE" for m, _ in self.seen)


def test_a_connect_error_during_the_wait_keeps_waiting_until_the_container_answers():
    fake = FakeDocker(outage=4)
    t0 = time.monotonic()
    asyncio.run(fake.runtime().wait_ready("lab-x", 10))
    assert time.monotonic() - t0 < 5
    assert len(fake.seen) > 4, "the wait gave up during the outage instead of retrying"


def test_a_connect_error_on_every_probe_ends_with_the_wait_timeout_not_a_transport_error():
    fake = FakeDocker(outage=10**6)
    with pytest.raises(TimeoutError):
        asyncio.run(fake.runtime().wait_ready("lab-x", 0.8))
    assert len(fake.seen) > 1, "it must retry, not stop at the first connect error"


def test_a_server_error_answer_keeps_the_wait_going_until_ready():
    # liveness cannot be told (500): keep waiting; ttyd comes up once the outage is over
    fake = FakeDocker(outage=2, inspect=(500, {"message": "boom"}))
    asyncio.run(fake.runtime().wait_ready("lab-x", 10))


def test_a_server_error_answer_with_ttyd_never_up_ends_with_the_wait_timeout():
    fake = FakeDocker(inspect=(500, {"message": "boom"}), ttyd_up=False)
    with pytest.raises(TimeoutError):
        asyncio.run(fake.runtime().wait_ready("lab-x", 0.8))


def test_a_container_that_is_gone_ends_the_wait_at_once_after_an_outage():
    fake = FakeDocker(outage=2, inspect=(404, {"message": "no such container"}), ttyd_up=False)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError):
        asyncio.run(fake.runtime().wait_ready("lab-x", 10))
    assert time.monotonic() - t0 < 5


def test_a_stopped_container_ends_the_wait_at_once_after_an_outage():
    fake = FakeDocker(outage=2, inspect=(200, {"State": {"Running": False}}), ttyd_up=False)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError):
        asyncio.run(fake.runtime().wait_ready("lab-x", 10))
    assert time.monotonic() - t0 < 5


def test_a_running_container_with_ttyd_up_is_ready_without_any_waiting():
    fake = FakeDocker()
    t0 = time.monotonic()
    asyncio.run(fake.runtime().wait_ready("lab-x", 10))
    assert time.monotonic() - t0 < 1


# ================================================================ the start endpoint, no database


class Pool:
    """Just enough of the asyncpg pool for `start`: one optional live session to force-end."""

    def __init__(self, existing=None):
        self.existing = existing
        self.new_id = uuid.uuid4()
        self.sql: list[str] = []

    # asyncpg's pool hands out a connection; here the connection is the pool itself.
    async def acquire(self, timeout=None):
        return self

    async def release(self, conn, timeout=None):
        pass

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        if sql.lstrip().startswith("insert into"):
            return {"id": self.new_id, "lab_id": "l", "status": "starting", "expires_at": None}
        return self.existing  # the live-session lookup

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if "set status = 'ended'" in sql:
            self.existing = None
            return args[0]
        return args[0]  # 'running' mark: returns the id

    async def execute(self, sql, *args):
        self.sql.append(sql)
        return "DELETE 1"


def _start(monkeypatch, runtime, pool, force=False):
    from fastapi import Response

    lab = SimpleNamespace(id="l", status="public", time_limit_min=5)
    monkeypatch.setattr(sessions, "container_spec", lambda lab, sid: {"name": f"lab-{sid}"})
    monkeypatch.setattr(sessions, "memory_limit_mb", lambda lab: 1)
    monkeypatch.setattr(sessions.admission, "resources_ok", lambda mb: True)

    async def ok(*a):
        return True

    monkeypatch.setattr(sessions.admission, "cap_ok", ok)
    state = SimpleNamespace(labs={"l": lab}, pool=pool, runtime=runtime)
    req = SimpleNamespace(app=SimpleNamespace(state=state))
    body = sessions.StartBody(lab_id="l", force=force)
    resp = Response()

    async def go():
        return await sessions.start(body, req, resp, SimpleNamespace(subject="s"))

    return go


def test_post_sessions_succeeds_after_a_socket_outage_and_does_not_remove_the_container(
    monkeypatch,
):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "20")
    # outage starts after create+start answered (the first 2 requests)
    fake = FakeDocker(inspect=(200, {"State": {"Running": True}}), ttyd_up=False)
    flips = {"n": 0}
    base = fake.handle

    def handle(request):
        flips["n"] += 1
        if 2 < flips["n"] <= 8:
            raise CONNECT_ERROR
        if flips["n"] > 8:
            fake.ttyd_up = True
        return base(request)

    fake.handle = handle  # type: ignore[method-assign]
    pool = Pool()
    out = asyncio.run(_start(monkeypatch, fake.runtime(), pool)())
    assert out["status"] == "running"
    assert out["id"] == str(pool.new_id)
    assert not fake.removed, "the container was removed although the socket only blinked"
    assert not any(s.startswith("delete from") for s in pool.sql)


# ================================================================ criterion 2: one deadline


class SlowRuntime:
    """Slow earlier steps; the wait gets whatever time it is given and never becomes ready."""

    def __init__(self, remove_s=0.0, start_s=0.0):
        self.remove_s, self.start_s = remove_s, start_s
        self.wait_timeouts: list[float] = []
        self.removes = 0

    async def start(self, spec):
        await asyncio.sleep(self.start_s)
        return str(spec["name"])

    async def wait_ready(self, name, timeout):
        self.wait_timeouts.append(float(timeout))
        await asyncio.sleep(timeout)
        raise TimeoutError("never ready")

    async def remove(self, name):
        self.removes += 1
        if self.removes == 1:  # the previous session's container
            await asyncio.sleep(self.remove_s)


def _existing():
    return {"id": uuid.uuid4(), "lab_id": "other", "status": "running", "expires_at": None}


def _run_start(monkeypatch, rt, force):
    from fastapi import HTTPException

    pool = Pool(existing=_existing() if force else None)
    t0 = time.monotonic()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(_start(monkeypatch, rt, pool, force=force)())
    return time.monotonic() - t0, caught.value


def test_a_slow_forced_end_and_a_slow_start_share_one_deadline_with_the_wait(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "3")
    rt = SlowRuntime(remove_s=1.0, start_s=1.0)
    elapsed, err = _run_start(monkeypatch, rt, force=True)
    assert err.status_code >= 500
    # one deadline from the request start (3 s): not 1 + 1 + 3 = 5 s in sequence
    assert elapsed < 4.0, f"the whole start took {elapsed:.1f} s, expected one 3 s budget"
    for t in rt.wait_timeouts:  # if the wait ran at all, it got only what was left
        assert t <= 1.6, f"the wait was given {t:.1f} s although about 1 s was left"


def test_a_start_that_already_used_the_budget_does_not_wait_again(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "2")
    rt = SlowRuntime(start_s=2.5)
    elapsed, err = _run_start(monkeypatch, rt, force=False)
    assert err.status_code >= 500
    assert elapsed < 3.5, f"the whole start took {elapsed:.1f} s after a 2.5 s create/start"
    for t in rt.wait_timeouts:
        assert t <= 0.5, f"the wait was given {t:.1f} s with the budget already spent"


def test_a_fast_start_still_gives_the_wait_the_whole_configured_budget(monkeypatch):
    monkeypatch.setenv("LF_READY_TIMEOUT_S", "3")
    rt = SlowRuntime()
    elapsed, err = _run_start(monkeypatch, rt, force=False)
    assert err.status_code >= 500
    assert rt.wait_timeouts, "the wait must run when the earlier steps were instant"
    assert rt.wait_timeouts[0] >= 2.5, f"the wait got {rt.wait_timeouts[0]:.2f} s of 3 s"
    assert elapsed < 4.0
