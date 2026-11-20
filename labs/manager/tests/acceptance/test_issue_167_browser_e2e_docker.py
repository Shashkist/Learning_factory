"""Acceptance test for issue #167, Docker part (CI `docker` job, runsc): one browser flow against
the REAL manager.

What the exam starts: the real manager (`python -m uvicorn app.main:app`, its own process, so the
UI mount is decided by this exam's environment) with LF_WEB_DIR pointing at the built web app
(labs/web/dist) and a real Postgres. The smoke student is made by the real CLI
(`python -m app.cli smoke-student`, D36). Chromium is driven by Playwright from labs/web:
tests/acceptance/playwright.docker.config.ts and issue-167-docker-e2e.pw.ts (`npx --no-install`).
Then the docker CLI must show the lab-<uuid> container of the run gone.

Contract for the CI `docker` job (pinned by test_issue_167_ci_wiring.py): Node 22, `npm ci` and
`npm run build` in labs/web, Chromium installed for Playwright, all before the docker gate.

Lab content (decision, see the hand-back of #167): the catalog never lists `internal` or `draft`
labs, not even to the smoke cohort (#129 exam). So the exam gives the manager its own content
folder (LF_CONTENT_DIR) holding a copy of the smoke lab `smoke-tasks` with `status: published`;
real content is never touched.

This exam FAILS, never skips, when the built UI, Node or Chromium is missing: a skipped docker exam
fails the junit gate anyway, and a missing browser must be a red job, not a quiet pass.
Throwaway `postgres:16` container `lf-exam-pg-<hex>` unless LF_TEST_DATABASE_URL is set. Credentials
are throwaway; the smoke student is the synthetic ID of D36. Only containers of this module are
removed.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import lab_image
import pytest

pytestmark = pytest.mark.docker

MANAGER_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
WEB_DIR = REPO_ROOT / "labs" / "web"
DIST = WEB_DIR / "dist"
PW_CONFIG = "tests/acceptance/playwright.docker.config.ts"
SMOKE_LAB_SRC = REPO_ROOT / "labs" / "content" / "smoke" / "smoke-tasks"
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"
PG_PASSWORD = "test-only-" + "pg"
TEST_SECRET = "test-only-id-" + "secret"
SMOKE_ID = "990000000000000000000000000001"
SMOKE_PASSWORD = "smoke-e2e-" + "pw-0001"
LAB_ID = "smoke-tasks"
LAB_TITLE = "Smoke test, task API"  # lab.yaml title.en
MANAGER_LOG: list[Path] = []  # the running manager's log file, for the failure message


def docker(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


def _rm(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)


def _run_as(dsn: str, fn):
    async def go():
        import asyncpg

        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


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
        assert urlsplit(given).hostname in ("localhost", "127.0.0.1", "::1")
        yield given
        return
    name = f"lf-exam-pg-{uuid.uuid4().hex[:10]}"
    docker(
        "run", "-d", "--name", name, "-e", f"POSTGRES_PASSWORD={PG_PASSWORD}",
        "-p", "127.0.0.1::5432", "postgres:16", timeout=900,
    )
    try:
        out = docker("port", name, "5432/tcp").stdout.split()[0]
        dsn = f"postgresql://postgres:{PG_PASSWORD}@127.0.0.1:{int(out.rsplit(':', 1)[1])}/postgres"

        async def ping(conn):
            return await conn.fetchval("select 1")

        deadline = time.time() + 90
        while True:
            try:
                _run_as(dsn, ping)
                break
            except Exception:  # noqa: BLE001 - still starting
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


@pytest.fixture(scope="module")
def content_dir(tmp_path_factory) -> Path:
    """Own content root: the smoke lab, published (the catalog hides `internal` labs)."""
    root = tmp_path_factory.mktemp("content")
    target = root / "smoke" / LAB_ID
    shutil.copytree(SMOKE_LAB_SRC, target)
    lab_yaml = target / "lab.yaml"
    text = lab_yaml.read_text(encoding="utf-8")
    assert "status: internal" in text, "the smoke lab is no longer `status: internal`"
    lab_yaml.write_text(text.replace("status: internal", "status: published"), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def manager(admin_dsn, network, image, content_dir, tmp_path_factory):
    """The real manager process, serving the built UI, and the smoke student from the real CLI."""
    assert (DIST / "index.html").is_file(), (
        f"{DIST}/index.html is missing: build the web app first (`npm run build` in labs/web)"
    )

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)
    app_dsn = _app_dsn(admin_dsn)
    env = {k: v for k, v in os.environ.items() if not k.startswith("LF_")}
    env.update(
        LF_DATABASE_URL=app_dsn,
        LF_ID_SECRET=TEST_SECRET,
        LF_LAB_TERM_IMAGE=image,
        LF_LAB_NETWORK=network,
        LF_MEM_RESERVE_MB="0",
        LF_DISK_RESERVE_GB="0",
        LF_JANITOR_INTERVAL_S="3600",
        LF_CONTENT_DIR=str(content_dir),
        LF_WEB_DIR=str(DIST),
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log_path = tmp_path_factory.mktemp("manager-log") / "manager.log"
    MANAGER_LOG[:] = [log_path]
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning"],
            cwd=MANAGER_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.time() + 60
            while True:
                assert proc.poll() is None, f"the manager exited: {log_path.read_text()[-2000:]}"
                try:
                    with urllib.request.urlopen(base + "/healthz", timeout=2):
                        break
                except Exception:  # noqa: BLE001 - still starting
                    assert time.time() < deadline, "the manager did not start"
                    time.sleep(0.3)
            # The manager's startup applies the schema, so the CLI runs once it answers (as #86).
            cli = subprocess.run(
                [sys.executable, "-m", "app.cli", "smoke-student"],
                input=SMOKE_PASSWORD + "\n", text=True, capture_output=True, cwd=MANAGER_DIR,
                env=env, timeout=90,
            )
            assert cli.returncode == 0, cli.stdout + cli.stderr
            yield base
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_manager_serves_the_built_web_app_next_to_the_api(manager):
    with urllib.request.urlopen(manager + "/", timeout=10) as res:
        assert res.status == 200
        assert "text/html" in res.headers["content-type"]
        assert 'id="root"' in res.read().decode("utf-8"), "/ is not the built web app"
    with urllib.request.urlopen(manager + "/healthz", timeout=10) as res:
        assert res.status == 200


def _annotations(node: object, kind: str) -> list[str]:
    """Every distinct `kind` annotation in a Playwright JSON report, wherever it sits."""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == kind and isinstance(node.get("description"), str):
            found.append(node["description"])
        for value in node.values():
            found += _annotations(value, kind)
    elif isinstance(node, list):
        for value in node:
            found += _annotations(value, kind)
    return sorted(set(found))


def _log_tail(chars: int = 3000) -> str:
    if not MANAGER_LOG or not MANAGER_LOG[0].is_file():
        return "(no manager log)"
    return MANAGER_LOG[0].read_text(encoding="utf-8", errors="replace")[-chars:]


def _watch(stop: threading.Event, seen: set[str]) -> None:
    while not stop.is_set():
        names = docker("ps", "-a", "--filter", "name=^lab-", "--format", "{{.Names}}", check=False)
        seen.update(names.stdout.split())
        time.sleep(0.3)


def test_smoke_student_runs_the_lab_in_chromium_and_the_container_is_gone_after(
    manager, tmp_path
):
    assert shutil.which("node"), "node is not installed (the docker job must set up Node)"
    assert shutil.which("npx"), "npx is not installed (the docker job must set up Node)"
    assert (WEB_DIR / "node_modules" / "@playwright" / "test").is_dir(), (
        "labs/web/node_modules has no @playwright/test: run `npm ci` in labs/web first"
    )
    assert (WEB_DIR / PW_CONFIG).is_file(), f"labs/web/{PW_CONFIG} is missing"
    report = tmp_path / "report.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LF_", "SMOKE_"))}
    env.update(
        CI="1",
        LF_E2E_BASE_URL=manager,
        LF_E2E_ID=SMOKE_ID,
        LF_E2E_PASSWORD=SMOKE_PASSWORD,
        LF_E2E_LAB_ID=LAB_ID,
        LF_E2E_LAB_TITLE=LAB_TITLE,
        LF_E2E_REPORT=str(report),
        LF_E2E_OUTPUT_DIR=str(tmp_path / "pw-output"),  # failures must not write into the repo
    )
    stop = threading.Event()
    seen: set[str] = set()
    watcher = threading.Thread(target=_watch, args=(stop, seen), daemon=True)
    watcher.start()
    try:
        # --no-install: a missing Playwright must fail here, never download at test time.
        run = subprocess.run(
            ["npx", "--no-install", "playwright", "test", "-c", PW_CONFIG],
            cwd=WEB_DIR, env=env, capture_output=True, text=True, timeout=600,
        )
    finally:
        stop.set()
        watcher.join(timeout=10)
    try:
        assert run.returncode == 0, (
            "the browser flow failed (is Chromium installed? `npx playwright install chromium`):\n"
            + (run.stdout + run.stderr)[-4000:]
            + "\n--- manager log tail ---\n"
            + _log_tail()
        )
        stats = json.loads(report.read_text(encoding="utf-8"))["stats"]
        assert stats["expected"] == 1, f"the browser test did not run and pass once: {stats}"
        assert not (stats["unexpected"] or stats["skipped"] or stats["flaky"]), stats
        assert SMOKE_PASSWORD not in run.stdout + run.stderr
        sessions = _annotations(json.loads(report.read_text(encoding="utf-8")), "lf-session")
        assert len(sessions) == 1, f"want one lf-session annotation in the report: {sessions}"
        session = sessions[0]
        name = f"lab-{session}"
        assert name in seen, f"{name} was never seen running; saw {sorted(seen)}"
        deadline = time.time() + 30
        while docker("inspect", name, check=False).returncode == 0:
            assert time.time() < deadline, f"{name} still exists after End lab"
            time.sleep(1)
    finally:
        # Only this run's container: `lab-<session>` from the report. Another lab-* container
        # (another run, the owner's) is none of this exam's business, even if the watcher saw it.
        try:
            mine = _annotations(json.loads(report.read_text(encoding="utf-8")), "lf-session")
        except (OSError, ValueError):
            mine = []
        for session in mine:
            _rm(f"lab-{session}")
