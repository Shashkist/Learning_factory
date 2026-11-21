# ruff: noqa: E501
"""Acceptance tests for issue #90 (D38): the post-deploy smoke runs inside the Deploy run, and
a failed `Server smoke` opens an incident. Static checks on the parsed workflows; nothing runs
on the server.

Interfaces the exam fixes:
  * server-smoke.yml: `on.workflow_call.inputs.from_deploy` (boolean, default false); jobs
    `smoke` (the checks) and one gate job (any name) listed in the `needs` of `smoke`.
  * deploy.yml: job `deploy` has the output `deployed` (`'true'` only when deploy.sh ran and
    succeeded); job `smoke` with `needs: deploy`, `if` on `needs.deploy.outputs.deployed`,
    `uses: ./.github/workflows/server-smoke.yml`, no `secrets: inherit` (#94: the called job gets
    its secrets from its own `environment: production`), `with.from_deploy: true`.
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
INCIDENT = WF_DIR / "incident.yml"


def _load(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    assert path.is_file(), f"{path.name} is missing"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(wf: dict) -> dict:
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True
    assert isinstance(on, dict), "needs an `on:` mapping"
    return on


def _group(job_or_wf: dict) -> str:
    c = job_or_wf.get("concurrency")
    if isinstance(c, dict):
        c = c.get("group")
    return str(c or "")


def _needs(job: dict) -> list[str]:
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


def _gate_job(wf: dict) -> tuple[str, dict]:
    jobs = wf["jobs"]
    assert "smoke" in jobs, "no `smoke` job"
    needs = _needs(jobs["smoke"])
    assert needs, "the smoke job must need a gate job that checks for a running Deploy"
    name = needs[0]
    assert name in jobs, f"smoke needs {name!r}, which is not a job"
    return name, jobs[name]


def _runs(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in job.get("steps") or [])


def _truthy(v) -> bool:
    return v is True or str(v).lower() == "true"


# ------------------------------------------------------------ server-smoke.yml: triggers


def test_smoke_workflow_can_be_called_with_a_from_deploy_boolean_defaulting_to_false():
    call = _on(_load(SMOKE)).get("workflow_call")
    assert isinstance(call, dict), "server-smoke.yml needs `on.workflow_call`"
    inp = (call.get("inputs") or {}).get("from_deploy")
    assert isinstance(inp, dict), "workflow_call needs the input `from_deploy`"
    assert inp.get("type") == "boolean"
    assert inp.get("default") in (False, "false")
    assert not _truthy(inp.get("required", False))


def test_smoke_workflow_has_no_workflow_run_trigger_any_more():
    assert "workflow_run" not in _on(_load(SMOKE))


def test_nightly_schedule_and_manual_dispatch_are_kept():
    on = _on(_load(SMOKE))
    assert [e.get("cron") for e in on.get("schedule") or []] == ["30 3 * * *"]
    check = ((on.get("workflow_dispatch") or {}).get("inputs") or {}).get("check") or {}
    # #67 removed the one-off liveness diagnostic (D30)
    assert set(check.get("options") or []) == {"isolation", "terminal"}
    assert check.get("default") == "isolation"


def test_a_called_run_runs_the_terminal_check():
    # a call passes no `check` input, so the fallback must be terminal (as for schedule)
    wf = _load(SMOKE)
    job = wf["jobs"].get("smoke") or {}
    env = {**(wf.get("env") or {}), **(job.get("env") or {})}
    selector = [str(v) for v in env.values() if "inputs.check" in str(v)]
    assert selector and any("terminal" in v for v in selector)
    assert "workflow_call" in _on(wf)


# ------------------------------------------------------------ server-smoke.yml: locking


def test_the_host_lock_is_not_on_the_workflow():
    assert "concurrency" not in _load(SMOKE)


def test_the_smoke_job_takes_the_host_lock_only_when_not_called_from_deploy():
    job = _load(SMOKE)["jobs"]["smoke"]
    assert "server-lab-host" in _group(job)
    assert "from_deploy" in _group(job), (
        "the group must depend on inputs.from_deploy so a called run never queues on the caller's lock"
    )
    conc = job["concurrency"]
    cancel = conc.get("cancel-in-progress") if isinstance(conc, dict) else None
    assert cancel in (False, None, "false")


def test_the_lock_group_of_a_called_run_is_not_server_lab_host():
    group = _group(_load(SMOKE)["jobs"]["smoke"])
    assert "server-lab-host" in group
    # the expression must select something else when from_deploy is true: a second literal
    # group or a run-unique value
    literals = re.findall(r"'([^']*)'", group)
    assert any(lit and "server-lab-host" not in lit for lit in literals) or "run_id" in group, (
        f"no alternative group for called runs: {group}"
    )


# ------------------------------------------------------------ server-smoke.yml: the gate


def test_a_gate_job_without_concurrency_runs_before_the_smoke():
    wf = _load(SMOKE)
    name, gate = _gate_job(wf)
    assert name != "smoke"
    assert "concurrency" not in gate, "the gate must not wait on the host lock"
    assert "concurrency" not in wf


def test_the_gate_does_not_apply_to_a_called_run():
    wf = _load(SMOKE)
    _, gate = _gate_job(wf)
    assert "from_deploy" in str(gate.get("if", "")), (
        "the gate must be skipped when inputs.from_deploy is true"
    )
    # and a skipped gate must not skip the smoke of a called run
    smoke_if = str(wf["jobs"]["smoke"].get("if", ""))
    assert smoke_if, "the smoke job needs an `if` that tolerates a skipped gate"
    assert "always()" in smoke_if or "skipped" in smoke_if or "from_deploy" in smoke_if


def test_the_gate_looks_for_queued_waiting_or_running_deploy_runs():
    _, gate = _gate_job(_load(SMOKE))
    text = _runs(gate)
    assert "deploy.yml" in text, "the gate must query the runs of deploy.yml"
    assert re.search(r"gh\s+(run\s+list|api)", text)
    for status in ("queued", "waiting", "in_progress"):
        assert status in text, f"the gate must look for Deploy runs that are {status}"


def test_the_gate_skips_the_smoke_with_a_log_line_when_a_deploy_is_pending():
    wf = _load(SMOKE)
    _, gate = _gate_job(wf)
    text = _runs(gate)
    assert re.search(r"echo\s+[\"'][^\"']*(skip|Deploy)[^\"']*[\"']", text, re.I), "no log line"
    assert "GITHUB_OUTPUT" in text, "the gate reports its verdict as an output"
    assert gate.get("outputs"), "the gate job needs an output the smoke job reads"
    smoke = wf["jobs"]["smoke"]
    assert "needs." in str(smoke.get("if", "")) or any(
        "needs." in str(s.get("if", "")) for s in smoke.get("steps") or []
    ), "the smoke must depend on the gate verdict"


def test_the_gate_token_is_read_only_and_gets_actions_read():
    wf = _load(SMOKE)
    _, gate = _gate_job(wf)
    perms = gate.get("permissions") or {}
    assert perms.get("actions") == "read", "the gate needs `actions: read` to list runs"
    assert "write" not in str(list(perms.values()))
    assert (wf.get("permissions") or {}) == {"contents": "read"}, (
        "workflow-level token stays contents: read"
    )
    assert "GH_TOKEN" in str(gate), "gh needs GH_TOKEN (through env, not a `${{ }}` in run)"
    assert "${{" not in _runs(gate)


def test_the_gate_does_not_use_the_production_environment_or_ssh():
    _, gate = _gate_job(_load(SMOKE))
    assert not gate.get("environment")
    assert "ssh" not in _runs(gate).lower()


# ------------------------------------------------------------ deploy.yml


def test_the_deploy_job_outputs_deployed():
    job = _load(DEPLOY)["jobs"]["deploy"]
    outputs = job.get("outputs") or {}
    assert "deployed" in outputs, "job `deploy` needs the output `deployed`"
    assert "steps." in str(outputs["deployed"])


def test_deployed_is_true_only_after_the_deploy_script_succeeded():
    job = _load(DEPLOY)["jobs"]["deploy"]
    steps = job["steps"]
    text = "\n".join(s.get("run", "") for s in steps)
    assert "deployed=true" in text, "no step sets deployed=true"
    assert "GITHUB_OUTPUT" in text
    t = text.index("deployed=true")
    assert t > text.index("deploy.sh"), "deployed=true must come after the deploy script ran"
    # the stale-sha skip (exit 0) happens before it, so a skip never reports a deploy
    assert t > text.index("no longer the head of main")
    for s in steps:
        if "deployed=true" in s.get("run", ""):
            cond = str(s.get("if", ""))
            assert "always()" not in cond and "failure()" not in cond
    holder = next(s for s in steps if "deployed=true" in s.get("run", ""))
    if "deploy.sh" in holder["run"]:
        run = holder["run"]
        between = run[run.index("deploy.sh") : run.index("deployed=true")]
        assert re.search(r'\$st|\$\?|-eq 0|= *0|\|\| exit|set -e', between), (
            "check that deploy.sh succeeded before writing deployed=true"
        )


def test_deployed_is_not_set_when_the_access_values_are_missing():
    job = _load(DEPLOY)["jobs"]["deploy"]
    holder = next(s for s in job["steps"] if "deployed=true" in s.get("run", ""))
    assert "steps.gate.outputs.ready" in str(holder.get("if", "")), (
        "the step that sets deployed=true must not run when the access values are missing"
    )


def test_deploy_has_a_smoke_job_that_calls_the_smoke_workflow_after_a_real_deploy():
    jobs = _load(DEPLOY)["jobs"]
    assert "smoke" in jobs, "deploy.yml needs a `smoke` job"
    smoke = jobs["smoke"]
    assert _needs(smoke) == ["deploy"]
    cond = str(smoke.get("if", "")).replace('"', "'")
    assert "needs.deploy.outputs.deployed" in cond and "'true'" in cond, cond
    assert smoke.get("uses") == "./.github/workflows/server-smoke.yml"
    assert smoke.get("secrets") != "inherit", "#94: no secrets: inherit, the callee uses its own environment"
    assert _truthy((smoke.get("with") or {}).get("from_deploy")), "with: from_deploy: true"
    assert "runs-on" not in smoke and "steps" not in smoke, "a called-workflow job has no steps"


def test_deploy_keeps_the_workflow_level_host_lock_and_the_production_environment():
    wf = _load(DEPLOY)
    conc = wf.get("concurrency")
    assert isinstance(conc, dict) and conc.get("group") == "server-lab-host"
    assert conc.get("cancel-in-progress") in (False, "false")
    env = wf["jobs"]["deploy"].get("environment")
    assert (env.get("name") if isinstance(env, dict) else env) == "production"
    assert "concurrency" not in wf["jobs"].get("smoke", {}), (
        "the called smoke runs under the Deploy lock"
    )


def test_the_called_smoke_gets_no_more_than_contents_read_and_actions_read():
    smoke = _load(DEPLOY)["jobs"].get("smoke") or {}
    perms = smoke.get("permissions")
    assert isinstance(perms, dict), "the smoke job must set its own permissions (no packages)"
    assert perms.get("contents") == "read"
    assert set(perms) <= {"contents", "actions"}
    assert all(v == "read" for v in perms.values())


# ------------------------------------------------------------ incident.yml


def test_a_failed_server_smoke_run_opens_an_incident():
    run = _on(_load(INCIDENT)).get("workflow_run")
    assert isinstance(run, dict)
    assert "Server smoke" in (run.get("workflows") or [])
    assert "completed" in (run.get("types") or [])
    assert _load(SMOKE).get("name") == "Server smoke", "incident.yml names the workflow by `name:`"


def test_the_incident_watch_list_still_has_the_other_workflows():
    names = _on(_load(INCIDENT))["workflow_run"]["workflows"]
    for expected in ("CI", "Dashboard CI", "Factory dashboard", "Deploy", "Server bootstrap"):
        assert expected in names
    assert "Smoke" not in names, "`Smoke` is not a workflow name; it was replaced by `Server smoke`"
