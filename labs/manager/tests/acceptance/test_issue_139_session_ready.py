"""Acceptance tests for issue #139: a session is `running` only after the skel copy; one
ownership rule for the task API and the terminal proxy.

Criterion 1 (Docker, CI under gVisor): `POST /api/sessions` answers `running` only once the lab
container's entrypoint has copied `/etc/skel` into the home tmpfs (ttyd starts after the
copy). Today the status flips when `docker start` returns, so a first-task setup that appends
to `~/.bashrc` as the student can win the race and `cp -Rn` then keeps the setup's file, which
holds the appended line but none of the skel content. Two exams: the skel files are there the
moment the session is `running` (no waiting), and a setup that appends to `~/.bashrc` right
after start leaves both the skel content and the appended line.

Criterion 2 (database, no Docker): for the same sessions (no cookie, bad cookie, another
student's, unknown id, malformed id, ended, disabled student) every task endpoint and the
terminal endpoint answer the same status and the same `detail`. The existing #8 and #9 exams
cover each endpoint on its own; this one catches the two copies drifting apart.

The docker part starts its own throwaway `postgres:16` (`lf-exam-pg-<hex>`) unless
LF_TEST_DATABASE_URL is set, and only touches the `lab-<uuid>` containers of its own sessions.
The database part skips locally when LF_TEST_DATABASE_URL is absent (CI sets it). Student ids
are fake; this module uses its own range, 840000000 (docker) and 850000000 (database), so the
login limiter of other exams (D47) is not inherited.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import os
import stat
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
IMAGE_DIR = REPO_ROOT / "labs" / "images" / "term"
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
PG_PASSWORD = "test-only-" + "pg"  # throwaway, for the exam's own Postgres container
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)

_n = itertools.count(1)

APPENDED = "export LF_EXAM_139_APPENDED=yes"
HOME_FILES = ("/home/student/.bashrc", "/home/student/.profile", "/home/student/.bash_logout")

LAB_YAML = """id: {lab}
title:
  en: Ready lab
  he: מעבדת מוכנות
description:
  en: Fake lab.
  he: מעבדה מזויפת.
image: term
time_limit_min: 15
status: published
module: 1
lesson: 5
tasks:
  - id: 01-rc
    kind: hands-on
    checklist:
      - en: The line is there
        he: השורה קיימת
"""

SETUP_SH = (
    "#!/bin/sh\nset -e\n"
    f"su student -s /bin/sh -c \"echo '{APPENDED}' >> /home/student/.bashrc\"\n"
)

SKEL_PROBE = (
    "n=0; for f in $(ls -A /etc/skel); do n=$((n+1)); "
    'test -e "/home/student/$f" || echo "missing $f"; done; echo "count $n"'
)


def id_hash(raw: str) -> str:
    return hmac.new(TEST_SECRET.encode(), raw.lstrip("0").encode(), hashlib.sha256).hexdigest()


def _run_as(dsn: str, fn):
    async def go():
        import asyncpg  # a missing asyncpg must fail, not skip

        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


def _app_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def write_lab(root: Path, lab: str) -> Path:
    d = root / "test" / lab
    t = d / "tasks" / "01-rc"
    t.mkdir(parents=True)
    (d / "lab.yaml").write_text(LAB_YAML.format(lab=lab), encoding="utf-8")
    (t / "task.en.md").write_text("# Task\n", encoding="utf-8")
    (t / "task.he.md").write_text("# משימה\n", encoding="utf-8")
    (t / "solution.en.md").write_text("n/a\n", encoding="utf-8")
    (t / "solution.he.md").write_text("n/a\n", encoding="utf-8")
    (t / "setup.sh").write_text(SETUP_SH, encoding="utf-8")
    (t / "check.sh").write_text("#!/bin/sh\necho 'PASS 1'\n", encoding="utf-8")
    for name in ("setup.sh", "check.sh"):
        (t / name).chmod((t / name).stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return root


def _reset_schema(admin_dsn: str) -> None:
    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        assert OWNER_SQL.is_file(), f"{OWNER_SQL} is missing"
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)


def _seed_student(admin_dsn: str, raw: str, cohort_name: str) -> str:
    from argon2 import PasswordHasher

    pw = PasswordHasher().hash(COHORT_PASSWORD)

    async def seed(conn):
        cohort = await conn.fetchval(
            f"select id from {SCHEMA}.cohorts where name = $1", cohort_name
        )
        if cohort is None:
            cohort = await conn.fetchval(
                f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                "values ($1, $2, $3) returning id",
                cohort_name, pw, TODAY + 30 * DAY,
            )
        await conn.execute(
            f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, $2, $3)",
            id_hash(raw), f"Fake Student {raw}", cohort,
        )

    _run_as(admin_dsn, seed)
    return id_hash(raw)


# ================================================================ criterion 1: Docker


def docker(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


def _rm(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)


@pytest.fixture(scope="module")
def network():
    name = os.environ.get("LF_LAB_NETWORK", "lf-labs")
    existed = docker("network", "inspect", name, check=False).returncode == 0
    if not existed:
        docker("network", "create", name)
    yield name
    if not existed:
        docker("network", "rm", name, check=False)


@pytest.fixture(scope="module")
def image() -> str:
    env = os.environ.get("LF_LAB_TERM_IMAGE")
    if env:
        return env
    assert (IMAGE_DIR / "Dockerfile").is_file(), "labs/images/term/Dockerfile is missing"
    tag = "lf-lab-term:exam"
    docker("build", "-t", tag, str(IMAGE_DIR), timeout=1500)
    return tag


@pytest.fixture(scope="module")
def docker_admin_dsn():
    given = os.environ.get("LF_TEST_DATABASE_URL")
    if given:
        host = urlsplit(given).hostname
        assert host in ("localhost", "127.0.0.1", "::1"), "LF_TEST_DATABASE_URL must be local"
        yield given
        return
    name = f"lf-exam-pg-{uuid.uuid4().hex[:10]}"
    docker(
        "run", "-d", "--name", name, "-e", f"POSTGRES_PASSWORD={PG_PASSWORD}",
        "-p", "127.0.0.1::5432", "postgres:16", timeout=900,
    )
    try:
        out = docker("port", name, "5432/tcp").stdout.split()[0]
        port = int(out.rsplit(":", 1)[1])
        dsn = f"postgresql://postgres:{PG_PASSWORD}@127.0.0.1:{port}/postgres"

        async def ping(conn):
            return await conn.fetchval("select 1")

        deadline = time.time() + 90
        while True:
            try:
                _run_as(dsn, ping)
                break
            except Exception:  # noqa: BLE001 - the server is still starting
                if time.time() > deadline:
                    raise
                time.sleep(1)
        yield dsn
    finally:
        _rm(name)


class Live:
    """A running manager app with the real DockerRuntime; every session it opens is removed."""

    def __init__(self, client, admin_dsn: str, lab: str):
        self.client, self.admin_dsn, self.lab = client, admin_dsn, lab
        self.names: list[str] = []

    def fresh_student(self):
        from fastapi.testclient import TestClient

        raw = str(840000000 + next(_n))  # own id range (D47)
        _seed_student(self.admin_dsn, raw, "Fake cohort docker")
        c = TestClient(self.client.app, base_url="https://testserver", client=("10.139.0.1", 50000))
        c.portal = self.client.portal  # the app's pool belongs to the lifespan client's loop
        resp = c.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return c

    def open(self, c) -> dict:
        resp = c.post("/api/sessions", json={"lab_id": self.lab})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        self.names.append(f"lab-{body['id']}")
        return body

    def exec(self, name: str, user: str, script: str) -> subprocess.CompletedProcess:
        return docker("exec", "-u", user, name, "sh", "-c", script, check=False, timeout=90)


@pytest.fixture(scope="module")
def live(docker_admin_dsn, network, image, tmp_path_factory):
    from fastapi.testclient import TestClient

    lab = "readylab"
    root = write_lab(tmp_path_factory.mktemp("ready") / "content", lab)
    _reset_schema(docker_admin_dsn)
    mp = pytest.MonkeyPatch()
    mp.setenv("LF_DATABASE_URL", _app_dsn(docker_admin_dsn))
    mp.setenv("LF_ID_SECRET", TEST_SECRET)
    mp.setenv("LF_CONTENT_DIR", str(root))
    mp.setenv("LF_LAB_TERM_IMAGE", image)
    mp.setenv("LF_LAB_NETWORK", network)
    mp.setenv("LF_MEM_RESERVE_MB", "0")
    mp.setenv("LF_DISK_RESERVE_GB", "0")
    mp.setenv("LF_JANITOR_INTERVAL_S", "3600")
    for var in ("LF_DOCKER_HOST", "LF_TRUSTED_PROXY", "LF_MEMINFO_PATH", "LF_SETUP_TIMEOUT_S"):
        mp.delenv(var, raising=False)
    from app.main import app

    lv = None
    try:
        with TestClient(app, base_url="https://testserver", client=("10.139.0.2", 50000)) as c:
            lv = Live(c, docker_admin_dsn, lab)
            yield lv
    finally:
        if lv is not None:
            for name in lv.names:
                _rm(name)
        mp.undo()


@pytest.mark.docker
def test_when_the_session_is_running_the_skel_files_are_already_in_the_home(live):
    for _ in range(3):  # the race is tight; several fresh sessions make a miss unlikely
        c = live.fresh_student()
        body = live.open(c)
        assert body["status"] == "running", body
        name = f"lab-{body['id']}"
        # No waiting: the response said `running`, so the entrypoint's copy is done.
        probe = live.exec(name, "student", SKEL_PROBE)
        assert probe.returncode == 0, probe.stderr
        lines = [ln for ln in probe.stdout.splitlines() if ln.strip()]
        assert not [ln for ln in lines if ln.startswith("missing")], probe.stdout
        assert int(lines[-1].split()[1]) >= 3, "the image has no skel files"
        c.post(f"/api/sessions/{body['id']}/end")


@pytest.mark.docker
def test_a_setup_that_appends_to_bashrc_right_after_start_leaves_skel_and_the_appended_line(live):
    for _ in range(3):
        c = live.fresh_student()
        body = live.open(c)
        sid, name = body["id"], f"lab-{body['id']}"
        shown = c.post(f"/api/sessions/{sid}/tasks/01-rc/show")  # immediately after start
        assert shown.status_code == 200, shown.text

        rc = live.exec(name, "student", "cat /home/student/.bashrc").stdout
        skel = live.exec(name, "student", "cat /etc/skel/.bashrc").stdout
        assert skel.strip(), "the image has no /etc/skel/.bashrc"
        assert rc.startswith(skel), "the skel .bashrc content is missing (setup won the race)"
        assert rc.rstrip("\n").endswith(APPENDED), "the appended line is missing"
        assert rc.count(APPENDED) == 1
        for f in HOME_FILES:
            assert live.exec(name, "student", f"test -f {f}").returncode == 0, f
        c.post(f"/api/sessions/{sid}/end")


# ================================================================ criterion 2: database


def _db_dsn() -> str:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set (no Postgres available)")
    host = urlsplit(dsn).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail(f"LF_TEST_DATABASE_URL must point at a local throwaway Postgres, not {host!r}")
    return dsn


class FakeRuntime:
    """Starts and removes nothing for real; every exec succeeds."""

    async def start(self, spec):
        return str(spec["name"])

    async def remove(self, name):
        return None

    async def list_lab_containers(self):
        return []

    async def exec_script(self, name, script, timeout=None):
        from app.runtime import ExecResult

        return ExecResult(0, "ok\n", False)


class Env:
    def __init__(self, admin_dsn: str, client):
        self.admin_dsn, self.client = admin_dsn, client

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def new_client(self):
        from fastapi.testclient import TestClient

        n = next(_n)
        c = TestClient(
            self.client.app, base_url="https://testserver",
            client=(f"10.139.{n // 250 + 10}.{n % 250 + 1}", 50000),
        )
        c.portal = self.client.portal
        return c

    def student(self):
        raw = str(850000000 + next(_n))  # own id range (D47)
        h = _seed_student(self.admin_dsn, raw, "Fake cohort parity")
        c = self.new_client()
        resp = c.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return c, h

    def session(self, c) -> str:
        resp = c.post("/api/sessions", json={"lab_id": "readylab"})
        assert resp.status_code in (200, 201), resp.text
        return resp.json()["id"]


@pytest.fixture(scope="module")
def _db():
    dsn = _db_dsn()
    _reset_schema(dsn)
    return dsn


@pytest.fixture
def env(_db, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 32768000 kB\nMemFree: 8192000 kB\nMemAvailable: 16384000 kB\nSwapTotal: 0 kB\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.setenv("LF_CONTENT_DIR", str(write_lab(tmp_path / "content", "readylab")))
    monkeypatch.setenv("LF_MEMINFO_PATH", str(meminfo))
    monkeypatch.setenv("LF_DOCKER_HOST", f"unix://{tmp_path / 'no-docker.sock'}")
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "0")
    monkeypatch.setenv("LF_JANITOR_INTERVAL_S", "3600")
    monkeypatch.setenv("LF_MAX_TERM", "1000")
    # A refusal must never reach the lab; nothing listens here, so a leak would be a 502.
    monkeypatch.setenv("LF_TERM_UPSTREAM", "127.0.0.1:1")
    for var in (
        "LF_TRUSTED_PROXY", "LF_MAX_CODE", "LF_MEM_RESERVE_MB", "LF_HEARTBEAT_TIMEOUT_S",
        "LF_DISK_PATH", "LF_SETUP_TIMEOUT_S",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.main import app

    async def end_all(conn):
        await conn.execute(
            f"update {SCHEMA}.lab_sessions set status = 'ended', ended_at = now(), "
            "end_reason = 'orphan' where status in ('starting', 'running')"
        )

    with TestClient(app, base_url="https://testserver", client=("10.139.9.9", 50000)) as c:
        app.state.runtime = FakeRuntime()
        e = Env(_db, c)
        e.sql(end_all)
        yield e
        e.sql(end_all)


def probes(sid: str):
    """Every task endpoint plus the terminal endpoint, for one session id."""
    base = f"/api/sessions/{sid}/tasks"
    return [
        ("task list", "get", base),
        ("current task", "get", f"{base}/current"),
        ("show", "post", f"{base}/01-rc/show"),
        ("next", "post", f"{base}/current/next"),
        ("terminal", "get", f"/s/{sid}/term/"),
    ]


def answers(client, sid: str, headers: dict | None = None) -> dict[str, tuple[int, object]]:
    out = {}
    for label, method, url in probes(sid):
        resp = getattr(client, method)(url, headers=headers or {})
        try:
            detail = resp.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        out[label] = (resp.status_code, detail)
    return out


def assert_same(got: dict[str, tuple[int, object]], status: int, detail: object | None) -> None:
    for label, (code, d) in got.items():
        assert code == status, (label, got)
        if detail is not None:
            assert d == detail, (label, got)
    assert len({tuple(map(str, v)) for v in got.values()}) == 1, got


def test_another_students_session_is_403_not_your_session_on_every_endpoint(env):
    owner, _ = env.student()
    sid = env.session(owner)
    intruder, _ = env.student()
    assert_same(answers(intruder, sid), 403, "not_your_session")


def test_an_unknown_session_id_is_404_no_session_on_every_endpoint(env):
    client, _ = env.student()
    assert_same(answers(client, str(uuid.uuid4())), 404, "no_session")


def test_a_malformed_session_id_is_404_no_session_on_every_endpoint(env):
    client, _ = env.student()
    for bad in ("not-a-uuid", "123"):
        assert_same(answers(client, bad), 404, "no_session")


def test_an_ended_session_is_410_session_ended_on_every_endpoint(env):
    client, _ = env.student()
    sid = env.session(client)
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    assert_same(answers(client, sid), 410, "session_ended")


def test_an_ended_session_of_another_student_is_still_403_on_every_endpoint(env):
    owner, _ = env.student()
    sid = env.session(owner)
    assert owner.post(f"/api/sessions/{sid}/end").status_code == 204
    intruder, _ = env.student()
    assert_same(answers(intruder, sid), 403, "not_your_session")


def test_no_cookie_is_401_on_every_endpoint(env):
    owner, _ = env.student()
    sid = env.session(owner)
    assert_same(answers(env.new_client(), sid), 401, None)


def test_an_invalid_cookie_is_401_on_every_endpoint(env):
    owner, _ = env.student()
    sid = env.session(owner)
    got = answers(env.new_client(), sid, headers={"cookie": "lf_session=not-a-real-token"})
    assert_same(got, 401, None)


def test_a_disabled_student_is_401_on_every_endpoint(env):
    client, h = env.student()
    sid = env.session(client)

    async def disable(conn):
        await conn.execute(f"update {SCHEMA}.students set disabled = true where id_hash = $1", h)

    env.sql(disable)
    assert_same(answers(client, sid), 401, None)


def test_the_owner_of_a_live_session_passes_the_ownership_check_on_every_endpoint(env):
    client, _ = env.student()
    sid = env.session(client)
    got = answers(client, sid)
    # The task endpoints answer 200 or a task-level 409; the terminal reaches (and fails to
    # reach) its upstream: 502/503/504. None of them is an ownership refusal.
    for label, (code, _detail) in got.items():
        assert code not in (401, 403, 404, 410), (label, got)
