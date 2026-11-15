"""Acceptance tests for issue #72: manager container image (serves the web UI) and compose file.

Non-docker tests: SPA/static serving through the FastAPI app, the compose file contract and the
Dockerfile shape. Docker tests (CI `docker` job): build `labs/manager/Dockerfile` with context
`labs/` (or use `LF_MANAGER_IMAGE`), run it with no database URL and no Docker socket, and check
it over HTTP.
"""
import importlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
COMPOSE = REPO_ROOT / "labs" / "deploy" / "compose.yml"
DOCKERFILE = REPO_ROOT / "labs" / "manager" / "Dockerfile"

INDEX = "<!doctype html><html><body><div id=root>lf-fake-spa-72</div></body></html>"
ASSET = "console.log('lf-fake-asset-72');"
SECRET_KEY = r"SECRET|PASSWORD|TOKEN|PEPPER|DATABASE_URL|DSN"
DSN_WITH_PASSWORD = r"postgres(ql)?://[^\s/]*:[^\s@/]+@"


# ---------------------------------------------------------------- static serving (TestClient)

def _fresh_app(monkeypatch, web_dir: Path | None):
    """Import `app.main` afresh with LF_WEB_DIR set or unset (import-time wiring is honoured)."""
    if web_dir is None:
        monkeypatch.delenv("LF_WEB_DIR", raising=False)
    else:
        monkeypatch.setenv("LF_WEB_DIR", str(web_dir))
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main").app


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    d = tmp_path / "web"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX, encoding="utf-8")
    (d / "assets" / "app.js").write_text(ASSET, encoding="utf-8")
    return d


@pytest.fixture
def client(monkeypatch, web_dir):
    from fastapi.testclient import TestClient

    app = _fresh_app(monkeypatch, web_dir)
    with TestClient(app) as c:
        yield c
    sys.modules.pop("app.main", None)


def test_root_returns_index_html_when_lf_web_dir_is_set(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "lf-fake-spa-72" in r.text
    assert "text/html" in r.headers["content-type"]


def test_an_existing_asset_is_served_as_is(client):
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert "lf-fake-asset-72" in r.text


def test_unknown_non_api_path_falls_back_to_index_html(client):
    for path in ("/labs/linux-1", "/teacher/dashboard", "/some/deep/client/route"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "lf-fake-spa-72" in r.text, path


def test_healthz_is_not_shadowed_by_the_spa_fallback(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_api_health_is_not_shadowed_by_the_spa_fallback(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["database"] == "not_configured"


def test_unknown_api_path_is_a_json_404_not_the_spa(client):
    r = client.get("/api/does-not-exist-72")
    assert r.status_code == 404
    assert "lf-fake-spa-72" not in r.text
    assert "application/json" in r.headers["content-type"]


def test_unknown_s_path_is_not_the_spa(client):
    # /s/* belongs to the terminal proxy; the fallback must never answer it with index.html.
    r = client.get("/s/00000000-0000-0000-0000-000000000072/")
    assert "lf-fake-spa-72" not in r.text
    assert r.status_code in (401, 403, 404)


def test_missing_lf_web_dir_means_api_only(monkeypatch):
    from fastapi.testclient import TestClient

    app = _fresh_app(monkeypatch, None)
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}
        assert c.get("/api/health").json()["database"] == "not_configured"
        assert c.get("/").status_code == 404
        assert c.get("/labs/linux-1").status_code == 404
    sys.modules.pop("app.main", None)


def test_lf_web_dir_pointing_nowhere_is_a_supported_state(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    app = _fresh_app(monkeypatch, tmp_path / "does-not-exist")
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/").status_code == 404
    sys.modules.pop("app.main", None)


# ---------------------------------------------------------------- compose file

def _compose() -> dict:
    yaml = pytest.importorskip("yaml")
    assert COMPOSE.is_file(), f"{COMPOSE} is missing"
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _service() -> dict:
    services = _compose().get("services") or {}
    assert "lf-manager" in services, "service lf-manager is missing"
    return services["lf-manager"]


def _env(svc: dict) -> dict[str, str]:
    env = svc.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) if "=" in item else (item, "") for item in env)
    return {k: "" if v is None else str(v) for k, v in env.items()}


def test_compose_parses_and_names_the_project():
    assert _compose().get("name") == "learning-factory"


def test_compose_container_name_is_lf_manager():
    assert _service().get("container_name") == "lf-manager"


def test_compose_image_is_the_ghcr_manager_image_with_tag_variable():
    assert _service().get("image") == "ghcr.io/shashkist/lf-manager:${LF_MANAGER_TAG:-latest}"


def test_compose_restarts_unless_stopped():
    assert _service().get("restart") == "unless-stopped"


def test_compose_reads_the_server_env_file():
    env_file = _service().get("env_file")
    files = [env_file] if isinstance(env_file, str) else [
        e if isinstance(e, str) else e.get("path") for e in (env_file or [])
    ]
    assert "/root/learning-factory/.env" in files


def test_compose_mounts_the_docker_socket():
    volumes = [
        str(v) if not isinstance(v, dict) else f"{v.get('source')}:{v.get('target')}"
        for v in (_service().get("volumes") or [])
    ]
    assert any(v.startswith("/var/run/docker.sock") for v in volumes), volumes


def test_compose_sets_the_login_limit_and_lab_network_environment():
    env = _env(_service())
    assert env.get("LF_LOGIN_IP_LIMIT") == "100"
    assert env.get("LF_LAB_NETWORK") == "lf-labs"
    assert "LF_TRUSTED_PROXY" in env["LF_TRUSTED_PROXY"] if "LF_TRUSTED_PROXY" in env else False


def test_compose_lf_labs_network_is_external_with_the_fixed_address():
    nets = _compose().get("networks") or {}
    assert "lf-labs" in nets, "lf-labs network is not declared"
    assert nets["lf-labs"].get("external") is True
    svc_nets = _service().get("networks")
    assert isinstance(svc_nets, dict), "service networks must be a mapping (fixed address)"
    assert "lf-labs" in svc_nets
    assert (svc_nets["lf-labs"] or {}).get("ipv4_address") == "172.31.240.2"


def test_compose_joins_the_external_nginx_network_named_from_a_variable():
    nets = _compose().get("networks") or {}
    nginx = [n for n in nets.values() if n and "LF_NGINX_NETWORK" in str(n.get("name", ""))]
    assert len(nginx) == 1, "expected one network named from ${LF_NGINX_NETWORK}"
    assert nginx[0].get("external") is True
    assert len(_service().get("networks") or {}) >= 2


def test_compose_has_a_memory_limit():
    svc = _service()
    limit = svc.get("mem_limit") or (
        ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}
    ).get("memory")
    assert limit, "no memory limit set"


def test_compose_holds_no_literal_secret_values():
    assert COMPOSE.is_file(), f"{COMPOSE} is missing"
    text = COMPOSE.read_text(encoding="utf-8")
    for key, value in _env(_service()).items():
        if re.search(SECRET_KEY + r"|KEY", key, re.I):
            assert value == "" or value.startswith("${"), f"{key} carries a literal value"
    assert not re.search(DSN_WITH_PASSWORD, text), "DSN with a password"
    assert not re.search(r"(ghp_|github_pat_|sk-)[A-Za-z0-9_]{10,}", text)


# ---------------------------------------------------------------- Dockerfile (static)

def test_dockerfile_is_multi_stage_python312_with_frozen_uv_lock_and_uvicorn():
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert len(re.findall(r"^FROM\s", text, re.M | re.I)) >= 2, "not multi-stage"
    assert re.search(r"python:3\.12\S*slim", text)
    assert "npm ci" in text and "npm run build" in text
    assert "uv.lock" in text
    assert "--frozen" in text or "--locked" in text
    assert "LF_CONTENT_DIR" in text and "LF_WEB_DIR" in text
    assert "uvicorn" in text and "app.main:app" in text and "8000" in text


def test_dockerfile_does_not_turn_on_uvicorn_proxy_headers():
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "--proxy-headers" not in text.replace("--no-proxy-headers", "")


def test_dockerfile_bakes_in_no_secret_variables():
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert not re.search(rf"^\s*(ENV|ARG)\s+\w*({SECRET_KEY})\w*(\s*=\s*|\s+)\S", text,
                         re.M | re.I)


# ---------------------------------------------------------------- docker (CI only)

def _docker(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="module")
def manager_image() -> str:
    preset = os.environ.get("LF_MANAGER_IMAGE")
    if preset:
        return preset
    tag = f"lf-manager-exam-{uuid.uuid4().hex[:8]}"
    r = _docker("build", "-f", str(DOCKERFILE), "-t", tag, str(REPO_ROOT / "labs"), timeout=1800)
    assert r.returncode == 0, r.stderr[-2000:]
    return tag


@pytest.fixture(scope="module")
def running(manager_image):
    name = f"lf-test-{uuid.uuid4().hex[:8]}"
    r = _docker("run", "-d", "--name", name, "-p", "127.0.0.1::8000", manager_image)
    try:
        assert r.returncode == 0, r.stderr
        port = _docker("port", name, "8000/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            pytest.fail("never answered /healthz:\n" + _docker("logs", name).stdout[-2000:])
        yield name, base
    finally:
        _docker("rm", "-f", "-v", name, timeout=120)


def _get(base: str, path: str) -> tuple[int, str, str]:
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, resp.headers.get("content-type", ""), resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read().decode()


@pytest.mark.docker
def test_container_healthz_answers_200_without_database_or_socket(running):
    status, _, body = _get(running[1], "/healthz")
    assert status == 200
    assert json.loads(body) == {"ok": True}


@pytest.mark.docker
def test_container_api_health_reports_database_not_configured(running):
    status, _, body = _get(running[1], "/api/health")
    assert status == 200
    assert json.loads(body)["database"] == "not_configured"


@pytest.mark.docker
def test_container_serves_the_built_ui_at_root(running):
    status, ctype, body = _get(running[1], "/")
    assert status == 200
    assert "text/html" in ctype
    assert "<html" in body.lower()


@pytest.mark.docker
def test_container_spa_fallback_for_an_unknown_non_api_path(running):
    base = running[1]
    _, _, index = _get(base, "/")
    status, ctype, body = _get(base, "/labs/linux-1")
    assert status == 200
    assert "text/html" in ctype
    assert body == index


@pytest.mark.docker
def test_container_unknown_api_path_is_json_404_not_the_spa(running):
    status, ctype, body = _get(running[1], "/api/does-not-exist-72")
    assert status == 404
    assert "application/json" in ctype
    assert "<html" not in body.lower()


@pytest.mark.docker
def test_container_logs_at_least_one_lab_loaded(running):
    logs = _docker("logs", running[0])
    out = logs.stdout + logs.stderr
    m = re.search(r"labs loaded: (\d+)", out)
    assert m, out[-2000:]
    assert int(m.group(1)) >= 1


@pytest.mark.docker
def test_image_has_no_secret_looking_environment(manager_image):
    r = _docker("image", "inspect", "-f", "{{json .Config.Env}}", manager_image)
    assert r.returncode == 0, r.stderr
    for item in json.loads(r.stdout):
        key, _, value = item.partition("=")
        assert not re.search(SECRET_KEY, key, re.I), f"secret-looking variable: {key}"
        assert not re.search(DSN_WITH_PASSWORD, value), key


@pytest.mark.docker
def test_container_logs_hold_no_secret_values(running):
    logs = _docker("logs", running[0])
    assert not re.search(DSN_WITH_PASSWORD, logs.stdout + logs.stderr)
