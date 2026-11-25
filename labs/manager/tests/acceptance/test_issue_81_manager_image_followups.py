"""Acceptance tests for issue #81: manager image follow-ups.

Criterion 4 choice: the `concurrency` group option (not "always deploy LF_MANAGER_TAG=<sha>").
Both image jobs must carry their own job-level `concurrency` group. All non-docker.
"""
import importlib
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
INDEX = "<!doctype html><html><body><div id=root>lf-fake-spa-81</div></body></html>"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    d = tmp_path / "web"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX, encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log('lf-81');", encoding="utf-8")
    monkeypatch.setenv("LF_WEB_DIR", str(d))
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    sys.modules.pop("app.main", None)
    app = importlib.import_module("app.main").app
    with TestClient(app) as c:
        yield c
    sys.modules.pop("app.main", None)


def _jobs() -> dict:
    yaml = pytest.importorskip("yaml")
    assert CI.is_file(), f"{CI} is missing"
    jobs = (yaml.safe_load(CI.read_text(encoding="utf-8")) or {}).get("jobs") or {}
    assert jobs, "no jobs in ci.yml"
    return jobs


def _image_job(name: str) -> dict:
    jobs = _jobs()
    assert name in jobs, f"job {name} is missing"
    return jobs[name]


# ---- criterion 1: every compose config line is quiet

def test_every_docker_compose_config_line_in_ci_is_quiet():
    lines = [
        line
        for job in _jobs().values()
        for s in job.get("steps") or []
        for line in str(s.get("run", "")).splitlines()
        if re.search(r"docker compose\b.*\bconfig\b", line)
    ]
    assert lines, "no `docker compose ... config` line in ci.yml"
    bad = [c for c in lines if not re.search(r"(\s--quiet\b|\s-q\b)", c)]
    assert not bad, bad


# ---- criterion 2: no-cache keyed on the resolved path

@pytest.mark.parametrize("path", ["/", "/index.html", "/index.html/", "/./index.html"])
def test_index_html_in_any_spelling_is_no_cache(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert "lf-fake-spa-81" in r.text, path
    assert r.headers.get("cache-control") == "no-cache", path


@pytest.mark.parametrize("path", ["/labs", "/labs/linux-1.2", "/some/unknown/route"])
def test_spa_fallbacks_are_no_cache(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert "lf-fake-spa-81" in r.text, path
    assert r.headers.get("cache-control") == "no-cache", path


def test_an_existing_asset_is_not_forced_to_no_cache(client):
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") != "no-cache"


# ---- criterion 3: static-extension 404 only for top-level files and assets/

@pytest.mark.parametrize("path", ["/assets/missing.js", "/missing.js", "/favicon.ico"])
def test_missing_top_level_and_assets_files_stay_404(client, path):
    r = client.get(path)
    assert r.status_code == 404, path
    assert "lf-fake-spa-81" not in r.text, path


@pytest.mark.parametrize(
    "path",
    ["/labs/node.js", "/labs/x/tasks/config.json", "/labs/a.b/tasks/1.1.css"],
)
def test_dotted_deep_links_under_spa_routes_return_the_ui(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert "lf-fake-spa-81" in r.text, path
    assert "text/html" in r.headers["content-type"], path


# ---- criteria 4 and 5: image jobs in ci.yml

@pytest.mark.parametrize("name", ["image-manager", "image-term"])
def test_image_job_has_its_own_concurrency_group(name):
    conc = _image_job(name).get("concurrency")
    assert conc, f"{name} has no job-level concurrency"
    group = conc.get("group") if isinstance(conc, dict) else conc
    assert group and str(group).strip(), f"{name}: empty concurrency group"


def test_image_manager_and_image_term_use_different_concurrency_groups():
    groups = []
    for n in ("image-manager", "image-term"):
        c = _image_job(n).get("concurrency")
        groups.append(str(c.get("group") if isinstance(c, dict) else c))
    assert groups[0] != groups[1], groups


@pytest.mark.parametrize("name", ["image-manager", "image-term"])
def test_ghcr_login_reads_the_token_from_env_not_from_the_script(name):
    logins = [
        s for s in _image_job(name).get("steps") or []
        if "docker login" in str(s.get("run", "")) and "ghcr.io" in str(s.get("run", ""))
    ]
    assert logins, f"{name}: no GHCR login step"
    for s in logins:
        env = s.get("env") or {}
        assert str(env.get("GHCR_TOKEN", "")).replace(" ", "") == "${{secrets.GITHUB_TOKEN}}", env
        assert "${{ secrets." not in s["run"] and "${{secrets." not in s["run"], s["run"]
        assert "GHCR_TOKEN" in s["run"], "the script must read $GHCR_TOKEN"
