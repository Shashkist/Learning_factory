"""Acceptance tests for issue #128, part 1: the smoke-only lab (D54, D36).

Content contract fixed by this exam (the implementer must follow it):
  * The lab lives under `labs/content/smoke/` (D54 says `labs/content/smoke/tasks/`). Its
    `lab.yaml` has id `smoke-tasks`, `status: internal`, image `term`, internet false, sudo false.
    NOTE for the manager: the loader requires id == lab directory name (test_issue_5). D54's path
    `smoke/tasks/` with id `smoke-tasks` therefore cannot validate as is. This exam finds the lab
    by glob (`labs/content/smoke/**/lab.yaml`) and does not pin the directory name: put it at
    `smoke/smoke-tasks/` (no loader change) or relax the rule; either passes.
  * Tasks, in this order: `01-quiz` (quiz, three options, `correct: 1`) and `02-touch-file`
    (hands-on, exactly one checklist item).
  * `02-touch-file`: `setup.sh` leaves the student's home without `~/smoke-ok`; `check.sh` prints
    `PASS 1` when `/home/student/smoke-ok` exists, otherwise `FAIL 1 <hint>` where the hint
    contains the word `smoke-ok`; exit 0 only on pass. The change the student makes is
    `touch ~/smoke-ok`. Both scripts are deterministic: no network, no clock, no randomness.
  * `lab.yaml` accepts `status: internal` (STATUSES = draft | published | internal).
  * `POST /api/sessions` for an internal lab answers 404 `{"detail": "unknown_lab"}` (byte for
    byte what a missing lab answers) unless the student's cohort is named exactly `lf-smoke`.

DB tests need a throwaway Postgres in LF_TEST_DATABASE_URL (skip locally, CI sets it). Docker
tests are marked `docker`. Student ids and passwords are synthetic.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import yaml

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CONTENT_ROOT = REPO_ROOT / "labs" / "content"
SMOKE_ROOT = CONTENT_ROOT / "smoke"
IMAGE_DIR = REPO_ROOT / "labs" / "images" / "term"
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
LAB_ID = "smoke-tasks"
QUIZ, HANDS_ON = "01-quiz", "02-touch-file"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway
TEST_SECRET = "test-only-id-" + "secret"  # throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)
_n = itertools.count(1)


def lab_yaml_path() -> Path:
    found = sorted(SMOKE_ROOT.glob("**/lab.yaml")) if SMOKE_ROOT.is_dir() else []
    assert found, "labs/content/smoke/**/lab.yaml is missing (the smoke lab, D54)"
    assert len(found) == 1, f"expected exactly one smoke lab, found {found}"
    return found[0]


def lab_data() -> dict:
    return yaml.safe_load(lab_yaml_path().read_text(encoding="utf-8"))


def task_dir(tid: str) -> Path:
    return lab_yaml_path().parent / "tasks" / tid


def text(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(REPO_ROOT)} is missing"
    return path.read_bytes().decode("utf-8")


# ---------------------------------------------------------------- non-docker: content shape


def test_the_smoke_lab_exists_with_id_smoke_tasks_and_status_internal():
    data = lab_data()
    assert data["id"] == LAB_ID
    assert data["status"] == "internal"
    assert data["image"] == "term"
    assert data.get("internet", False) is False
    assert data.get("sudo", False) is False


def test_the_smoke_lab_has_exactly_one_quiz_then_one_hands_on_task():
    tasks = lab_data()["tasks"]
    assert [(t["id"], t["kind"]) for t in tasks] == [(QUIZ, "quiz"), (HANDS_ON, "hands-on")]


def test_the_quiz_has_three_options_and_the_correct_index_is_1():
    quiz = lab_data()["tasks"][0]
    assert len(quiz["options"]) == 3
    assert quiz["correct"] == 1
    assert quiz["question"]["en"] and quiz["question"]["he"]


def test_the_hands_on_task_has_one_checklist_item():
    task = lab_data()["tasks"][1]
    assert len(task["checklist"]) == 1


def test_the_content_loader_accepts_status_internal_and_the_smoke_lab_validates():
    from app.content import load_labs

    result = load_labs(CONTENT_ROOT)
    smoke_errors = {k: v for k, v in result.errors.items() if k.startswith("smoke")}
    assert not smoke_errors, smoke_errors
    assert LAB_ID in result.labs, sorted(result.labs)
    lab = result.labs[LAB_ID]
    assert lab.status == "internal"
    assert [(t.id, t.kind) for t in lab.tasks] == [(QUIZ, "quiz"), (HANDS_ON, "hands-on")]


def test_other_status_values_are_still_rejected(tmp_path):
    from app.content import load_labs

    root = tmp_path / "content"
    shutil.copytree(lab_yaml_path().parent, root / "smoke" / LAB_ID)
    path = root / "smoke" / LAB_ID / "lab.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["status"] = "secret"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    result = load_labs(root)
    assert LAB_ID not in result.labs
    assert any("status" in e for errs in result.errors.values() for e in errs)


def test_the_hands_on_task_has_the_validator_files_and_scripts_are_executable_lf_shell():
    d = task_dir(HANDS_ON)
    for name in ("task.en.md", "task.he.md", "solution.en.md", "solution.he.md"):
        assert text(d / name).strip()
    for name in ("setup.sh", "check.sh"):
        body = text(d / name)
        assert "\r" not in body, f"{name} has CRLF endings"
        assert re.match(r"#!(/bin/(ba)?sh|/usr/bin/env (ba)?sh)\n", body), f"{name}: shebang"
        git_mode = subprocess.run(
            ["git", "ls-files", "-s", str((d / name).relative_to(REPO_ROOT))],
            capture_output=True, text=True, cwd=REPO_ROOT,
        ).stdout.split()
        if git_mode:  # tracked: must be committed as 100755
            assert git_mode[0] == "100755", f"{name} is mode {git_mode[0]} in git"
        else:
            assert (d / name).stat().st_mode & stat.S_IXUSR, f"{name} is not executable"


def test_check_sh_names_the_file_in_its_hint_and_is_deterministic():
    body = text(task_dir(HANDS_ON) / "check.sh")
    assert "smoke-ok" in body
    assert "FAIL 1" in body and "PASS 1" in body
    for noisy in ("curl", "wget", "ping ", "RANDOM", "$(date", "`date", "/dev/urandom", "nc "):
        assert noisy not in body, f"check.sh must be deterministic, found {noisy!r}"


def test_setup_sh_is_deterministic_and_never_creates_the_target_file():
    body = text(task_dir(HANDS_ON) / "setup.sh")
    for noisy in ("curl", "wget", "ping ", "RANDOM", "$(date", "/dev/urandom", "sleep"):
        assert noisy not in body, f"setup.sh must be deterministic, found {noisy!r}"
    assert not re.search(r"^\s*(touch|>|echo .*>)\s*\S*smoke-ok", body, re.M), (
        "setup.sh must leave the file absent"
    )


def test_the_task_text_tells_the_student_what_to_do():
    assert "smoke-ok" in text(task_dir(HANDS_ON) / "task.en.md")


# ---------------------------------------------------------------- docker: the scripts really run


def _docker(*args, timeout=600, check=True, stdin=None):
    res = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, input=stdin
    )
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


@pytest.fixture(scope="module")
def docker_available():
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    if _docker("info", check=False, timeout=60).returncode != 0:
        pytest.skip("the Docker daemon is not reachable")


@pytest.fixture(scope="module")
def image(docker_available):
    env = os.environ.get("LF_LAB_TERM_IMAGE")
    if env:
        return env
    assert (IMAGE_DIR / "Dockerfile").is_file(), "labs/images/term/Dockerfile is missing"
    tag = "lf-lab-term:exam"
    _docker("build", "-t", tag, str(IMAGE_DIR), timeout=1500)
    return tag


@pytest.fixture
def machine(image, docker_available, request):
    out = _docker("info", "--format", "{{json .Runtimes}}", check=False, timeout=60).stdout
    try:
        runtimes = json.loads(out or "{}")
    except ValueError:
        runtimes = {}
    flag = ["--runtime=runsc"] if "runsc" in runtimes else []
    name = f"lf-exam-{uuid.uuid4().hex[:10]}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    _docker(
        "run", "-d", "--name", name, *flag, "--log-driver", "none", "--network", "none",
        "--memory", "256m", "--pids-limit", "256",
        "--cap-drop", "ALL", "--cap-add", "SETUID", "--cap-add", "SETGID",
        "--security-opt", "no-new-privileges",
        "--tmpfs", "/var/log/lf:size=16m,mode=0700",
        "--tmpfs", "/home/student:size=128m,mode=0755,uid=1001,gid=1001,exec",
        "--tmpfs", "/tmp:size=64m,mode=1777,exec",
        "--tmpfs", "/var/tmp:size=32m,mode=1777",
        "--tmpfs", "/run/lock:size=1m,mode=1777",
        "--tmpfs", "/var/mail:size=1m,mode=0755",
        "-e", "LF_BASE_PATH=/s/exam/term/",
        image,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        ready = _docker("exec", name, "test", "-f", "/home/student/.bashrc", check=False)
        if ready.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("the lab container never became ready")
    return name


def _as_root(machine: str, script: Path):
    first = text(script).split("\n", 1)[0]
    interp = [p for p in first[2:].split() if not p.endswith("/env")][-1]
    return _docker(
        "exec", "-i", machine, interp, "-s", check=False, timeout=60, stdin=text(script)
    )


def _check(machine: str):
    from app.content import parse_check_output

    res = _as_root(machine, task_dir(HANDS_ON) / "check.sh")
    return res, parse_check_output(res.stdout, res.returncode)


@pytest.mark.docker
def test_after_setup_check_fails_with_a_hint_naming_the_file(machine):
    assert _as_root(machine, task_dir(HANDS_ON) / "setup.sh").returncode == 0
    res, parsed = _check(machine)
    assert res.returncode != 0 and not parsed.passed, res.stdout
    assert set(parsed.items) == {1}
    assert not parsed.items[1].passed
    assert parsed.items[1].hint and "smoke-ok" in parsed.items[1].hint


@pytest.mark.docker
def test_touching_the_file_as_the_student_makes_check_pass(machine):
    assert _as_root(machine, task_dir(HANDS_ON) / "setup.sh").returncode == 0
    touch = _docker("exec", machine, "su", "-", "student", "-c", "touch ~/smoke-ok", check=False)
    assert touch.returncode == 0, touch.stderr
    res, parsed = _check(machine)
    assert res.returncode == 0 and parsed.passed, res.stdout
    assert "FAIL" not in res.stdout


@pytest.mark.docker
def test_setup_is_idempotent_and_restores_the_failing_state_after_the_change(machine):
    setup = task_dir(HANDS_ON) / "setup.sh"
    assert _as_root(machine, setup).returncode == 0
    assert _as_root(machine, setup).returncode == 0
    _docker("exec", machine, "su", "-", "student", "-c", "touch ~/smoke-ok")
    assert _check(machine)[1].passed
    assert _as_root(machine, setup).returncode == 0
    assert not _check(machine)[1].passed


# ---------------------------------------------------------------- database: who may start it


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
    return f"10.128.{n // 250}.{n % 250 + 1}"


class FakeRuntime:
    def __init__(self) -> None:
        self.started: list = []

    async def start(self, spec):
        self.started.append(spec)
        return str(spec["name"])

    async def remove(self, name):
        return None

    async def list_lab_containers(self):
        return []

    async def exec_script(self, name, script, timeout=None):
        from app.runtime import ExecResult

        return ExecResult(0, "", False)


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
    def __init__(self, admin_dsn, client, runtime):
        self.admin_dsn, self.client, self.app, self.runtime = admin_dsn, client, client.app, runtime
        self.cohorts: dict[str, int] = {}

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def student_in(self, cohort_name: str):
        """Seed a fake student in the cohort `cohort_name` and log in; returns the client."""
        from argon2 import PasswordHasher
        from fastapi.testclient import TestClient

        raw = str(128000000 + next(_n))  # own id range (D47)
        pw = PasswordHasher().hash(COHORT_PASSWORD)

        async def seed(conn):
            if cohort_name not in self.cohorts:
                self.cohorts[cohort_name] = await conn.fetchval(
                    f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                    "values ($1, $2, $3) returning id",
                    cohort_name, pw, TODAY + 30 * DAY,
                )
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id, access_until) "
                "values ($1, $2, $3, $4)",
                id_hash(raw), f"Fake Student {raw}", self.cohorts[cohort_name], None,
            )

        self.sql(seed)
        c = TestClient(self.app, base_url="https://testserver", client=(fresh_ip(), 50000))
        c.portal = self.client.portal
        resp = c.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return c


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
    monkeypatch.setenv("LF_CONTENT_DIR", str(CONTENT_ROOT))
    monkeypatch.setenv("LF_MEMINFO_PATH", str(meminfo))
    monkeypatch.setenv("LF_DOCKER_HOST", f"unix://{tmp_path / 'no-docker.sock'}")
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "0")
    monkeypatch.setenv("LF_JANITOR_INTERVAL_S", "3600")
    monkeypatch.setenv("LF_MAX_TERM", "1000")
    for var in (
        "LF_TRUSTED_PROXY", "LF_MAX_CODE", "LF_MEM_RESERVE_MB", "LF_HEARTBEAT_TIMEOUT_S",
        "LF_DISK_PATH", "LF_SETUP_TIMEOUT_S", "LF_CHECK_TIMEOUT_S",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.main import app

    async def end_all(conn):
        await conn.execute(
            f"update {SCHEMA}.lab_sessions set status = 'ended', ended_at = now(), "
            "end_reason = 'orphan' where status in ('starting', 'running')"
        )

    runtime = FakeRuntime()
    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        app.state.runtime = runtime
        e = Env(_db, c, runtime)
        e.sql(end_all)
        yield e
        e.sql(end_all)


def test_a_normal_student_cannot_start_the_smoke_lab_and_gets_unknown_lab(env):
    client = env.student_in("Fake class")
    resp = client.post("/api/sessions", json={"lab_id": LAB_ID})
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "unknown_lab"}
    assert env.runtime.started == []


def test_the_refusal_is_the_same_as_for_a_lab_that_does_not_exist(env):
    client = env.student_in("Fake class")
    missing = client.post("/api/sessions", json={"lab_id": "no-such-lab-128"})
    internal = client.post("/api/sessions", json={"lab_id": LAB_ID})
    assert (internal.status_code, internal.content) == (missing.status_code, missing.content)


def test_a_cohort_whose_name_only_resembles_lf_smoke_is_refused(env):
    for name in ("lf-smoke-2", "LF-SMOKE", "lf-smoke "):
        client = env.student_in(name)
        resp = client.post("/api/sessions", json={"lab_id": LAB_ID})
        assert resp.status_code == 404, f"{name!r}: {resp.text}"


def test_a_student_of_the_lf_smoke_cohort_can_start_the_smoke_lab(env):
    client = env.student_in("lf-smoke")
    resp = client.post("/api/sessions", json={"lab_id": LAB_ID})
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["lab_id"] == LAB_ID
    assert len(env.runtime.started) == 1


def test_the_smoke_student_can_show_the_hands_on_task_of_the_smoke_lab(env):
    client = env.student_in("lf-smoke")
    sid = client.post("/api/sessions", json={"lab_id": LAB_ID}).json()["id"]
    resp = client.post(f"/api/sessions/{sid}/tasks/{HANDS_ON}/show")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == HANDS_ON
