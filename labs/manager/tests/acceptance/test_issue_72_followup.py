"""Acceptance tests for the issue #72 follow-up: image CI, and gaps found in review of the first PR.

Compose contract (default route, log rotation), SPA serving details (HEAD, cache headers, dotted
deep links, terminal routes never shadowed) and the CI contract for building and pushing the
manager image. All non-docker.
"""
import importlib
import re
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
COMPOSE = REPO_ROOT / "labs" / "deploy" / "compose.yml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

INDEX = "<!doctype html><html><body><div id=root>lf-fake-spa-72b</div></body></html>"
ASSET = "console.log('lf-fake-asset-72b');"


# ---------------------------------------------------------------- helpers

def _fresh_app(monkeypatch, web_dir: Path):
    monkeypatch.setenv("LF_WEB_DIR", str(web_dir))
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main").app


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    d = tmp_path / "web"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX, encoding="utf-8")
    (d / "assets" / "app.js").write_text(ASSET, encoding="utf-8")
    app = _fresh_app(monkeypatch, d)
    with TestClient(app) as c:
        yield c
    sys.modules.pop("app.main", None)


def _yaml(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    assert path.is_file(), f"{path} is missing"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _service() -> dict:
    services = _yaml(COMPOSE).get("services") or {}
    assert "lf-manager" in services, "service lf-manager is missing"
    return services["lf-manager"]


def _priority(entry) -> int:
    return int((entry or {}).get("gw_priority", 0))


# ---------------------------------------------------------------- compose

def test_compose_nginx_network_outranks_lf_labs_for_the_default_route():
    nets = _yaml(COMPOSE).get("networks") or {}
    nginx_keys = [k for k, n in nets.items() if n and "LF_NGINX_NETWORK" in str(n.get("name", ""))]
    assert len(nginx_keys) == 1, "expected one network named from ${LF_NGINX_NETWORK}"
    svc_nets = _service().get("networks")
    assert isinstance(svc_nets, dict), "service networks must be a mapping"
    assert nginx_keys[0] in svc_nets and "lf-labs" in svc_nets
    assert _priority(svc_nets[nginx_keys[0]]) > _priority(svc_nets["lf-labs"])


def test_compose_rotates_json_file_logs_on_the_shared_disk():
    logging = _service().get("logging") or {}
    assert logging.get("driver") == "json-file"
    opts = logging.get("options") or {}
    assert opts.get("max-size"), "max-size is not set"
    assert opts.get("max-file"), "max-file is not set"


# ---------------------------------------------------------------- serving

@pytest.mark.parametrize("path", ["/healthz", "/api/health"])
def test_head_on_health_endpoints_answers_200_with_the_web_ui_enabled(client, path):
    r = client.head(path)
    assert r.status_code == 200, path


def test_index_html_is_sent_with_no_cache_like_the_root(client):
    root = client.get("/")
    assert root.headers.get("cache-control") == "no-cache"
    r = client.get("/index.html")
    assert r.status_code == 200
    assert "lf-fake-spa-72b" in r.text
    assert r.headers.get("cache-control") == "no-cache"


@pytest.mark.parametrize("path", ["/labs/linux-1.2", "/labs/sample/tasks/1.1"])
def test_deep_links_with_a_dotted_last_segment_fall_back_to_index_html(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert "lf-fake-spa-72b" in r.text, path
    assert "text/html" in r.headers["content-type"]


@pytest.mark.parametrize("path", ["/assets/old-123.js", "/favicon.ico"])
def test_a_missing_static_asset_is_a_404_not_html(client, path):
    r = client.get(path)
    assert r.status_code == 404, path
    assert "lf-fake-spa-72b" not in r.text, path
    assert "text/html" not in r.headers.get("content-type", ""), path


@pytest.mark.parametrize("suffix", ["term", "term/"])
def test_terminal_routes_answer_401_without_login_and_are_never_the_spa(client, suffix):
    r = client.get(f"/s/{uuid.uuid4()}/{suffix}")
    assert "lf-fake-spa-72b" not in r.text
    assert r.status_code == 401, r.status_code


# ---------------------------------------------------------------- CI contract

def _jobs() -> dict:
    jobs = _yaml(CI).get("jobs") or {}
    assert jobs, "no jobs in ci.yml"
    return jobs


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _manager_image_job() -> dict:
    for job in _jobs().values():
        text = repr(job.get("steps") or [])
        if "labs/manager/Dockerfile" in text and "lf-manager" in text:
            return job
    pytest.fail("no CI job builds labs/manager/Dockerfile as lf-manager")


def _steps_text(job: dict) -> str:
    return "\n".join(
        f"{s.get('run', '')}\n{s.get('with', '')}" for s in job.get("steps") or []
    )


def test_ci_builds_the_manager_dockerfile_with_context_labs():
    text = _steps_text(_manager_image_job())
    assert "labs/manager/Dockerfile" in text
    # `docker build ... labs` (context is the last token) or build-push-action `context: labs`
    assert re.search(r"docker build[^\n]*\s\.?/?labs/?\s*($|\n)", text, re.M) or re.search(
        r"'context': '\.?/?labs/?'", text
    ), "build context is not labs/"


def test_ci_manager_image_job_waits_for_the_docker_job():
    assert "docker" in _needs(_manager_image_job())


def test_ci_tags_the_manager_image_with_the_commit_sha_and_latest():
    text = _steps_text(_manager_image_job())
    assert "ghcr.io/shashkist/lf-manager:${{ github.sha }}" in text
    assert "ghcr.io/shashkist/lf-manager:latest" in text


def test_ci_pushes_the_manager_image_only_on_push_to_main():
    job = _manager_image_job()
    pushing = [
        s for s in job.get("steps") or []
        if re.search(r"docker push|docker login|build-push-action|login-action",
                     f"{s.get('run', '')} {s.get('uses', '')}")
    ]
    assert pushing, "no login or push step"
    for s in pushing:
        cond = str(s.get("if", ""))
        assert "refs/heads/main" in cond and "push" in cond, f"unguarded step: {s}"
    assert any("docker push" in s.get("run", "") for s in pushing)


def test_ci_validates_compose_without_printing_the_resolved_config():
    runs = [
        s.get("run", "") for job in _jobs().values() for s in job.get("steps") or []
    ]
    cmds = [
        line for run in runs for line in run.splitlines()
        if re.search(r"docker compose\b.*-f\s+\S*labs/deploy/compose\.yml\b.*\bconfig\b", line)
    ]
    assert cmds, "no step runs `docker compose -f labs/deploy/compose.yml config`"
    assert all(re.search(r"(\s--quiet\b|\s-q\b)", c) for c in cmds), cmds
