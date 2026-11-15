"""Acceptance test for issue #86, Docker part (CI `docker` job, runsc): the real chain, minus
nginx and the public network.

The real manager app is served by uvicorn on 127.0.0.1, `smoke-student` (the real CLI) creates the
smoke student, and the real client `labs/deploy/smoke/terminal.py` logs in, starts the real lab
`hello` from labs/content/sample/hello (a real `lf-lab-term` container under runsc), opens the
terminal WebSocket, types `echo hi` after the prompt and ends the session. The runner is not on
the lab network and cannot resolve `lab-<uuid>`, so the exam uses the `LF_TERM_UPSTREAM` seam of
test_issue_8_term_proxy.py (the live smoke on the server covers the Docker DNS name).

Throwaway `postgres:16` container `lf-exam-pg-<hex>` unless LF_TEST_DATABASE_URL is set; image is
$LF_LAB_TERM_IMAGE if set, else built from labs/images/term. Only containers of this module are
touched. Credentials are throwaway.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

pytestmark = pytest.mark.docker

MANAGER_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CLIENT = REPO_ROOT / "labs" / "deploy" / "smoke" / "terminal.py"
IMAGE_DIR = REPO_ROOT / "labs" / "images" / "term"
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"
PG_PASSWORD = "test-only-" + "pg"
TEST_SECRET = "test-only-id-" + "secret"
SMOKE_ID = "990000000000000000000000000001"
SMOKE_PASSWORD = "smoke-docker-" + "pw-0001"
TTYD_PORT = 7681


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
    env = os.environ.get("LF_LAB_TERM_IMAGE")
    if env:
        return env
    assert (IMAGE_DIR / "Dockerfile").is_file(), "labs/images/term/Dockerfile is missing"
    tag = "lf-lab-term:exam"
    docker("build", "-t", tag, str(IMAGE_DIR), timeout=1500)
    return tag


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
def served(admin_dsn, network, image):
    """The real app on 127.0.0.1 and the smoke student made by the real CLI."""
    import uvicorn

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)
    app_dsn = _app_dsn(admin_dsn)
    mp = pytest.MonkeyPatch()
    mp.setenv("LF_DATABASE_URL", app_dsn)
    mp.setenv("LF_ID_SECRET", TEST_SECRET)
    mp.setenv("LF_LAB_TERM_IMAGE", image)
    mp.setenv("LF_LAB_NETWORK", network)
    mp.setenv("LF_MEM_RESERVE_MB", "0")
    mp.setenv("LF_DISK_RESERVE_GB", "0")
    mp.setenv("LF_JANITOR_INTERVAL_S", "3600")
    for var in ("LF_DOCKER_HOST", "LF_TRUSTED_PROXY", "LF_TERM_UPSTREAM", "LF_MEMINFO_PATH",
                "LF_CONTENT_DIR"):
        mp.delenv(var, raising=False)
    from app.main import app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", ws="auto")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 60
    while not server.started:
        assert time.time() < deadline, "the manager did not start"
        time.sleep(0.1)
    cli = subprocess.run(
        [sys.executable, "-m", "app.cli", "smoke-student"],
        input=SMOKE_PASSWORD + "\n", text=True, capture_output=True, cwd=MANAGER_DIR,
        env={**os.environ, "LF_DATABASE_URL": app_dsn, "LF_ID_SECRET": TEST_SECRET},
        timeout=90,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        mp.undo()


def _seam(network: str, stop: threading.Event, seen: list[str]) -> None:
    """Point the proxy at the lab container as soon as the client has created it."""
    while not stop.is_set():
        names = docker("ps", "--filter", "name=^lab-", "--format", "{{.Names}}", check=False)
        for name in names.stdout.split():
            res = docker("inspect", name, check=False)
            if res.returncode != 0:
                continue
            ip = json.loads(res.stdout)[0]["NetworkSettings"]["Networks"].get(network, {}).get(
                "IPAddress"
            )
            if ip:
                seen.append(name)
                os.environ["LF_TERM_UPSTREAM"] = f"{ip}:{TTYD_PORT}"
                return
        time.sleep(0.3)


def test_the_client_runs_echo_hi_in_a_real_lab_and_the_container_is_gone_after(served, network):
    assert CLIENT.is_file(), "labs/deploy/smoke/terminal.py is missing"
    stop = threading.Event()
    seen: list[str] = []
    watcher = threading.Thread(target=_seam, args=(network, stop, seen), daemon=True)
    watcher.start()
    try:
        res = subprocess.run(
            [sys.executable, str(CLIENT), served],
            capture_output=True, text=True, timeout=400, cwd=REPO_ROOT,
            env={
                **{k: v for k, v in os.environ.items() if not k.startswith("SMOKE_")},
                "SMOKE_ID": SMOKE_ID,
                "SMOKE_PASSWORD": SMOKE_PASSWORD,
                "SMOKE_TIMEOUT": "120",
            },
        )
    finally:
        stop.set()
        watcher.join(timeout=10)
        os.environ.pop("LF_TERM_UPSTREAM", None)
    try:
        assert res.returncode == 0, res.stdout + res.stderr
        assert SMOKE_PASSWORD not in res.stdout + res.stderr
        assert seen, "no lab container was ever started"
        assert seen[0].removeprefix("lab-") in res.stdout, "the client must print the session id"
        deadline = time.time() + 30
        while docker("inspect", seen[0], check=False).returncode == 0:
            assert time.time() < deadline, f"{seen[0]} still exists after the client ended"
            time.sleep(1)
    finally:
        for name in seen:
            _rm(name)
