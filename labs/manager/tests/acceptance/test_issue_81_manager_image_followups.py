"""Acceptance tests for issue #81: manager image follow-ups.

Criterion 4 choice: the `concurrency` group option (not "always deploy LF_MANAGER_TAG=<sha>").
Both image jobs: per-ref group, no cancel, :latest behind the ci_paths guard (D154).
All non-docker.
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

@pytest.mark.parametrize("path", ["/", "/index.html", "/index.html/", "/%2E/index.html"])
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
        run = s["run"].replace(" ", "")
        assert "${{secrets." not in run and "${{github.token" not in run, s["run"]
        assert "GHCR_TOKEN" in s["run"], "the script must read $GHCR_TOKEN"


# ---- criterion 4 per D154: per-ref group, no cancel, :latest guarded, :<sha> not

def _pushes(name: str):
    """(step, run script, push lines) for every step of the job that runs `docker push`."""
    out = []
    for s in _image_job(name).get("steps") or []:
        run = str(s.get("run", ""))
        lines = [ln for ln in run.splitlines() if re.search(r"docker push", ln)]
        if lines:
            out.append((s, run, lines))
    assert out, f"{name}: no `docker push` step"
    return out


def _is_guard(text: str) -> bool:
    return bool(re.search(r"ci_paths.py.*deploys", str(text), re.S))


@pytest.mark.parametrize("name", ["image-manager", "image-term"])
def test_image_job_group_is_per_ref_and_never_cancels(name):
    conc = _image_job(name).get("concurrency")
    assert isinstance(conc, dict), f"{name}: concurrency must be a mapping"
    assert "cancel-in-progress" in conc, f"{name}: cancel-in-progress must be set explicitly"
    assert conc["cancel-in-progress"] is False, conc
    assert "github.ref" in str(conc.get("group")), conc


_SHA_TAG = re.compile(
    r":\s*[\"']?(\$\{?SHA\}?|\$\{?GITHUB_SHA\}?|\$\{\{\s*github\.sha\s*\}\})[\"']?(\s|$)"
)


def _is_sha_push(ln: str) -> bool:
    """A push whose tag is explicitly the SHA, and nothing that fans out to other tags."""
    if re.search(r"(^|\s)(-a|--all-tags)(\s|$)", ln) or ":latest" in ln:
        return False
    return bool(_SHA_TAG.search(ln))


def _is_latest_like(ln: str) -> bool:
    """Every push that is not explicitly the SHA tag may move :latest (named, -a, untagged)."""
    return not _is_sha_push(ln)


@pytest.mark.parametrize("name", ["image-manager", "image-term"])
def test_latest_is_pushed_only_behind_the_ci_paths_deploys_guard(name):
    latest = [
        (s, run, ln)
        for s, run, lines in _pushes(name)
        for ln in lines
        if _is_latest_like(ln)
    ]
    assert latest, f"{name}: no `docker push ...:latest`"
    for _s, run, ln in latest:
        before = run[: run.index(ln)]
        assert _is_guard(before), f"{name}: {ln.strip()!r} may move :latest, no deploys guard"
        assert ln != ln.lstrip(), f"{name}: {ln.strip()!r} may move :latest outside the conditional"


@pytest.mark.parametrize("name", ["image-manager", "image-term"])
def test_sha_tag_is_pushed_unconditionally(name):
    sha = [
        (s, run, ln)
        for s, run, lines in _pushes(name)
        for ln in lines
        if _is_sha_push(ln)
    ]
    assert sha, f"{name}: no `docker push` of the :<sha> tag"
    for s, run, ln in sha:
        assert not _is_guard(s.get("if", "")), f"{name}: the :<sha> push step is gated by the guard"
        assert ln == ln.lstrip(), f"{name}: the :<sha> push is nested in a conditional: {ln!r}"
        if "ci_paths" in run:
            assert run.index("ci_paths") > run.index(ln), f"{name}: guard runs before sha push"
