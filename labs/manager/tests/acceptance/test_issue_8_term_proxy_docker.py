"""Acceptance tests for issue #8, Docker part: the terminal proxy in front of a real ttyd (CI
`docker` job, runsc).

The whole path is real except the network address: the manager app runs in-process, a student
logs in, `POST /api/sessions` starts a real `lf-lab-term` container under runsc through
`DockerRuntime`, and the proxy talks to that container's ttyd. The test runner is not on the
lab network and cannot resolve `lab-<uuid>`, so the exam uses the seam documented in
test_issue_8_term_proxy.py: `LF_TERM_UPSTREAM=<container ip>:7681`, read per request.

ttyd 1.7.x protocol (see labs/images/term/Dockerfile for the version): WebSocket path
`<base>ws`, subprotocol `tty`; the client's first message is JSON
`{"AuthToken": "", "columns": N, "rows": M}`; later client messages are prefixed `0` (input),
`1` (resize); server messages are prefixed `0` (output), `1` (window title), `2` (prefs).

The docker job has no Postgres service, so this module starts its own throwaway
`postgres:16` container named `lf-exam-pg-<hex>` (published on 127.0.0.1 only) unless
LF_TEST_DATABASE_URL is set, and removes it afterwards. The image is $LF_LAB_TERM_IMAGE if
set, else built from labs/images/term. The lab network ($LF_LAB_NETWORK, default lf-labs) is
created if missing and removed again only if this module created it. Only containers this
module started (`lab-<uuid>` of its own session, `lf-exam-pg-*`) are ever touched.
Student ids are fake.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import lab_image
import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
PG_PASSWORD = "test-only-" + "pg"  # throwaway, for the exam's own Postgres container
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)
TTYD_PORT = 7681
LOG = "/var/log/lf/terminal.log"

_n = itertools.count(1)

LAB_YAML = """id: dockproxy
title:
  en: Docker proxy lab
  he: מעבדת פרוקסי
description:
  en: Fake lab.
  he: מעבדה מזויפת.
image: term
time_limit_min: 15
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
"""


def docker(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


def _rm(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)


def id_hash(raw: str) -> str:
    return hmac.new(TEST_SECRET.encode(), raw.lstrip("0").encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- docker and database


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
    return lab_image.ensure_lab_image()


def _run_as(dsn: str, fn):
    async def go():
        import asyncpg  # a missing asyncpg must fail, not skip

        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


@pytest.fixture(scope="module")
def admin_dsn():
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


def _app_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class Lab:
    """A started session: client of the owner, session id, container name and address."""

    def __init__(self, client, other, sid: str, name: str, ip: str):
        self.client, self.other, self.sid, self.name, self.ip = client, other, sid, name, ip

    def exec(self, user: str, script: str, timeout: int = 90) -> subprocess.CompletedProcess:
        return docker(
            "exec", "-u", user, self.name, "sh", "-c", script, check=False, timeout=timeout
        )

    def cookie(self) -> dict[str, str]:
        return {"cookie": f"lf_session={self.client.cookies.get('lf_session')}"}


@pytest.fixture(scope="module")
def lab(admin_dsn, network, image, tmp_path_factory, request):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    tmp = tmp_path_factory.mktemp("proxy-docker")
    content = tmp / "content" / "test" / "dockproxy"
    content.mkdir(parents=True)
    (content / "lab.yaml").write_text(LAB_YAML, encoding="utf-8")

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        assert OWNER_SQL.is_file(), f"{OWNER_SQL} is missing"
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)

    mp = pytest.MonkeyPatch()
    mp.setenv("LF_DATABASE_URL", _app_dsn(admin_dsn))
    mp.setenv("LF_ID_SECRET", TEST_SECRET)
    mp.setenv("LF_CONTENT_DIR", str(tmp / "content"))
    mp.setenv("LF_LAB_TERM_IMAGE", image)
    mp.setenv("LF_LAB_NETWORK", network)
    mp.setenv("LF_MEM_RESERVE_MB", "0")
    mp.setenv("LF_DISK_RESERVE_GB", "0")
    mp.setenv("LF_JANITOR_INTERVAL_S", "3600")
    for var in ("LF_DOCKER_HOST", "LF_TRUSTED_PROXY", "LF_TERM_UPSTREAM", "LF_MEMINFO_PATH"):
        mp.delenv(var, raising=False)
    from app.main import app

    pw = PasswordHasher().hash(COHORT_PASSWORD)
    cohort: list[int] = []

    def student(c):
        raw = str(600000000 + next(_n))

        async def seed(conn):
            if not cohort:
                cohort.append(
                    await conn.fetchval(
                        f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                        "values ($1, $2, $3) returning id",
                        "Fake cohort", pw, TODAY + 30 * DAY,
                    )
                )
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, $2, $3)",
                id_hash(raw), f"Fake Student {raw}", cohort[0],
            )

        _run_as(admin_dsn, seed)
        resp = c.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text

    sid = ""
    try:
        with TestClient(app, base_url="https://testserver", client=("10.99.0.1", 50000)) as owner:
            other = TestClient(app, base_url="https://testserver", client=("10.99.0.2", 50000))
            other.portal = owner.portal
            student(owner)
            student(other)
            resp = owner.post("/api/sessions", json={"lab_id": "dockproxy"})
            assert resp.status_code == 201, resp.text
            sid = resp.json()["id"]
            name = f"lab-{sid}"
            request.addfinalizer(lambda: _rm(name))
            info = json.loads(docker("inspect", name).stdout)[0]
            ip = info["NetworkSettings"]["Networks"][network]["IPAddress"]
            assert ip, "the lab container has no address on the lab network"
            mp.setenv("LF_TERM_UPSTREAM", f"{ip}:{TTYD_PORT}")
            yield Lab(owner, other, sid, name, ip)
            owner.post(f"/api/sessions/{sid}/end")
    finally:
        if sid:
            _rm(f"lab-{sid}")
        mp.undo()


@pytest.fixture(scope="module")
def ttyd_up(lab):
    """Wait until ttyd answers through the proxy."""
    deadline = time.time() + 90
    last = "no attempt"
    while time.time() < deadline:
        resp = lab.client.get(f"/s/{lab.sid}/term/")
        if resp.status_code == 200:
            return resp
        last = f"{resp.status_code} {resp.text[:100]}"
        time.sleep(1)
    raise AssertionError(f"ttyd never answered through the proxy: {last}")


# ---------------------------------------------------------------- websocket helper


class Pump:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                msg = self.ws.receive()
            except Exception as exc:  # noqa: BLE001
                self.q.put({"type": "websocket.close", "code": 1006, "error": repr(exc)})
                return
            self.q.put(msg)
            if msg["type"] == "websocket.close":
                return

    def get(self, timeout: float) -> dict:
        try:
            return self.q.get(timeout=max(timeout, 0.1))
        except queue.Empty:
            raise AssertionError("no websocket message in time") from None


def _payload(msg: dict) -> bytes:
    if msg.get("bytes") is not None:
        return msg["bytes"]
    return (msg.get("text") or "").encode()


def read_until(pump: Pump, pattern: bytes, wait: float) -> bytes:
    """Accumulate ttyd output frames (prefix `0`) until `pattern` (a regex) matches."""
    out = b""
    deadline = time.time() + wait
    while time.time() < deadline:
        msg = pump.get(deadline - time.time())
        assert msg["type"] != "websocket.close", f"closed early, output so far: {out!r}"
        data = _payload(msg)
        if data[:1] == b"0":
            out += data[1:]
            if re.search(pattern, out):
                return out
    raise AssertionError(f"pattern {pattern!r} not seen in {wait} s; output: {out!r}")


# A line that is just `hi`. Bash ends bracketed paste mode (`ESC[?2004l`) and returns the
# cursor (`\r`) after the newline of the typed command, before the command's output.
HI_LINE = rb"\n(?:\x1b\[[0-9;?]*[A-Za-z]|\r)*hi\r?\n"


@pytest.fixture(scope="module")
def typed(lab, ttyd_up):
    """Open the terminal through the proxy, run `echo hi`, return the output ttyd sent."""
    with lab.client.websocket_connect(
        f"/s/{lab.sid}/term/ws", subprotocols=["tty"], headers=lab.cookie()
    ) as ws:
        assert ws.accepted_subprotocol == "tty"
        pump = Pump(ws)
        ws.send_bytes(json.dumps({"AuthToken": "", "columns": 80, "rows": 24}).encode())
        # Type only once the student's prompt is there, as a person would: under runsc, input
        # sent before `su - student` has started its shell is dropped, not buffered.
        read_until(pump, rb"[$] $", 90)
        ws.send_bytes(b"0echo hi\r")
        # the echoed command line is `echo hi`; the command's own output is a line `hi`
        return read_until(pump, HI_LINE, 90)


# ---------------------------------------------------------------- tests


def test_http_through_the_proxy_serves_the_real_ttyd_page(lab, ttyd_up):
    assert ttyd_up.status_code == 200
    assert "text/html" in ttyd_up.headers["content-type"]
    assert "<html" in ttyd_up.text.lower()


def test_http_through_the_proxy_reaches_ttyd_under_the_session_base_path(lab, ttyd_up):
    resp = lab.client.get(f"/s/{lab.sid}/term/token")
    assert resp.status_code == 200, resp.text
    assert "token" in resp.json()


def test_websocket_through_the_proxy_runs_echo_hi_and_reads_hi_back(typed):
    assert re.search(HI_LINE, typed), typed


def test_the_command_log_exists_in_the_container_after_a_session(lab, typed):
    assert lab.exec("root", f"test -f {LOG}").returncode == 0, f"{LOG} was never created"
    deadline = time.time() + 20
    content = ""
    while time.time() < deadline:  # script -f flushes on every write; allow for a slow runsc
        content = lab.exec("root", f"cat {LOG}").stdout
        if "echo hi" in content:
            break
        time.sleep(0.5)
    assert "echo hi" in content, "the typed command is not in the command log"


def test_the_student_cannot_read_the_command_log(lab, typed):
    assert lab.exec("root", f"stat -c %U {LOG}").stdout.strip() == "root"
    assert lab.exec("root", "stat -c %a /var/log/lf").stdout.strip() == "700"
    assert lab.exec("student", f"cat {LOG}").returncode != 0
    assert lab.exec("student", f"test -r {LOG}").returncode != 0
    assert lab.exec("student", "ls /var/log/lf").returncode != 0


def test_another_student_cannot_reach_the_real_terminal(lab, ttyd_up):
    assert lab.other.get(f"/s/{lab.sid}/term/").status_code == 403
    assert lab.other.get(f"/s/{lab.sid}/term/token").status_code == 403


def test_no_cookie_cannot_reach_the_real_terminal(lab, ttyd_up):
    from fastapi.testclient import TestClient

    anon = TestClient(lab.client.app, base_url="https://testserver", client=("10.99.0.3", 50000))
    anon.portal = lab.client.portal
    assert anon.get(f"/s/{lab.sid}/term/").status_code == 401


def test_zz_an_ended_session_is_410_and_its_container_is_gone(lab, typed):
    resp = lab.client.post(f"/api/sessions/{lab.sid}/end")
    assert resp.status_code == 204
    assert lab.client.get(f"/s/{lab.sid}/term/").status_code == 410
    assert docker("inspect", lab.name, check=False).returncode != 0
