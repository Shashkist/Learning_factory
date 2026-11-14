"""Acceptance tests for issue #7: session lifecycle (API, admission, janitor, run flags).

Interfaces are those of D24. Where D24 leaves a detail open, this exam chose the simplest
reading (the build must follow it or change the exam through the manager):

* `app.runtime.container_spec(lab, session_id)` takes a `Lab` from `app.content.load_labs`
  and the session id as a str. It returns the Engine API create body (`Image`, `Env` as a
  list of `K=V`, `Labels`, `HostConfig`) plus a top-level `"name"` key (`lab-<uuid>`),
  because the Engine API takes the name outside the body.
* `LabRuntime.start(spec)` returns the container name; `remove(name)` is idempotent;
  `list_lab_containers()` yields items that unpack as (name, running, session id). The
  fake below tolerates sync or async methods and attribute, key or tuple access.
* `app.janitor.run_once` is called with the FastAPI app (`run_once(app)`); if its
  signature names `pool`, `runtime` or `labs` instead, those are passed by name.
* The fake's list items still unpack as (name, running, session id); they also carry the
  container's labels (`.labels`, key `labels`) and the parsed `lf.expires` epoch (`.expires`,
  key `expires`, None if absent), so a sweep that needs the expiry reads it from there.
* A runtime failure while starting must not leave a live session behind (a student would be
  locked out until the janitor ran) and answers 5xx.
* An unreachable Docker at startup must not stop the manager from starting.
* A janitor-ended session has status `ended` or `expired`; the reason is `end_reason`.

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and
skip locally when it is absent (CI sets it and fails on any skip). Student ids are fake.
Docker is never used here; see test_issue_7_session_runtime_docker.py for that.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import inspect
import itertools
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)
MB = 1024 * 1024

_n = itertools.count(1)

LABS = {
    "alpha": {"image": "term", "minutes": 15, "sudo": False},
    "beta": {"image": "term", "minutes": 30, "sudo": False},
    "gamma": {"image": "code", "minutes": 20, "sudo": False},
    "sudoer": {"image": "term", "minutes": 10, "sudo": True},
}


def write_content(root: Path) -> Path:
    """A tiny content tree: <root>/test/<lab>/lab.yaml with one quiz task each."""
    for lab_id, cfg in LABS.items():
        d = root / "test" / lab_id
        d.mkdir(parents=True)
        (d / "lab.yaml").write_text(
            f"""id: {lab_id}
title:
  en: Lab {lab_id}
  he: מעבדה {lab_id}
description:
  en: Fake lab for the session exam.
  he: מעבדה מזויפת למבחן הסשנים.
image: {cfg["image"]}
time_limit_min: {cfg["minutes"]}
internet: false
allow_domains: []
sudo: {str(cfg["sudo"]).lower()}
status: published
module: 1
lesson: 5
tasks:
  - id: q1
    kind: quiz
    question:
      en: Pick one.
      he: בחר אחד.
    options:
      - en: a
        he: א
      - en: b
        he: ב
    correct: 0
""",
            encoding="utf-8",
        )
    return root


def title_of(lab_id: str) -> dict[str, str]:
    return {"en": f"Lab {lab_id}", "he": f"מעבדה {lab_id}"}


# ---------------------------------------------------------------- fake runtime


def _aw(value):
    async def co():
        return value

    return co().__await__()


class _Nothing:
    def __await__(self):
        return _aw(None)


class _Seq(list):
    def __await__(self):
        return _aw(list(self))


class _Name(str):
    def __await__(self):
        return _aw(str(self))


class Cont(tuple):
    """(name, running, session id) with attribute and key access."""

    def __new__(cls, name, running, session, labels=None):
        self = super().__new__(cls, (name, running, session))
        self.labels = dict(labels or {})
        raw = self.labels.get("lf.expires")
        self.expires = int(raw) if raw not in (None, "") else None
        return self

    @property
    def name(self):
        return self[0]

    @property
    def running(self):
        return self[1]

    @property
    def session(self):
        return self[2]

    session_id = session

    def __getitem__(self, key):
        if isinstance(key, str):
            return {
                "name": self[0], "running": self[1], "session": self[2], "session_id": self[2],
                "labels": self.labels, "expires": self.expires,
            }[key]
        return tuple.__getitem__(self, key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class FakeRuntime:
    """Records what the manager asks Docker to do; methods work sync or awaited."""

    def __init__(self):
        self.lock = threading.Lock()
        self.containers: dict[str, tuple[bool, str]] = {}
        self.specs: list[dict] = []
        self.started: list[str] = []
        self.removed: list[str] = []
        self.fail_next_start = False
        self.fail_next_list = False
        self.labels: dict[str, dict] = {}

    def start(self, spec):
        with self.lock:
            if self.fail_next_start:
                self.fail_next_start = False
                raise RuntimeError("fake docker failure")
            session = (spec.get("Labels") or {}).get("lf.session", "")
            name = spec.get("name") or spec.get("Name") or f"lab-{session}"
            self.specs.append(spec)
            self.started.append(name)
            self.containers[name] = (True, session)
            self.labels[name] = dict(spec.get("Labels") or {})
            return _Name(name)

    def remove(self, name):
        with self.lock:
            self.removed.append(name)
            self.containers.pop(name, None)
            return _Nothing()

    def list_lab_containers(self):
        with self.lock:
            if self.fail_next_list:
                self.fail_next_list = False
                raise RuntimeError("fake docker list failure")
            return _Seq(
                Cont(n, r, s, self.labels.get(n)) for n, (r, s) in self.containers.items()
            )

    # test helpers
    def add_foreign(self, name, session, running=True, labels=None):
        with self.lock:
            self.containers[name] = (running, session)
            self.labels[name] = {"lf": "1", "lf.session": session, **(labels or {})}

    def vanish(self, name):
        with self.lock:
            self.containers.pop(name, None)


# ---------------------------------------------------------------- container_spec (no Docker)


def _lab(lab_id: str, tmp_path: Path):
    from app.content import load_labs

    res = load_labs(write_content(tmp_path))
    assert lab_id in res.labs, res.errors
    return res.labs[lab_id]


def _spec(lab_id: str, tmp_path: Path, monkeypatch, sid: str | None = None):
    from app import runtime

    for var in ("LF_LAB_NETWORK", "LF_LAB_TERM_IMAGE"):
        monkeypatch.delenv(var, raising=False)
    sid = sid or str(uuid.uuid4())
    return runtime.container_spec(_lab(lab_id, tmp_path), sid), sid


def _size(opts: str) -> int:
    m = re.search(r"(?:^|,)size=(\d+)([kmg]?)(?:,|$)", opts, re.I)
    assert m, f"no size in tmpfs options {opts!r}"
    mult = {"": 1, "k": 1024, "m": MB, "g": 1024 * MB}[m.group(2).lower()]
    return int(m.group(1)) * mult


def test_spec_runs_under_runsc_on_the_lab_network(tmp_path, monkeypatch):
    spec, _ = _spec("alpha", tmp_path, monkeypatch)
    host = spec["HostConfig"]
    assert host["Runtime"] == "runsc"
    assert host["NetworkMode"] == "lf-labs"


def test_spec_uses_the_term_image_default(tmp_path, monkeypatch):
    spec, _ = _spec("alpha", tmp_path, monkeypatch)
    assert spec["Image"] == "ghcr.io/shashkist/lf-lab-term:latest"


def test_spec_memory_256mb_and_swap_equal_to_memory(tmp_path, monkeypatch):
    host = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]
    assert host["Memory"] in (256 * MB, 256 * 10**6)
    assert host["MemorySwap"] == host["Memory"]


def test_spec_code_lab_gets_768mb(tmp_path, monkeypatch):
    host = _spec("gamma", tmp_path, monkeypatch)[0]["HostConfig"]
    assert host["Memory"] in (768 * MB, 768 * 10**6)
    assert host["MemorySwap"] == host["Memory"]


def test_spec_half_a_cpu_and_256_pids(tmp_path, monkeypatch):
    host = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]
    assert host["NanoCpus"] == 500_000_000
    assert host["PidsLimit"] == 256


def test_spec_drops_all_caps_and_adds_only_setuid_setgid(tmp_path, monkeypatch):
    host = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]
    assert [c.upper() for c in host["CapDrop"]] == ["ALL"]
    assert sorted(c.upper().removeprefix("CAP_") for c in host["CapAdd"]) == ["SETGID", "SETUID"]
    assert not host.get("Privileged")


def test_spec_has_no_new_privileges_without_sudo(tmp_path, monkeypatch):
    host = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]
    assert any(str(o).startswith("no-new-privileges") for o in host.get("SecurityOpt") or [])


def test_spec_sudo_lab_drops_no_new_privileges(tmp_path, monkeypatch):
    host = _spec("sudoer", tmp_path, monkeypatch)[0]["HostConfig"]
    assert not any(str(o).startswith("no-new-privileges") for o in host.get("SecurityOpt") or [])
    # still capability-restricted
    assert [c.upper() for c in host["CapDrop"]] == ["ALL"]


def test_spec_log_driver_none(tmp_path, monkeypatch):
    host = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]
    assert host["LogConfig"]["Type"] == "none"


def test_spec_has_no_volumes_binds_mounts_or_published_ports(tmp_path, monkeypatch):
    spec = _spec("alpha", tmp_path, monkeypatch)[0]
    host = spec["HostConfig"]
    assert not host.get("Binds")
    assert not host.get("Mounts")
    assert not host.get("VolumesFrom")
    assert not spec.get("Volumes")
    assert not host.get("PortBindings")
    assert not host.get("PublishAllPorts")


def test_spec_command_log_dir_is_a_small_private_tmpfs(tmp_path, monkeypatch):
    tmpfs = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]["Tmpfs"]
    opts = tmpfs["/var/log/lf"]
    assert re.search(r"(?:^|,)mode=0?700(?:,|$)", opts), opts
    assert 0 < _size(opts) <= 64 * MB, opts


def test_spec_home_is_a_size_capped_tmpfs(tmp_path, monkeypatch):
    tmpfs = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]["Tmpfs"]
    size = _size(tmpfs["/home/student"])
    assert 16 * MB <= size <= 512 * MB


def test_spec_name_labels_and_base_path_env(tmp_path, monkeypatch):
    spec, sid = _spec("alpha", tmp_path, monkeypatch)
    assert (spec.get("name") or spec.get("Name")) == f"lab-{sid}"
    assert spec["Labels"]["lf"] == "1"
    assert spec["Labels"]["lf.session"] == sid
    assert f"LF_BASE_PATH=/s/{sid}/term/" in spec["Env"]


def test_spec_is_unique_per_session(tmp_path, monkeypatch):
    a, _ = _spec("alpha", tmp_path, monkeypatch)
    b = _spec("alpha", tmp_path / "again", monkeypatch)[0]
    assert (a.get("name") or a.get("Name")) != (b.get("name") or b.get("Name"))


# ---------------------------------------------------------------- database harness


def _admin_dsn() -> str:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set (no Postgres available)")
    host = urlsplit(dsn).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail(f"LF_TEST_DATABASE_URL must point at a local throwaway Postgres, not {host!r}")
    return dsn


def _app_dsn(admin_dsn: str) -> str:
    parts = urlsplit(admin_dsn)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_as(dsn: str, fn):
    async def go():
        import asyncpg  # a missing asyncpg must fail, not skip

        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


def id_hash(raw: str) -> str:
    return hmac.new(TEST_SECRET.encode(), raw.lstrip("0").encode(), hashlib.sha256).hexdigest()


def fresh_ip() -> str:
    n = next(_n)
    return f"10.77.{n // 250}.{n % 250 + 1}"


def _meminfo(path: Path, available_mb: int) -> None:
    path.write_text(
        f"MemTotal:       {available_mb * 2048} kB\nMemFree:        {available_mb * 512} kB\n"
        f"MemAvailable:   {available_mb * 1024} kB\nSwapTotal:      0 kB\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def _db():
    admin_dsn = _admin_dsn()

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        assert OWNER_SQL.is_file(), f"{OWNER_SQL} is missing"
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)
    return admin_dsn


class Env:
    def __init__(self, admin_dsn, client, runtime, meminfo: Path, tmp_path: Path):
        self.admin_dsn = admin_dsn
        self.client = client
        self.app = client.app
        self.runtime = runtime
        self.meminfo = meminfo
        self.tmp_path = tmp_path
        self.cohort_id: int | None = None

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def _client(self):
        from fastapi.testclient import TestClient

        c = TestClient(self.app, base_url="https://testserver", client=(fresh_ip(), 50000))
        c.portal = self.client.portal  # the app's pool belongs to the lifespan client's loop
        return c

    def student(self, access_until: dt.date | None = None):
        """Seed a fake student, log in; returns (client, raw id, id hash)."""
        from argon2 import PasswordHasher

        raw = str(800000000 + next(_n))
        pw = PasswordHasher().hash(COHORT_PASSWORD)

        async def seed(conn):
            if self.cohort_id is None:
                self.cohort_id = await conn.fetchval(
                    f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                    "values ($1, $2, $3) returning id",
                    f"Fake cohort {next(_n)}", pw, TODAY + 30 * DAY,
                )
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id, access_until) "
                "values ($1, $2, $3, $4)",
                id_hash(raw), f"Fake Student {raw}", self.cohort_id, access_until,
            )

        self.sql(seed)
        client = self._client()
        resp = client.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return client, raw, id_hash(raw)

    def row(self, session_id: str) -> dict:
        async def go(conn):
            r = await conn.fetchrow(
                f"select * from {SCHEMA}.lab_sessions where id = $1", session_id
            )
            return dict(r) if r else {}

        return self.sql(go)

    def live_rows(self, student_hash: str) -> list[dict]:
        async def go(conn):
            rows = await conn.fetch(
                f"select * from {SCHEMA}.lab_sessions where student = $1 "
                "and status in ('starting', 'running')",
                student_hash,
            )
            return [dict(r) for r in rows]

        return self.sql(go)

    def update(self, session_id: str, assignments: str):
        async def go(conn):
            await conn.execute(
                f"update {SCHEMA}.lab_sessions set {assignments} where id = $1", session_id
            )

        self.sql(go)

    def janitor(self):
        from app import janitor

        fn = janitor.run_once
        state = self.app.state
        avail = {
            "app": self.app, "application": self.app, "pool": state.pool,
            "runtime": state.runtime, "labs": state.labs,
        }
        params = inspect.signature(fn).parameters
        kwargs = {n: avail[n] for n in params if n in avail}
        required = [
            n for n, p in params.items() if p.default is inspect.Parameter.empty and n not in avail
        ]
        args = (self.app,) if required or not params else ()
        if required:
            kwargs = {}

        async def go():
            r = fn(*args, **kwargs)
            if inspect.isawaitable(r):
                r = await r
            return r

        return self.client.portal.call(go)

    def start(self, client, lab_id: str, **body):
        return client.post("/api/sessions", json={"lab_id": lab_id, **body})


@pytest.fixture
def env(_db, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    content = write_content(tmp_path / "content")
    meminfo = tmp_path / "meminfo"
    _meminfo(meminfo, 16000)
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.setenv("LF_CONTENT_DIR", str(content))
    monkeypatch.setenv("LF_MEMINFO_PATH", str(meminfo))
    monkeypatch.setenv("LF_DOCKER_HOST", f"unix://{tmp_path / 'no-docker.sock'}")
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "0")
    monkeypatch.setenv("LF_JANITOR_INTERVAL_S", "3600")
    for var in (
        "LF_TRUSTED_PROXY", "LF_MAX_TERM", "LF_MAX_CODE", "LF_MEM_RESERVE_MB",
        "LF_HEARTBEAT_TIMEOUT_S", "LF_DISK_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.main import app

    runtime = FakeRuntime()

    async def end_all(conn):
        await conn.execute(
            f"update {SCHEMA}.lab_sessions set status = 'ended', ended_at = now(), "
            "end_reason = 'orphan' where status in ('starting', 'running')"
        )

    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        app.state.runtime = runtime
        e = Env(_db, c, runtime, meminfo, tmp_path)
        e.sql(end_all)  # earlier tests' live sessions must not count against caps
        yield e
        e.sql(end_all)


# ---------------------------------------------------------------- start / reopen / conflict


def test_start_returns_201_with_session_shape_and_runs_one_container(env):
    client, _, h = env.student()
    resp = env.start(client, "alpha")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) >= {"id", "lab_id", "status", "expires_at"}
    assert body["lab_id"] == "alpha"
    assert body["status"] in ("starting", "running")
    uuid.UUID(body["id"])
    assert env.runtime.started == [f"lab-{body['id']}"]
    spec = env.runtime.specs[0]
    assert spec["Labels"]["lf.session"] == body["id"]
    row = env.row(body["id"])
    assert row["student"] == h and row["lab_id"] == "alpha"
    assert row["status"] in ("starting", "running")
    assert row["container"] == f"lab-{body['id']}"


def test_start_sets_expiry_to_start_plus_the_lab_time_limit(env):
    client, _, _ = env.student()
    body = env.start(client, "alpha").json()  # alpha: 15 minutes
    now = dt.datetime.now(dt.UTC)
    exp = dt.datetime.fromisoformat(body["expires_at"])
    assert exp.tzinfo is not None
    assert dt.timedelta(minutes=14) < exp - now < dt.timedelta(minutes=16), body["expires_at"]
    db_exp = env.row(body["id"])["expires_at"]
    assert abs((db_exp - exp).total_seconds()) < 2


def test_start_requires_login(env):
    from fastapi.testclient import TestClient

    anon = TestClient(env.app, base_url="https://testserver", client=(fresh_ip(), 50000))
    anon.portal = env.client.portal
    assert anon.post("/api/sessions", json={"lab_id": "alpha"}).status_code == 401
    assert env.runtime.started == []


def test_unknown_lab_is_404_unknown_lab_and_starts_nothing(env):
    client, _, h = env.student()
    resp = env.start(client, "no-such-lab")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "unknown_lab"}
    assert env.runtime.started == []
    assert env.live_rows(h) == []


def test_reopening_the_same_lab_returns_200_and_the_same_running_session(env):
    client, _, _ = env.student()
    first = env.start(client, "alpha").json()
    again = env.start(client, "alpha")
    assert again.status_code == 200
    assert again.json()["id"] == first["id"]
    assert set(again.json()) >= {"id", "lab_id", "status", "expires_at"}
    assert len(env.runtime.started) == 1


def test_another_lab_open_is_409_with_the_open_labs_id_and_title(env):
    client, _, h = env.student()
    first = env.start(client, "alpha").json()
    resp = env.start(client, "beta")
    assert resp.status_code == 409
    assert resp.json() == {
        "detail": {
            "code": "another_lab_open",
            "session_id": first["id"],
            "lab_id": "alpha",
            "title": title_of("alpha"),
        }
    }
    assert len(env.runtime.started) == 1
    assert [r["id"] for r in env.live_rows(h)] == [uuid.UUID(first["id"])]


def test_force_ends_the_old_session_first_and_starts_the_new_one(env):
    client, _, h = env.student()
    old = env.start(client, "alpha").json()
    resp = env.start(client, "beta", force=True)
    assert resp.status_code == 201, resp.text
    new = resp.json()
    assert new["lab_id"] == "beta" and new["id"] != old["id"]
    assert f"lab-{old['id']}" in env.runtime.removed
    assert f"lab-{old['id']}" not in env.runtime.containers
    assert f"lab-{new['id']}" in env.runtime.containers
    old_row = env.row(old["id"])
    assert old_row["status"] in ("ended", "expired")
    assert old_row["end_reason"] == "forced"
    assert old_row["ended_at"] is not None
    assert [str(r["id"]) for r in env.live_rows(h)] == [new["id"]]


def test_one_live_session_per_student_under_concurrent_starts(env):
    client, _, h = env.student()
    results: list[int] = []

    def go(lab):
        results.append(env.start(client, lab).status_code)

    threads = [threading.Thread(target=go, args=(lab,)) for lab in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 409], results
    live = env.live_rows(h)
    assert len(live) == 1
    # the loser must not leave a container running
    assert list(env.runtime.containers) == [f"lab-{live[0]['id']}"]


def test_runtime_failure_while_starting_leaves_no_live_session(env):
    client, _, h = env.student()
    env.runtime.fail_next_start = True
    resp = env.start(client, "alpha")
    assert 500 <= resp.status_code < 600, resp.text
    assert env.live_rows(h) == []
    retry = env.start(client, "alpha")
    assert retry.status_code == 201, retry.text


def test_manager_starts_when_docker_is_unreachable(env):
    # the fixture points LF_DOCKER_HOST at a socket that does not exist
    assert env.client.get("/healthz").json() == {"ok": True}
    assert env.client.get("/api/health").status_code == 200


# ---------------------------------------------------------------- current / heartbeat / end


def test_current_returns_the_live_session_or_404_no_session(env):
    client, _, _ = env.student()
    none = client.get("/api/sessions/current")
    assert none.status_code == 404
    assert none.json() == {"detail": "no_session"}
    first = env.start(client, "alpha").json()
    cur = client.get("/api/sessions/current")
    assert cur.status_code == 200
    assert cur.json()["id"] == first["id"]
    assert set(cur.json()) >= {"id", "lab_id", "status", "expires_at"}


def test_heartbeat_returns_204_and_refreshes_last_heartbeat(env):
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    env.update(sid, "last_heartbeat = now() - interval '4 minutes'")
    resp = client.post(f"/api/sessions/{sid}/heartbeat")
    assert resp.status_code == 204
    age = dt.datetime.now(dt.UTC) - env.row(sid)["last_heartbeat"]
    assert age < dt.timedelta(seconds=60), age


def test_heartbeat_for_another_students_session_is_404(env):
    a, _, _ = env.student()
    b, _, _ = env.student()
    sid = env.start(a, "alpha").json()["id"]
    env.update(sid, "last_heartbeat = now() - interval '4 minutes'")
    assert b.post(f"/api/sessions/{sid}/heartbeat").status_code == 404
    age = dt.datetime.now(dt.UTC) - env.row(sid)["last_heartbeat"]
    assert age > dt.timedelta(minutes=3), "someone else's heartbeat refreshed the session"


def test_heartbeat_for_unknown_session_is_404(env):
    client, _, _ = env.student()
    assert client.post(f"/api/sessions/{uuid.uuid4()}/heartbeat").status_code == 404


def test_end_returns_204_marks_ended_and_removes_the_container(env):
    client, _, h = env.student()
    sid = env.start(client, "alpha").json()["id"]
    resp = client.post(f"/api/sessions/{sid}/end")
    assert resp.status_code == 204
    row = env.row(sid)
    assert row["status"] == "ended"
    assert row["end_reason"] == "student"
    assert row["ended_at"] is not None
    assert f"lab-{sid}" in env.runtime.removed
    assert env.runtime.containers == {}
    assert env.live_rows(h) == []
    assert client.get("/api/sessions/current").status_code == 404
    assert client.post(f"/api/sessions/{sid}/heartbeat").status_code == 404


def test_end_twice_is_204_both_times(env):
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204


def test_ending_makes_room_to_open_another_lab(env):
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    client.post(f"/api/sessions/{sid}/end")
    assert env.start(client, "beta").status_code == 201


def test_a_student_cannot_end_another_students_session(env):
    a, _, _ = env.student()
    b, _, _ = env.student()
    sid = env.start(a, "alpha").json()["id"]
    resp = b.post(f"/api/sessions/{sid}/end")
    assert resp.status_code in (403, 404)
    assert env.row(sid)["status"] in ("starting", "running")
    assert f"lab-{sid}" in env.runtime.containers


def test_session_endpoints_require_login(env):
    from fastapi.testclient import TestClient

    anon = TestClient(env.app, base_url="https://testserver", client=(fresh_ip(), 50000))
    anon.portal = env.client.portal
    sid = uuid.uuid4()
    assert anon.get("/api/sessions/current").status_code == 401
    assert anon.post(f"/api/sessions/{sid}/heartbeat").status_code == 401
    assert anon.post(f"/api/sessions/{sid}/end").status_code == 401


# ---------------------------------------------------------------- admission


def test_low_free_memory_is_503_capacity_full(env):
    client, _, h = env.student()
    _meminfo(env.meminfo, 1000)  # reserve is 1500
    resp = env.start(client, "alpha")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "capacity_full"}
    assert env.runtime.started == []
    assert env.live_rows(h) == []


def test_memory_guard_counts_the_new_containers_own_limit(env):
    # 1700 MB free - 256 MB for the new lab = 1444 < 1500: refuse
    client, _, _ = env.student()
    _meminfo(env.meminfo, 1700)
    assert env.start(client, "alpha").status_code == 503


def test_memory_guard_admits_when_the_reserve_stays_free(env):
    # 1800 MB free - 256 MB = 1544 >= 1500: admit
    client, _, _ = env.student()
    _meminfo(env.meminfo, 1800)
    assert env.start(client, "alpha").status_code == 201


def test_memory_reserve_is_configurable_and_read_per_request(env, monkeypatch):
    client, _, _ = env.student()
    _meminfo(env.meminfo, 1000)
    assert env.start(client, "alpha").status_code == 503
    monkeypatch.setenv("LF_MEM_RESERVE_MB", "100")
    assert env.start(client, "alpha").status_code == 201


def test_disk_below_the_reserve_is_503_capacity_full(env, monkeypatch):
    client, _, _ = env.student()
    monkeypatch.setenv("LF_DISK_PATH", str(env.tmp_path))
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "100000000")  # far more than any disk has free
    resp = env.start(client, "alpha")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "capacity_full"}
    assert env.runtime.started == []


def test_term_cap_refuses_another_students_start(env, monkeypatch):
    monkeypatch.setenv("LF_MAX_TERM", "1")
    a, _, _ = env.student()
    b, _, hb = env.student()
    assert env.start(a, "alpha").status_code == 201
    resp = env.start(b, "beta")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "capacity_full"}
    assert env.live_rows(hb) == []
    assert len(env.runtime.started) == 1


def test_term_cap_does_not_block_the_code_kind(env, monkeypatch):
    monkeypatch.setenv("LF_MAX_TERM", "1")
    a, _, _ = env.student()
    b, _, _ = env.student()
    assert env.start(a, "alpha").status_code == 201
    assert env.start(b, "gamma").status_code == 201  # gamma uses the code image


def test_code_cap_refuses_code_labs_only(env, monkeypatch):
    monkeypatch.setenv("LF_MAX_CODE", "1")
    a, _, _ = env.student()
    b, _, _ = env.student()
    c, _, _ = env.student()
    assert env.start(a, "gamma").status_code == 201
    assert env.start(b, "gamma").status_code == 503
    assert env.start(c, "alpha").status_code == 201


def test_ending_a_session_frees_a_cap_slot(env, monkeypatch):
    monkeypatch.setenv("LF_MAX_TERM", "1")
    a, _, _ = env.student()
    b, _, _ = env.student()
    sid = env.start(a, "alpha").json()["id"]
    assert env.start(b, "alpha").status_code == 503
    a.post(f"/api/sessions/{sid}/end")
    assert env.start(b, "alpha").status_code == 201


# ---------------------------------------------------------------- janitor


def test_janitor_ends_a_session_with_a_stale_heartbeat(env):
    client, _, h = env.student()
    sid = env.start(client, "alpha").json()["id"]
    env.update(sid, "last_heartbeat = now() - interval '6 minutes'")
    env.janitor()
    row = env.row(sid)
    assert row["status"] in ("ended", "expired")
    assert row["end_reason"] == "heartbeat"
    assert row["ended_at"] is not None
    assert f"lab-{sid}" in env.runtime.removed
    assert env.runtime.containers == {}
    assert env.live_rows(h) == []


def test_janitor_counts_the_start_time_as_the_first_heartbeat(env):
    client, _, _ = env.student()
    fresh = env.start(client, "alpha").json()["id"]
    other, _, _ = env.student()
    old = env.start(other, "alpha").json()["id"]
    env.update(old, "last_heartbeat = null, started_at = now() - interval '10 minutes'")
    env.janitor()
    assert env.row(fresh)["status"] in ("starting", "running")
    assert f"lab-{fresh}" in env.runtime.containers
    assert env.row(old)["status"] in ("ended", "expired")
    assert env.row(old)["end_reason"] == "heartbeat"


def test_janitor_keeps_a_session_with_a_recent_heartbeat(env):
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    env.update(sid, "last_heartbeat = now() - interval '4 minutes'")
    env.janitor()
    assert env.row(sid)["status"] in ("starting", "running")
    assert f"lab-{sid}" in env.runtime.containers


def test_janitor_ends_a_session_past_its_time_limit(env):
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    client.post(f"/api/sessions/{sid}/heartbeat")  # heartbeat is fresh: only the limit applies
    env.update(sid, "expires_at = now() - interval '1 minute'")
    env.janitor()
    row = env.row(sid)
    assert row["status"] in ("ended", "expired")
    assert row["end_reason"] == "time_limit"
    assert f"lab-{sid}" in env.runtime.removed


def test_janitor_ends_the_session_of_a_student_whose_access_expired(env):
    client, _, h = env.student(access_until=TODAY + 5 * DAY)
    sid = env.start(client, "alpha").json()["id"]

    async def expire(conn):
        await conn.execute(
            f"update {SCHEMA}.students set access_until = $1 where id_hash = $2", TODAY - DAY, h
        )

    env.sql(expire)
    env.janitor()
    row = env.row(sid)
    assert row["status"] in ("ended", "expired")
    assert row["end_reason"] == "access_expired"
    assert f"lab-{sid}" in env.runtime.removed


def test_janitor_removes_lab_containers_that_have_no_live_session(env):
    stray_session = str(uuid.uuid4())
    env.runtime.add_foreign(f"lab-{stray_session}", stray_session)
    client, _, _ = env.student()
    live = env.start(client, "alpha").json()["id"]
    env.janitor()
    assert f"lab-{stray_session}" in env.runtime.removed
    assert f"lab-{stray_session}" not in env.runtime.containers
    assert f"lab-{live}" in env.runtime.containers  # a live session's container stays
    assert f"lab-{live}" not in env.runtime.removed


def test_janitor_removes_a_stopped_container(env):
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    env.runtime.add_foreign(f"lab-{sid}", sid, running=False)
    env.janitor()
    assert f"lab-{sid}" not in env.runtime.containers
    assert f"lab-{sid}" in env.runtime.removed


def test_janitor_ends_a_live_session_whose_container_is_gone(env):
    client, _, h = env.student()
    sid = env.start(client, "alpha").json()["id"]
    env.runtime.vanish(f"lab-{sid}")
    env.janitor()
    row = env.row(sid)
    assert row["status"] in ("ended", "expired")
    assert row["end_reason"] == "orphan"
    assert env.live_rows(h) == []
    # the student can open a lab again
    assert env.start(client, "beta").status_code == 201


def test_janitor_is_idempotent(env):
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    env.update(sid, "last_heartbeat = now() - interval '6 minutes'")
    env.janitor()
    removed = list(env.runtime.removed)
    env.janitor()
    assert env.runtime.removed == removed
    assert env.row(sid)["end_reason"] == "heartbeat"


def test_heartbeat_timeout_is_configurable(env, monkeypatch):
    monkeypatch.setenv("LF_HEARTBEAT_TIMEOUT_S", "30")
    client, _, _ = env.student()
    sid = env.start(client, "alpha").json()["id"]
    env.update(sid, "last_heartbeat = now() - interval '2 minutes'")
    env.janitor()
    assert env.row(sid)["end_reason"] == "heartbeat"


# ---------------------------------------------------------------- review round 1: spec (no Docker)


def _opts_have(opts: str, flag: str) -> bool:
    return re.search(rf"(?:^|,){flag}(?:,|$)", opts) is not None


@pytest.mark.parametrize("path,cap_mb", [("/tmp", 64), ("/var/tmp", 32), ("/run/lock", 1)])
def test_spec_every_writable_scratch_dir_is_a_size_capped_tmpfs(
    tmp_path, monkeypatch, path, cap_mb
):
    tmpfs = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]["Tmpfs"]
    assert path in tmpfs, f"{path} is not a tmpfs: students could fill the host disk"
    assert 0 < _size(tmpfs[path]) <= 2 * cap_mb * MB, tmpfs[path]


def test_spec_home_and_tmp_are_mounted_exec(tmp_path, monkeypatch):
    tmpfs = _spec("alpha", tmp_path, monkeypatch)[0]["HostConfig"]["Tmpfs"]
    for path in ("/home/student", "/tmp"):
        assert _opts_have(tmpfs[path], "exec"), f"{path}: {tmpfs[path]!r} (Docker adds noexec)"
        assert not _opts_have(tmpfs[path], "noexec"), tmpfs[path]


def test_spec_sudo_lab_sets_lf_sudo_and_adds_the_ownership_capabilities(tmp_path, monkeypatch):
    spec = _spec("sudoer", tmp_path, monkeypatch)[0]
    assert "LF_SUDO=1" in spec["Env"]
    caps = {c.upper().removeprefix("CAP_") for c in spec["HostConfig"]["CapAdd"]}
    assert caps >= {"SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE", "FOWNER"}, caps
    assert [c.upper() for c in spec["HostConfig"]["CapDrop"]] == ["ALL"]
    assert not spec["HostConfig"].get("Privileged")


def test_spec_non_sudo_lab_has_no_lf_sudo_in_its_env(tmp_path, monkeypatch):
    spec = _spec("alpha", tmp_path, monkeypatch)[0]
    assert not [e for e in spec["Env"] if e.startswith("LF_SUDO")], spec["Env"]


@pytest.mark.parametrize("lab_id", ["alpha", "sudoer"])
def test_spec_labels_carry_the_expiry_epoch(tmp_path, monkeypatch, lab_id):
    spec = _spec(lab_id, tmp_path, monkeypatch)[0]
    raw = spec["Labels"]["lf.expires"]
    assert re.fullmatch(r"\d+", str(raw)), raw
    want = dt.datetime.now(dt.UTC).timestamp() + LABS[lab_id]["minutes"] * 60
    assert abs(int(raw) - want) <= 120, (raw, want)


# ---------------------------------------------------------------- review round 1: janitor safety


class GatedRuntime(FakeRuntime):
    """`start` blocks (without blocking the app's event loop) until `gate` is set."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.entered = threading.Event()

    async def start(self, spec):
        self.entered.set()
        while not self.gate.is_set():
            await asyncio.sleep(0.02)
        return str(super().start(spec))


def _insert_session(env, student_hash: str, status: str, lab_id: str = "alpha") -> str:
    async def go(conn):
        return await conn.fetchval(
            f"insert into {SCHEMA}.lab_sessions (student, lab_id, status, expires_at) "
            "values ($1, $2, $3, now() + interval '15 minutes') returning id",
            student_hash, lab_id, status,
        )

    return str(env.sql(go))


def test_janitor_does_not_remove_the_container_of_a_starting_session(env):
    _, _, h = env.student()
    sid = _insert_session(env, h, "starting")
    env.runtime.add_foreign(f"lab-{sid}", sid, running=False)  # created, not yet started
    env.janitor()
    assert f"lab-{sid}" in env.runtime.containers
    assert f"lab-{sid}" not in env.runtime.removed
    assert env.row(sid)["status"] == "starting"


def test_ending_a_session_while_its_start_is_in_progress_leaves_no_container(env):
    gated = GatedRuntime()
    env.app.state.runtime = gated
    client, _, h = env.student()
    other = env._client()  # a second connection of the same student
    other.cookies.update(client.cookies)
    answers: list[int] = []
    thread = threading.Thread(target=lambda: answers.append(env.start(client, "alpha").status_code))
    thread.start()
    try:
        assert gated.entered.wait(10), "the start never reached the runtime"
        live = env.live_rows(h)
        assert len(live) == 1, live
        sid = str(live[0]["id"])
        assert other.post(f"/api/sessions/{sid}/end").status_code == 204
    finally:
        gated.gate.set()
        thread.join(30)
    assert answers and answers[0] in (409, 410), answers
    assert gated.containers == {}, "the container outlived the ended session"
    assert env.live_rows(h) == []
    assert env.row(sid)["status"] in ("ended", "expired")


class _BrokenPool:
    """Stands in for a database pool that cannot be used: every call raises."""

    def __getattr__(self, name):
        def boom(*args, **kwargs):
            raise ConnectionError("database is down")

        return boom


def test_janitor_removes_an_expired_container_when_the_database_is_unusable(env, monkeypatch):
    """Choice: swap `app.state.pool` for an object whose every call raises, then `run_once(app)`.

    The container is selected by its `lf.expires` label alone; no row exists for it.
    """
    now = int(dt.datetime.now(dt.UTC).timestamp())
    old, fresh = str(uuid.uuid4()), str(uuid.uuid4())
    env.runtime.add_foreign(f"lab-{old}", old, labels={"lf.expires": str(now - 600)})
    env.runtime.add_foreign(f"lab-{fresh}", fresh, labels={"lf.expires": str(now + 3600)})
    monkeypatch.setattr(env.app.state, "pool", _BrokenPool())
    env.janitor()
    assert f"lab-{old}" in env.runtime.removed
    assert f"lab-{old}" not in env.runtime.containers
    assert f"lab-{fresh}" in env.runtime.containers  # not expired: stays without a database


def test_a_failing_janitor_step_does_not_stop_the_pass_or_the_next_pass(env):
    client, _, _ = env.student()
    stale = env.start(client, "alpha").json()["id"]
    env.update(stale, "last_heartbeat = now() - interval '6 minutes'")
    stray = str(uuid.uuid4())
    env.runtime.add_foreign(f"lab-{stray}", stray)
    env.runtime.fail_next_list = True
    env.janitor()  # must not raise: each step has its own error handling
    assert env.row(stale)["end_reason"] == "heartbeat", "a failing step stopped the other steps"
    env.janitor()  # the next pass works again
    assert f"lab-{stray}" not in env.runtime.containers
    assert f"lab-{stray}" in env.runtime.removed


@pytest.fixture
def booted(_db, monkeypatch, tmp_path):
    """The app started (lifespan and first sweep) with a fake runtime that holds a stray."""
    from fastapi.testclient import TestClient

    meminfo = tmp_path / "meminfo"
    _meminfo(meminfo, 16000)
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.setenv("LF_CONTENT_DIR", str(write_content(tmp_path / "content")))
    monkeypatch.setenv("LF_MEMINFO_PATH", str(meminfo))
    monkeypatch.setenv("LF_DOCKER_HOST", f"unix://{tmp_path / 'no-docker.sock'}")
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "0")
    monkeypatch.setenv("LF_JANITOR_INTERVAL_S", "3600")  # only the startup sweep can run
    fake = FakeRuntime()
    stray = str(uuid.uuid4())
    fake.add_foreign(f"lab-{stray}", stray)
    import app.main as main_mod
    import app.runtime as runtime_mod

    monkeypatch.setattr(main_mod, "DockerRuntime", lambda *a, **k: fake, raising=False)
    monkeypatch.setattr(runtime_mod, "DockerRuntime", lambda *a, **k: fake, raising=False)
    with TestClient(main_mod.app, base_url="https://testserver", client=(fresh_ip(), 50000)):
        yield fake, f"lab-{stray}"


def test_startup_sweep_removes_a_stray_lab_container(booted):
    fake, name = booted
    deadline = time.time() + 8
    while name in fake.containers and time.time() < deadline:
        time.sleep(0.1)
    assert name not in fake.containers, "the startup sweep left a stray lab container"
    assert name in fake.removed
