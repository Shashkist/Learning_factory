"""Acceptance tests for issue #13, docker part (CI `docker` job): the trusted-proxy proof.

The production path in miniature, on one user-defined Docker network:

    client container --https--> nginx (pinned image, loads labs/deploy/nginx/labs.conf unchanged,
    self-signed cert mounted at the shared Let's Encrypt paths) --> manager image built from
    labs/manager/Dockerfile (network alias `lf-manager`, LF_TRUSTED_PROXY = nginx's address)

Observable chosen: the per-IP login rate limit (`LF_LOGIN_IP_LIMIT`, set to 3 here).
`POST /api/auth/login` with a malformed student id answers 401 and counts one attempt for the
caller's IP key only (no id key, no database row involved), and the 4th attempt answers 429.
The login route needs a database pool before it looks at the body, so the exam starts a
throw-away `postgres` container on the same network and gives the manager its URL (the manager
migrates it on start). The credentials are random per run and exist only inside that network.

What this proves:
- two client containers (two IPs) have separate counters: the manager keys on the address nginx
  saw, not on nginx's own address (otherwise B would be blocked by A's attempts);
- a client that forges `X-Forwarded-For` cannot change its key (nginx overwrites the header),
  and cannot spend another client's budget.
"""
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
LABS_CONF = REPO_ROOT / "labs" / "deploy" / "nginx" / "labs.conf"
DOCKERFILE = REPO_ROOT / "labs" / "manager" / "Dockerfile"
NGINX_IMAGE = "nginx:1.27.3-alpine"
PG_IMAGE = "postgres:16.4-alpine"
CURL_IMAGE = "curlimages/curl:8.10.1"
HOST = "labs.shashkist.com"
LIMIT = 3
CERT_DIR = "/etc/letsencrypt/live/teams.shashkist.com"


def _docker(*args: str, timeout: int = 900, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise AssertionError(f"docker {' '.join(args[:3])} failed: {r.stdout}\n{r.stderr}")
    return r


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    assert LABS_CONF.is_file(), "labs/deploy/nginx/labs.conf is missing"
    tag = os.environ.get("LF_MANAGER_IMAGE")
    run_id = uuid.uuid4().hex[:8]
    net = f"lf-exam13-net-{run_id}"
    names: list[str] = []
    try:
        if not tag:
            tag = f"lf-exam13-manager:{run_id}"
            _docker("build", "-f", str(DOCKERFILE), "-t", tag, str(REPO_ROOT / "labs"),
                    timeout=1800, check=True)
        _docker("network", "create", net, check=True)

        # throw-away certificate at the exact paths labs.conf names
        certs = tmp_path_factory.mktemp("certs")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", f"/CN={HOST}", "-keyout", str(certs / "privkey.pem"),
             "-out", str(certs / "fullchain.pem")],
            check=True, capture_output=True,
        )
        nginx = f"lf-exam13-nginx-{run_id}"
        names.append(nginx)
        _docker("run", "-d", "--name", nginx, "--network", net,
                "-v", f"{LABS_CONF}:/etc/nginx/conf.d/default.conf:ro",
                "-v", f"{certs}:{CERT_DIR}:ro", NGINX_IMAGE, check=True)
        time.sleep(1)
        state = _docker("inspect", "-f", "{{.State.Running}}", nginx).stdout.strip()
        assert state == "true", "nginx did not load labs.conf:\n" + _docker("logs", nginx).stderr
        nginx_ip = _docker(
            "inspect", "-f", "{{(index .NetworkSettings.Networks \"" + net + "\").IPAddress}}",
            nginx, check=True,
        ).stdout.strip()

        pg = f"lf-exam13-pg-{run_id}"
        names.append(pg)
        pg_password = secrets.token_hex(12)
        _docker("run", "-d", "--name", pg, "--network", net,
                "-e", f"POSTGRES_PASSWORD={pg_password}", PG_IMAGE, check=True)
        for _ in range(60):
            if _docker("exec", pg, "pg_isready", "-U", "postgres").returncode == 0:
                break
            time.sleep(1)
        # the first pg_isready can answer during the init restart: ask a real query
        for _ in range(60):
            if _docker("exec", pg, "psql", "-U", "postgres", "-c",
                       "create schema if not exists learning_factory").returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("postgres never accepted a query:\n" + _docker("logs", pg).stderr[-1500:])

        mgr = f"lf-exam13-manager-{run_id}"
        names.append(mgr)
        _docker("run", "-d", "--name", mgr, "--network", net, "--network-alias", "lf-manager",
                "-e", f"LF_DATABASE_URL=postgresql://postgres:{pg_password}@{pg}:5432/postgres",
                "-e", f"LF_ID_SECRET={secrets.token_hex(16)}",
                "-e", f"LF_LOGIN_IP_LIMIT={LIMIT}",
                "-e", f"LF_TRUSTED_PROXY={nginx_ip}",
                tag, check=True)
        ctx = {"net": net, "nginx_ip": nginx_ip, "manager": mgr, "clients": names}
        probe = _client(ctx)
        deadline = time.time() + 90
        last = ""
        while time.time() < deadline:
            r = _curl(probe, nginx_ip, "/api/health")
            last = r.stdout
            if '"database"' in last and '"ok"' in last.split('"database"')[1][:12]:
                break
            time.sleep(2)
        else:
            pytest.fail(
                f"manager never reported database ok through nginx: {last!r}\n"
                + _docker("logs", mgr).stdout[-2500:] + _docker("logs", mgr).stderr[-2500:]
            )
        yield ctx
    finally:
        for n in names:
            _docker("rm", "-f", "-v", n, timeout=120)
        _docker("network", "rm", net, timeout=120)


def _client(ctx: dict) -> str:
    name = f"lf-exam13-client-{uuid.uuid4().hex[:8]}"
    ctx["clients"].append(name)
    _docker("run", "-d", "--name", name, "--network", ctx["net"], "--entrypoint", "sleep",
            CURL_IMAGE, "3600", check=True)
    return name


def _curl(client: str, nginx_ip: str, path: str, *, method: str = "GET", body: str = "",
          headers: tuple[str, ...] = (), head: bool = False) -> subprocess.CompletedProcess:
    cmd = ["exec", client, "curl", "-sk", "--resolve", f"{HOST}:443:{nginx_ip}", "-X", method]
    if head:
        cmd += ["-D", "-", "-o", "/dev/null"]
    else:
        cmd += ["-w", "\n%{http_code}"]
    for h in headers:
        cmd += ["-H", h]
    if body:
        cmd += ["-H", "Content-Type: application/json", "-d", body]
    cmd.append(f"https://{HOST}{path}")
    return _docker(*cmd, timeout=60)


def _login(ctx: dict, client: str, *, xff: str | None = None) -> int:
    headers = (f"X-Forwarded-For: {xff}",) if xff else ()
    r = _curl(client, ctx["nginx_ip"], "/api/auth/login", method="POST",
              body=json.dumps({"id": "not-a-student-id", "password": "x"}), headers=headers)
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip().rsplit("\n", 1)[-1])


def test_nginx_loads_labs_conf_and_proxies_to_the_manager_by_docker_dns(stack):
    client = _client(stack)
    r = _curl(client, stack["nginx_ip"], "/healthz")
    assert r.stdout.strip().endswith("200"), r.stdout + r.stderr
    assert '"ok"' in r.stdout


def test_responses_carry_x_robots_tag_noindex(stack):
    client = _client(stack)
    r = _curl(client, stack["nginx_ip"], "/healthz", head=True, method="GET")
    assert "x-robots-tag: noindex" in r.stdout.lower(), r.stdout


def test_two_clients_have_separate_login_rate_limit_counters(stack):
    a, b = _client(stack), _client(stack)
    assert [_login(stack, a) for _ in range(LIMIT)] == [401] * LIMIT
    assert _login(stack, a) == 429, "client A must be limited after its own attempts"
    # B has not made an attempt: it must not share A's counter (nor nginx's address as a key)
    assert [_login(stack, b) for _ in range(LIMIT)] == [401] * LIMIT
    assert _login(stack, b) == 429


def test_a_forged_x_forwarded_for_does_not_change_the_clients_key(stack):
    c = _client(stack)
    codes = [_login(stack, c, xff=f"10.99.0.{i}") for i in range(LIMIT + 2)]
    assert codes[:LIMIT] == [401] * LIMIT, codes
    assert codes[LIMIT:] == [429, 429], f"a rotating forged header reset the limit: {codes}"


def test_a_forged_x_forwarded_for_cannot_spend_another_clients_budget(stack):
    attacker, victim = _client(stack), _client(stack)
    victim_ip = _docker(
        "inspect", "-f", "{{(index .NetworkSettings.Networks \"" + stack["net"] + "\").IPAddress}}",
        victim, check=True,
    ).stdout.strip()
    for _ in range(LIMIT + 2):
        _login(stack, attacker, xff=victim_ip)
    assert _login(stack, victim) == 401, "the victim was rate limited by someone else's header"
