"""Acceptance tests for issue #88, Docker part: a real check.sh in a real lab container (CI).

The whole path is real: the manager app runs in-process, a student logs in, `POST
/api/sessions` starts a real `lf-lab-term` container under runsc through `DockerRuntime`, the
task is shown (its setup.sh runs in the container) and the check route streams the task's
check.sh in through `docker exec` as root. It proves the contract of spec section 7 on the
real thing: PASS/FAIL lines are read from the real output, the script runs as root, it is
streamed and left nothing behind (D45, spec section 5), and a check that hangs comes back
after the 20 s limit as a failed check with reason "timeout" while the container survives.

The docker job has no Postgres service, so this module starts its own throwaway `postgres:16`
container named `lf-exam-pg-<hex>` (published on 127.0.0.1 only) unless LF_TEST_DATABASE_URL
is set, and removes it afterwards. The image is $LF_LAB_TERM_IMAGE if set, else built from
labs/images/term. The lab network ($LF_LAB_NETWORK, default lf-labs) is created if missing and
removed again only if this module created it. Only containers this module started
(`lab-<uuid>` of its own session, `lf-exam-pg-*`) are ever touched. Student ids are fake.
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

_n = itertools.count(1)
LAB = "dockcheck"
MARKER = "CHECK-STREAMED-ONLY-" + uuid.uuid4().hex
TARGET = "/home/student/report.txt"

LAB_YAML = """id: dockcheck
title:
  en: Docker check lab
  he: מעבדת בדיקה
description:
  en: Fake lab.
  he: מעבדה מזויפת.
image: term
time_limit_min: 15
status: published
module: 1
lesson: 5
tasks:
  - id: 01-file
    kind: hands-on
    checklist:
      - en: The report file exists
        he: קובץ הדו"ח קיים
      - en: It says done
        he: כתוב בו done
      - en: The check runs as root
        he: הבדיקה רצה כשורש
  - id: 02-hang
    kind: hands-on
    checklist:
      - en: Never finishes
        he: אף פעם לא נגמר
  - id: 03-noisy
    kind: hands-on
    checklist:
      - en: Prints PASS but exits 1
        he: מדפיס PASS ויוצא עם 1
"""

SCRIPTS = {
    "01-file": (
        f"#!/bin/sh\n# {MARKER}\n"
        f"if [ -f {TARGET} ]; then echo 'PASS 1'; else echo 'FAIL 1 create {TARGET}'; fi\n"
        f"if grep -qx done {TARGET} 2>/dev/null; then echo 'PASS 2'; "
        "else echo 'FAIL 2 the file must contain the word done'; fi\n"
        "if [ \"$(id -u)\" = 0 ]; then echo 'PASS 3'; else echo 'FAIL 3 not root'; fi\n"
        "grep -qx done " + TARGET + " 2>/dev/null\n"
    ),
    "02-hang": "#!/bin/sh\nsleep 120\necho 'PASS 1'\n",
    "03-noisy": "#!/bin/sh\necho 'PASS 1'\nexit 1\n",
}


def docker(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


def _rm(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)


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


class Lab:
    def __init__(self, client, sid: str, name: str, admin_dsn: str):
        self.client, self.sid, self.name, self.admin_dsn = client, sid, name, admin_dsn

    def exec(self, user: str, script: str, timeout: int = 90) -> subprocess.CompletedProcess:
        return docker(
            "exec", "-u", user, self.name, "sh", "-c", script, check=False, timeout=timeout
        )

    def show(self, tid: str) -> None:
        resp = self.client.post(f"/api/sessions/{self.sid}/tasks/{tid}/show")
        assert resp.status_code == 200, resp.text

    def check(self, tid: str):
        return self.client.post(f"/api/sessions/{self.sid}/tasks/{tid}/check")

    def row(self, tid: str) -> dict:
        async def go(conn):
            r = await conn.fetchrow(
                f"select * from {SCHEMA}.task_results where session_id = $1 and task_id = $2",
                uuid.UUID(self.sid), tid,
            )
            return dict(r) if r else {}

        return _run_as(self.admin_dsn, go)


@pytest.fixture(scope="module")
def lab(admin_dsn, network, image, tmp_path_factory, request):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    tmp = tmp_path_factory.mktemp("check-docker")
    lab_dir = tmp / "content" / "test" / LAB
    lab_dir.mkdir(parents=True)
    (lab_dir / "lab.yaml").write_text(LAB_YAML, encoding="utf-8")
    for tid, check_sh in SCRIPTS.items():
        d = lab_dir / "tasks" / tid
        d.mkdir(parents=True)
        (d / "task.en.md").write_text(f"# Task {tid}\n", encoding="utf-8")
        (d / "task.he.md").write_text(f"# משימה {tid}\n", encoding="utf-8")
        (d / "solution.en.md").write_text("n/a\n", encoding="utf-8")
        (d / "solution.he.md").write_text("n/a\n", encoding="utf-8")
        (d / "setup.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (d / "check.sh").write_text(check_sh, encoding="utf-8")
        for name in ("setup.sh", "check.sh"):
            (d / name).chmod((d / name).stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

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
    for var in ("LF_DOCKER_HOST", "LF_TRUSTED_PROXY", "LF_MEMINFO_PATH", "LF_CHECK_TIMEOUT_S"):
        mp.delenv(var, raising=False)
    from app.main import app

    raw = str(890000000 + next(_n))  # own id range (D47)
    pw = PasswordHasher().hash(COHORT_PASSWORD)

    async def seed(conn):
        cohort = await conn.fetchval(
            f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
            "values ($1, $2, $3) returning id",
            "Fake cohort", pw, TODAY + 30 * DAY,
        )
        await conn.execute(
            f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, $2, $3)",
            id_hash(raw), f"Fake Student {raw}", cohort,
        )

    sid = ""
    try:
        with TestClient(app, base_url="https://testserver", client=("10.189.0.1", 50000)) as c:
            _run_as(admin_dsn, seed)  # after startup: the app's migrations create the tables
            resp = c.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
            assert resp.status_code == 200, resp.text
            resp = c.post("/api/sessions", json={"lab_id": LAB})
            assert resp.status_code == 201, resp.text
            sid = resp.json()["id"]
            name = f"lab-{sid}"
            request.addfinalizer(lambda: _rm(name))
            deadline = time.time() + 60
            while time.time() < deadline:  # the entrypoint copies /etc/skel into the home
                probe = docker("exec", name, "test", "-d", "/home/student", check=False)
                if probe.returncode == 0:
                    break
                time.sleep(0.5)
            yield Lab(c, sid, name, admin_dsn)
            c.post(f"/api/sessions/{sid}/end")
    finally:
        if sid:
            _rm(f"lab-{sid}")
        mp.undo()


# ---------------------------------------------------------------- a real check.sh


def test_a_real_check_reads_pass_and_fail_lines_and_flips_when_the_student_fixes_it(lab):
    lab.show("01-file")
    first = lab.check("01-file")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["passed"] is False and body["attempts"] == 1 and body["reason"] is None
    items = {i["n"]: i for i in body["checklist"]}
    assert items[1]["passed"] is False and TARGET in items[1]["hint"]
    assert items[2]["passed"] is False and "done" in items[2]["hint"]
    assert items[3]["passed"] is True  # the check runs as root
    assert items[1]["text"] == {"en": "The report file exists", "he": 'קובץ הדו"ח קיים'}
    assert lab.row("01-file")["passed_at"] is None

    made = lab.exec("student", f"echo done > {TARGET}")
    assert made.returncode == 0, made.stderr
    second = lab.check("01-file").json()
    assert second["passed"] is True and second["attempts"] == 2
    assert all(i["passed"] and i["hint"] is None for i in second["checklist"])
    row = lab.row("01-file")
    assert row["attempts"] == 2 and row["passed_at"] is not None
    first_pass = row["passed_at"]

    lab.exec("student", f"rm -f {TARGET}")
    third = lab.check("01-file").json()
    assert third["passed"] is False and third["attempts"] == 3
    row = lab.row("01-file")
    assert row["attempts"] == 3 and row["passed_at"] == first_pass


def test_the_check_script_was_streamed_and_left_nothing_in_the_container(lab):
    lab.show("01-file")
    assert lab.check("01-file").status_code == 200
    found = lab.exec(
        "root",
        "grep -rIl --exclude-dir=proc --exclude-dir=sys --exclude-dir=dev "
        f"-e {MARKER} / 2>/dev/null; true",
    )
    assert found.stdout.strip() == "", f"check.sh is on the container's disk: {found.stdout}"


def test_the_response_never_carries_the_check_script(lab):
    lab.show("01-file")
    resp = lab.check("01-file")
    assert MARKER not in resp.text and "check.sh" not in resp.text


def test_exit_non_zero_with_a_pass_line_is_not_passed_on_the_real_container(lab):
    lab.show("03-noisy")
    body = lab.check("03-noisy").json()
    assert body["passed"] is False and body["reason"] is None
    assert lab.row("03-noisy")["passed_at"] is None


def test_a_check_that_hangs_comes_back_failed_with_reason_timeout_and_the_container_lives(lab):
    lab.show("02-hang")
    started = time.monotonic()
    resp = lab.check("02-hang")
    elapsed = time.monotonic() - started
    assert resp.status_code == 200, resp.text
    assert 15 <= elapsed < 40, f"the 20 s limit was not honoured: {elapsed:.1f}s"
    body = resp.json()
    assert body["passed"] is False and body["reason"] == "timeout"
    assert [i["passed"] for i in body["checklist"]] == [False]
    assert body["attempts"] == 1
    assert lab.row("02-hang")["passed_at"] is None
    alive = lab.exec("student", "echo still-alive")
    assert alive.returncode == 0 and "still-alive" in alive.stdout
