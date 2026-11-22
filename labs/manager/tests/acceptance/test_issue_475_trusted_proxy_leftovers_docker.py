"""Exam for #475, docker part (CI `docker` job): LF_TRUSTED_PROXY set to a container NAME.

Same production path in miniature as test_issue_13_deploy_docker.py (real nginx loading
labs/deploy/nginx/labs.conf in front of the real manager image on one user-defined network), but
the manager gets `LF_TRUSTED_PROXY=<nginx container name>`, so the name goes through the real
Docker DNS. Observables: the manager's `trusted proxy <name> -> <nginx address>` startup line,
and the per-IP login limit keeping two clients apart (the address nginx saw is the key, which only
works when the name resolved to nginx's address).
"""
import os
import secrets
import subprocess
import time
import uuid

import pytest
from test_issue_13_deploy_docker import (
    CERT_DIR,
    DOCKERFILE,
    HOST,
    LABS_CONF,
    LIMIT,
    NGINX_IMAGE,
    PG_IMAGE,
    REPO_ROOT,
    _client,
    _curl,
    _docker,
    _login,
)

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    tag = os.environ.get("LF_MANAGER_IMAGE")
    run_id = uuid.uuid4().hex[:8]
    net = f"lf-exam475-net-{run_id}"
    names: list[str] = []
    try:
        if not tag:
            tag = f"lf-exam475-manager:{run_id}"
            _docker("build", "-f", str(DOCKERFILE), "-t", tag, str(REPO_ROOT / "labs"),
                    timeout=900, check=True)
        _docker("network", "create", net, check=True)
        certs = tmp_path_factory.mktemp("certs")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", f"/CN={HOST}", "-keyout", str(certs / "privkey.pem"),
             "-out", str(certs / "fullchain.pem")],
            check=True, capture_output=True,
        )
        nginx = f"lf-exam475-nginx-{run_id}"
        names.append(nginx)
        _docker("run", "-d", "--name", nginx, "--network", net,
                "-v", f"{LABS_CONF}:/etc/nginx/conf.d/default.conf:ro",
                "-v", f"{certs}:{CERT_DIR}:ro", NGINX_IMAGE, check=True)
        time.sleep(1)
        nginx_ip = _docker(
            "inspect", "-f", "{{(index .NetworkSettings.Networks \"" + net + "\").IPAddress}}",
            nginx, check=True,
        ).stdout.strip()

        pg = f"lf-exam475-pg-{run_id}"
        names.append(pg)
        pg_password = secrets.token_hex(12)
        _docker("run", "-d", "--name", pg, "--network", net,
                "-e", f"POSTGRES_PASSWORD={pg_password}", PG_IMAGE, check=True)
        for _ in range(60):
            if _docker("exec", pg, "psql", "-U", "postgres", "-c",
                       "create schema if not exists learning_factory").returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("postgres never accepted a query")

        mgr = f"lf-exam475-manager-{run_id}"
        names.append(mgr)
        _docker("run", "-d", "--name", mgr, "--network", net, "--network-alias", "lf-manager",
                "-e", f"LF_DATABASE_URL=postgresql://postgres:{pg_password}@{pg}:5432/postgres",
                "-e", f"LF_ID_SECRET={secrets.token_hex(16)}",
                "-e", f"LF_LOGIN_IP_LIMIT={LIMIT}",
                "-e", f"LF_TRUSTED_PROXY={nginx}",  # the NAME, not the address
                tag, check=True)
        ctx = {"net": net, "nginx_ip": nginx_ip, "nginx": nginx, "manager": mgr, "clients": names}
        probe = _client(ctx)
        deadline = time.time() + 90
        last = ""
        while time.time() < deadline:
            last = _curl(probe, nginx_ip, "/api/health").stdout
            if '"database"' in last and '"ok"' in last.split('"database"')[1][:12]:
                break
            time.sleep(2)
        else:
            pytest.fail(f"manager never reported database ok: {last!r}\n"
                        + _docker("logs", mgr).stdout[-2000:] + _docker("logs", mgr).stderr[-2000:])
        yield ctx
    finally:
        for n in names:
            _docker("rm", "-f", "-v", n, timeout=120)
        _docker("network", "rm", net, timeout=120)


def test_the_manager_resolves_the_nginx_container_name_through_docker_dns(stack):
    deadline = time.time() + 30
    logs = ""
    while time.time() < deadline:
        r = _docker("logs", stack["manager"])
        logs = r.stdout + r.stderr
        if f"trusted proxy {stack['nginx']} -> " in logs:
            break
        time.sleep(1)
    line = next((x for x in logs.splitlines() if f"trusted proxy {stack['nginx']}" in x), "")
    assert stack["nginx_ip"] in line and "lookup failed" not in line, logs[-2000:]


def test_with_the_proxy_given_by_name_two_clients_keep_separate_login_counters(stack):
    a, b = _client(stack), _client(stack)
    assert [_login(stack, a) for _ in range(LIMIT)] == [401] * LIMIT
    assert _login(stack, a) == 429
    # B is limited only by its own attempts: the key is the address nginx saw, so the name
    # resolved to nginx's address and X-Forwarded-For was honoured from it
    assert [_login(stack, b) for _ in range(LIMIT)] == [401] * LIMIT
    assert _login(stack, b) == 429


def test_with_the_proxy_given_by_name_a_forged_x_forwarded_for_changes_nothing(stack):
    c = _client(stack)
    codes = [_login(stack, c, xff=f"10.98.0.{i}") for i in range(LIMIT + 2)]
    assert codes[:LIMIT] == [401] * LIMIT and codes[LIMIT:] == [429, 429], codes
