"""Exam for issue #568 (D149): the dashboard is built from the factory package at a pinned SHA.

Reads .github/workflows/factory-dashboard.yml only.

  * exactly one job runs `python -m factory_tools.dashboard build --repos docs/factory/repos.yaml
    --out site/`; that job checks out Shashkist/factory at a full 40-hex SHA into its own path,
    references no `secrets.` and has no write permission;
  * the job with the publish secrets (`secrets.` / `environment: production`) only downloads the
    artifact and copies files: no checkout, no pip, no local action, nothing from the factory path;
  * the publish job `needs` the build job and runs only on refs/heads/main.

Criteria 1 and 3 (live snapshot.json, repository tag on every row) are checked live after deploy,
not here. The removal of build_snapshot.py and the page files is a follow-up issue (D149).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
WORKFLOW = REPO / ".github" / "workflows" / "factory-dashboard.yml"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SECRETS = re.compile(r"\bsecrets\s*[.\[]")
FACTORY = "Shashkist/factory"


def wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def jobs() -> dict:
    return wf()["jobs"]


def run_text(job: dict) -> str:
    return "\n".join(
        str(s.get("run", "")).replace("\\\n", " ") for s in job.get("steps", [])
    )


def dump(job: dict) -> str:
    return yaml.safe_dump(job)


def env_name(job: dict) -> str:
    env = job.get("environment")
    return str(env.get("name", "")) if isinstance(env, dict) else str(env or "")


def build_job() -> tuple[str, dict]:
    found = [
        (n, j) for n, j in jobs().items()
        if "factory_tools.dashboard build" in run_text(j)
    ]
    assert len(found) == 1, "expected exactly one job that runs factory_tools.dashboard build"
    return found[0]


def publish_job() -> tuple[str, dict]:
    found = [
        (n, j) for n, j in jobs().items()
        if SECRETS.search(dump(j)) or env_name(j) == "production"
    ]
    assert len(found) == 1, "expected exactly one job with the publish secrets / production"
    return found[0]


def factory_checkout(job: dict) -> dict:
    found = [
        s for s in job.get("steps", [])
        if str(s.get("uses", "")).startswith("actions/checkout")
        and (s.get("with") or {}).get("repository") == FACTORY
    ]
    assert len(found) == 1, "the build job needs exactly one actions/checkout of Shashkist/factory"
    return found[0]


def factory_path() -> str:
    path = str((factory_checkout(build_job()[1]).get("with") or {}).get("path", ""))
    return path.strip("/ ")


# ---- the build job ----------------------------------------------------------

def test_exactly_one_job_runs_the_factory_dashboard_build_with_repos_and_out():
    _, job = build_job()
    line = next(ln for ln in run_text(job).splitlines() if "factory_tools.dashboard build" in ln)
    assert re.search(r"python3?\s+-m\s+factory_tools\.dashboard\s+build\b", line)
    assert re.search(r"--repos\s+docs/factory/repos\.yaml(\s|$)", line)
    assert re.search(r"--out\s+site/?(\s|$)", line)


def test_build_job_checks_out_the_factory_repository_at_a_full_40_hex_sha():
    _, job = build_job()
    ref = str((factory_checkout(job).get("with") or {}).get("ref", ""))
    assert FULL_SHA.match(ref), f"ref must be a full lowercase 40-hex SHA, got {ref!r}"


def test_factory_checkout_goes_into_its_own_path():
    path = factory_path()
    assert path and path not in (".", "site"), "the factory checkout needs its own path"
    for s in build_job()[1]["steps"]:
        if str(s.get("uses", "")).startswith("actions/checkout") and (
            (s.get("with") or {}).get("repository") != FACTORY
        ):
            assert str((s.get("with") or {}).get("path", ".")).strip("/ ") != path


def test_build_job_references_no_secrets_at_all():
    _, job = build_job()
    assert not SECRETS.search(dump(job))
    assert env_name(job) != "production"


def test_build_job_permissions_grant_no_write_scope():
    _, job = build_job()
    perms = job.get("permissions", wf().get("permissions"))
    assert perms is not None, "declare read-only permissions"
    assert perms != "write-all"
    if isinstance(perms, dict):
        assert "write" not in {str(v) for v in perms.values()}


def test_build_job_uploads_the_site_artifact():
    _, job = build_job()
    ups = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert ups and ups[0]["with"]["name"] == "factory-dashboard"


# ---- the publish job --------------------------------------------------------

def test_publish_job_is_a_different_job_from_the_build_job():
    assert build_job()[0] != publish_job()[0]


def test_publish_job_has_no_checkout_step():
    _, job = publish_job()
    assert not [s for s in job["steps"] if "checkout" in str(s.get("uses", ""))]


def test_publish_job_runs_nothing_from_the_factory_checkout():
    path = factory_path()
    assert path
    text = dump(publish_job()[1]).replace("/root/static/factory", "")
    assert "factory_tools" not in text
    assert FACTORY not in text
    assert not re.search(rf"(^|[\s'\"=/$]){re.escape(path)}(/|\s|$)", text)


def test_publish_job_installs_nothing_and_uses_no_local_action():
    _, job = publish_job()
    for s in job["steps"]:
        uses, run = str(s.get("uses", "")), str(s.get("run", ""))
        assert not uses.startswith("./"), "no local action in the secrets job"
        assert not uses.startswith("actions/setup-python")
        assert not re.search(r"\b(pip3?|uv|pipx|npm|poetry)\s+(install|add|run|sync)\b", run)
        assert "python" not in run, "the publish job runs no python"


def test_publish_job_only_downloads_the_artifact_and_copies_files():
    _, job = publish_job()
    uses = [str(s["uses"]).split("@")[0] for s in job["steps"] if "uses" in s]
    assert uses == ["actions/download-artifact"]


def test_publish_job_needs_the_build_job():
    needs = publish_job()[1].get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    assert build_job()[0] in needs


def test_publish_job_still_runs_only_on_main():
    job = publish_job()[1]
    assert "refs/heads/main" in str(job.get("if", ""))
    assert env_name(job) == "production"
