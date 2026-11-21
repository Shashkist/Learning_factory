# ruff: noqa: E501
"""Acceptance tests for issue #94 (D34, D36, D38): follow-ups of the smoke-in-Deploy change.
Static checks on the parsed workflows; nothing runs on the server.

  1. The smoke job's lock expression falls back to exactly `server-lab-host` for scheduled and
     manual runs (a near miss such as `server-lab-host-smoke` would let a nightly smoke run
     during a deploy or bootstrap).
  2. deploy.yml calls the smoke workflow with a named secrets mapping, not `secrets: inherit`.
     Every secret server-smoke.yml reads is declared (required) under `on.workflow_call.secrets`
     and passed by the same name from deploy.yml (a secret that is not passed resolves to an empty
     string without an error, even when the called job sets `environment:`). Nothing else is passed.
     SERVER_HOST_KEY is a repo variable (`vars.`), not a secret.
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


def _read() -> set:
    text = SMOKE.read_text(encoding="utf-8")
    return set(re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)", text)) - IMPLICIT


def _passed() -> dict:
    passed = _load(DEPLOY)["jobs"]["smoke"].get("secrets")
    assert isinstance(passed, dict) and passed, f"secrets must be a named mapping, not {passed!r}"
    return passed


def test_deploy_does_not_pass_secrets_inherit_to_the_smoke_call():
    smoke = _load(DEPLOY)["jobs"]["smoke"]
    assert smoke.get("secrets") != "inherit", "secrets: inherit hands over every repo secret"
    assert isinstance(smoke.get("secrets"), dict), "secrets must be a named mapping"


def test_the_smoke_workflow_reads_at_least_the_three_server_secrets():
    assert {"SSH_PRIVATE_KEY", "SERVER_HOST", "SERVER_USER"} <= _read()


def test_every_secret_the_smoke_workflow_reads_is_declared_required_under_workflow_call():
    declared = ((_on(_load(SMOKE)).get("workflow_call") or {}).get("secrets")) or {}
    assert isinstance(declared, dict), "on.workflow_call.secrets must be a mapping"
    missing = _read() - set(declared)
    assert not missing, f"read but not declared under on.workflow_call.secrets: {sorted(missing)}"
    for name in _read():
        spec = declared[name]
        assert isinstance(spec, dict) and spec.get("required") is True, f"{name} must be required: true"


def test_deploy_passes_every_secret_the_smoke_workflow_reads_by_the_same_name():
    passed = _passed()
    for name in _read():
        assert name in passed, f"deploy.yml does not pass {name}"
        value = str(passed[name]).strip()
        assert re.fullmatch(r"\$\{\{\s*secrets\." + name + r"\s*\}\}", value), (
            f"{name} must be passed as ${{{{ secrets.{name} }}}}, got {value!r}"
        )


def test_deploy_passes_no_secret_the_smoke_workflow_does_not_read():
    extra = set(_passed()) - _read()
    assert not extra, f"passed but not read by the callee: {sorted(extra)}"


def test_the_smoke_job_keeps_the_production_environment_that_supplies_the_secrets():
    env = _load(SMOKE)["jobs"]["smoke"].get("environment")
    assert (env.get("name") if isinstance(env, dict) else env) == "production"


def test_the_server_host_key_is_read_as_a_variable_not_a_secret():
    text = SMOKE.read_text(encoding="utf-8")
    assert "secrets.SERVER_HOST_KEY" not in text
