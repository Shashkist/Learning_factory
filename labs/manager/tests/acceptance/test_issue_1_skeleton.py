"""Acceptance tests for issue #1: repo skeleton and CI pipeline."""
import re
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load(name: str) -> dict:
    yaml = pytest.importorskip("yaml")
    path = WORKFLOWS / name
    assert path.is_file(), f"{path} is missing"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(wf: dict) -> dict:
    # PyYAML parses the bare key `on` as boolean True.
    trig = wf.get("on", wf.get(True))
    if isinstance(trig, str):
        return {trig: None}
    if isinstance(trig, list):
        return {t: None for t in trig}
    return trig or {}


def test_healthz_returns_ok_true():
    from fastapi.testclient import TestClient

    from app.main import app

    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_ci_workflow_runs_on_pull_requests_and_main():
    trig = _triggers(_load("ci.yml"))
    assert "pull_request" in trig
    assert "push" in trig
    assert "main" in ((trig["push"] or {}).get("branches") or [])


@pytest.mark.parametrize("word", ["manager", "web", "docker", "exam"])
def test_ci_workflow_defines_job(word):
    jobs = _load("ci.yml").get("jobs", {})
    assert any(word in j.lower() for j in jobs), f"no job containing '{word}' in {list(jobs)}"


def test_ci_docker_job_installs_gvisor():
    wf = _load("ci.yml")
    job = next(v for k, v in wf["jobs"].items() if "docker" in k.lower())
    assert "ubuntu" in str(job.get("runs-on", ""))
    assert "runsc" in str(job)


def test_ci_docker_job_runs_docker_marked_tests():
    wf = _load("ci.yml")
    job = next(v for k, v in wf["jobs"].items() if "docker" in k.lower())
    assert "docker" in str(job.get("steps")) and "-m" in str(job.get("steps"))


def test_incident_workflow_reacts_to_ci_deploy_smoke_failures():
    wf = _load("incident.yml")
    run = _triggers(wf).get("workflow_run")
    assert run is not None, "incident.yml must trigger on workflow_run"
    names = [str(n).lower() for n in run.get("workflows", [])]
    for expected in ("ci", "deploy", "smoke"):
        assert any(expected in n for n in names), f"{expected} not watched: {names}"
    assert "completed" in run.get("types", [])


def test_incident_workflow_applies_incident_labels():
    text = (WORKFLOWS / "incident.yml").read_text(encoding="utf-8")
    # D166: the reusable layout calls the factory's incident.yml (pinned), which applies
    # these labels itself (docs/ci.md); the local layout names them in this file
    jobs = _load("incident.yml")["jobs"]
    pinned = re.compile(r"^Shashkist/factory/\.github/workflows/incident\.yml@[0-9a-f]{40}$")
    called = [j for j in jobs.values() if pinned.match(str(j.get("uses", "")))]
    if called:
        # a real job (not a comment) calls the pinned workflow; its own `uses:` line carries
        # the version comment
        assert re.search(r"^[ \t-]*uses:[ \t]*Shashkist/factory/\.github/workflows/incident\.yml"
                         r"@[0-9a-f]{40}[ \t]+#[ \t]*v\d", text, re.M)
        return
    for label in ("type:incident", "priority:p0", "status:ready"):
        assert label in text
