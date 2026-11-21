# ruff: noqa: E501
"""Acceptance tests for issue #94 (D34, D36, D38): follow-ups of the smoke-in-Deploy change.
Static checks on the parsed workflows; nothing runs on the server.

  1. The smoke job's lock expression falls back to exactly `server-lab-host` for scheduled and
     manual runs (a near miss such as `server-lab-host-smoke` would let a nightly smoke run
     during a deploy or bootstrap).
  2. deploy.yml does not hand every repo secret to the called smoke workflow (`secrets: inherit`).
     The callee gets SSH_PRIVATE_KEY, SERVER_HOST and SERVER_USER from its own
     `environment: production`; SERVER_HOST_KEY is a repo variable (`vars.`), not a secret.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
WF_DIR = REPO_ROOT / ".github" / "workflows"
SMOKE = WF_DIR / "server-smoke.yml"
DEPLOY = WF_DIR / "deploy.yml"

# Secrets that live in the `production` environment (OWNER.md O8, D34): a called job with
# `environment: production` resolves them itself, so they need not be passed.
ENV_SECRETS = {"SSH_PRIVATE_KEY", "SERVER_HOST", "SERVER_USER"}
IMPLICIT = {"GITHUB_TOKEN"}


def _load(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    assert path.is_file(), f"{path.name} is missing"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(wf: dict) -> dict:
    on = wf.get("on", wf.get(True))
    assert isinstance(on, dict)
    return on


def _group_expr(job: dict) -> str:
    c = job.get("concurrency")
    if isinstance(c, dict):
        c = c.get("group")
    assert c, "the smoke job has no concurrency group"
    return str(c).strip()


def test_the_lock_fallback_literal_is_exactly_server_lab_host():
    expr = _group_expr(_load(SMOKE)["jobs"]["smoke"])
    inner = re.fullmatch(r"\$\{\{(.*)\}\}", expr, re.S)
    body = inner.group(1).strip() if inner else None
    if body is None:
        assert expr == "server-lab-host", f"plain group must be server-lab-host: {expr!r}"
        return
    m = re.search(r"\|\|\s*'([^']*)'\s*$", body)
    assert m, f"the group expression must end in `|| '<fallback>'`: {expr!r}"
    assert m.group(1) == "server-lab-host", (
        f"the fallback for scheduled and manual runs must equal server-lab-host, got {m.group(1)!r}"
    )


def test_the_called_run_still_gets_a_group_that_is_not_the_host_lock():
    expr = _group_expr(_load(SMOKE)["jobs"]["smoke"])
    assert "from_deploy" in expr
    literals = re.findall(r"'([^']*)'", expr)
    assert any(lit and "server-lab-host" not in lit for lit in literals) or "run_id" in expr


def test_deploy_does_not_pass_secrets_inherit_to_the_smoke_call():
    smoke = _load(DEPLOY)["jobs"]["smoke"]
    assert smoke.get("secrets") != "inherit", "secrets: inherit hands over every repo secret"


def test_every_secret_the_deploy_call_passes_is_declared_by_the_smoke_workflow():
    passed = _load(DEPLOY)["jobs"]["smoke"].get("secrets")
    if passed is None:
        return
    assert isinstance(passed, dict), f"secrets must be omitted or a named mapping, got {passed!r}"
    declared = ((_on(_load(SMOKE)).get("workflow_call") or {}).get("secrets")) or {}
    undeclared = set(passed) - set(declared)
    assert not undeclared, f"passed but not declared under on.workflow_call.secrets: {sorted(undeclared)}"


def test_every_non_environment_secret_the_smoke_workflow_reads_is_declared_or_passed():
    text = SMOKE.read_text(encoding="utf-8")
    read = set(re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)", text)) - IMPLICIT
    declared = set(((_on(_load(SMOKE)).get("workflow_call") or {}).get("secrets")) or {})
    passed = _load(DEPLOY)["jobs"]["smoke"].get("secrets")
    passed = set(passed) if isinstance(passed, dict) else set()
    missing = read - ENV_SECRETS - declared - passed
    assert not missing, f"secrets read but neither environment, declared nor passed: {sorted(missing)}"


def test_the_smoke_job_keeps_the_production_environment_that_supplies_the_secrets():
    env = _load(SMOKE)["jobs"]["smoke"].get("environment")
    assert (env.get("name") if isinstance(env, dict) else env) == "production"


def test_the_server_host_key_is_read_as_a_variable_not_a_secret():
    text = SMOKE.read_text(encoding="utf-8")
    assert "secrets.SERVER_HOST_KEY" not in text
