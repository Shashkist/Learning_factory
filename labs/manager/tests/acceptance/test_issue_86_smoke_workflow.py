# ruff: noqa: E501
"""Acceptance tests for issue #86, part 3: `.github/workflows/server-smoke.yml` with the new
`terminal` check (D36). Static checks on the parsed workflow; nothing runs on the server.

Shape the exam expects (D36):
  * `workflow_dispatch` input `check` gains `terminal` (default stays `isolation`); the workflow
    also runs on `schedule` 03:30 UTC. Events without inputs run the terminal check.
    D38 (#90) moved the post-deploy run into the Deploy workflow (`workflow_call`, see
    test_issue_90_smoke_in_deploy.py); the `workflow_run` trigger of Deploy is gone.
  * environment `production`, concurrency group `server-lab-host` without cancel (D38: on the
    `smoke` job, not on the workflow).
  * a per-run class password is generated with `openssl rand -hex 24` into a shell variable and
    only ever reaches `smoke-student` on stdin (through `docker exec -i lf-manager ...` over ssh)
    and the client through its environment: never on a command line, never echoed.
  * separate steps, in this order: smoke-student (enable), the client
    `labs/deploy/smoke/terminal.py`, a check that container `lab-<session id>` is gone (polled
    for about 30 s), and `smoke-student --disable` in a step with `if: always()`.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "server-smoke.yml"
DEPLOY = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def _wf() -> dict:
    yaml = pytest.importorskip("yaml")
    assert WORKFLOW.is_file(), "server-smoke.yml is missing"
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(wf: dict) -> dict:
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True
    assert isinstance(on, dict), "server-smoke.yml needs an `on:` mapping"
    return on


def _job(wf: dict) -> dict:
    # D38: the workflow has a small gate job before the smoke; the checks live in job `smoke`
    jobs = wf.get("jobs") or {}
    assert "smoke" in jobs, "server-smoke.yml needs a job named `smoke`"
    return jobs["smoke"]


def _steps(wf: dict) -> list[dict]:
    return list(_job(wf).get("steps") or [])


def _runs(wf: dict) -> list[str]:
    return [s.get("run", "") for s in _steps(wf)]


def _all_run(wf: dict) -> str:
    return "\n".join(_runs(wf))


def _step_index(wf: dict, pattern: str, *, skip: str | None = None) -> int:
    for i, s in enumerate(_steps(wf)):
        run = s.get("run", "")
        if re.search(pattern, run) and not (skip and re.search(skip, run)):
            return i
    raise AssertionError(f"no step runs something matching {pattern!r}")


# ---------------------------------------------------------------- triggers


def test_terminal_is_a_manual_option_and_the_default_stays_isolation():
    inputs = (_on(_wf()).get("workflow_dispatch") or {}).get("inputs") or {}
    check = inputs.get("check") or {}
    options = check.get("options") or []
    assert {"isolation", "liveness", "terminal"} <= set(options)
    assert check.get("default") == "isolation"


def test_it_no_longer_runs_from_a_workflow_run_trigger():
    # D38: the post-deploy smoke is a job of the Deploy run (workflow_call), so a separate
    # workflow_run trigger is gone: it ran after skipped deploys and queued behind the deploy lock
    on = _on(_wf())
    assert "workflow_run" not in on, "the workflow_run trigger must be removed (D38)"
    assert "workflow_call" in on and "workflow_dispatch" in on and "schedule" in on


def test_a_deploy_that_did_not_deploy_does_not_start_the_smoke():
    # the guard moved to the caller: the smoke job of deploy.yml runs only when the deploy
    # really deployed
    import yaml

    deploy = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))
    smoke = (deploy.get("jobs") or {}).get("smoke") or {}
    assert smoke.get("needs") == "deploy" or smoke.get("needs") == ["deploy"]
    assert "deployed" in str(smoke.get("if", ""))
    assert re.search(r"^name:\s*Deploy\s*$", DEPLOY.read_text(encoding="utf-8"), re.M)


def test_it_runs_nightly_at_0330_utc():
    schedule = _on(_wf()).get("schedule")
    assert isinstance(schedule, list) and schedule
    assert [{"cron": "30 3 * * *"}] == [{"cron": e.get("cron")} for e in schedule]


def test_events_other_than_manual_dispatch_run_the_terminal_check():
    wf = _wf()
    values = []
    for s in _steps(wf):
        values += [str(v) for v in (s.get("env") or {}).values()]
    values += [str(v) for v in (_job(wf).get("env") or {}).values()]
    selector = [v for v in values if "inputs.check" in v]
    assert selector, "the check to run must come from inputs.check"
    assert any("terminal" in v for v in selector), (
        "without an input (workflow_run, schedule) the check must default to terminal"
    )


def test_the_dispatch_default_check_is_not_made_terminal():
    # the input default is isolation (see above); the fallback to terminal is only for events with
    # no inputs, so a manual dispatch without a choice still runs isolation
    inputs = (_on(_wf()).get("workflow_dispatch") or {}).get("inputs") or {}
    assert inputs["check"]["default"] == "isolation"


# ---------------------------------------------------------------- production host rules


def test_production_environment_and_the_shared_host_lock_are_kept():
    wf = _wf()
    job = _job(wf)
    assert job.get("environment") == "production" or (job.get("environment") or {}).get(
        "name"
    ) == "production"
    # D38: the lock is on the smoke job (not the workflow) and only applies when not called
    assert not wf.get("concurrency"), "the workflow-level concurrency moved to the smoke job (D38)"
    conc = job.get("concurrency")
    if isinstance(conc, str):
        conc = {"group": conc}
    assert conc and "server-lab-host" in str(conc.get("group"))
    assert conc.get("cancel-in-progress") in (False, None, "false")


def test_the_token_can_only_read_contents():
    perms = _wf().get("permissions")
    assert perms == {"contents": "read"}


def test_existing_checks_still_map_to_their_scripts_and_the_key_is_removed_always():
    wf = _wf()
    text = _all_run(wf)
    assert "labs/deploy/smoke/isolation.sh" in text
    assert "labs/deploy/smoke/liveness.sh" in text
    cleanup = [s for s in _steps(wf) if "smoke_key" in s.get("run", "") and "rm" in s.get("run", "")]
    assert cleanup and "always()" in str(cleanup[-1].get("if", ""))


def test_no_expression_is_expanded_inside_a_run_script():
    for run in _runs(_wf()):
        assert "${{" not in run, "values must reach scripts through env:, never `${{ }}` in run"


def test_ssh_secrets_are_still_passed_through_env_only():
    wf = _wf()
    steps = [s for s in _steps(wf) if "SSH_PRIVATE_KEY" in str(s.get("env", {}))]
    assert steps
    for s in steps:
        assert "secrets.SSH_PRIVATE_KEY" in str(s["env"].get("SSH_PRIVATE_KEY"))


# ---------------------------------------------------------------- the terminal check


def test_the_smoke_student_is_enabled_through_the_manager_container_with_the_password_on_stdin():
    text = _all_run(_wf())
    enable = [
        ln for ln in text.splitlines() if "smoke-student" in ln and "--disable" not in ln
    ]
    assert enable, "no step runs `app.cli smoke-student`"
    joined = " ".join(enable)
    assert "docker exec -i lf-manager" in joined
    assert "python -m app.cli smoke-student" in joined


def test_the_password_is_generated_per_run_with_openssl_rand_hex_24():
    assert re.search(r"\w+=\"?\$\(openssl rand -hex 24\)\"?", _all_run(_wf())), (
        "generate the class password with `openssl rand -hex 24` into a variable"
    )


def _password_var(text: str) -> str:
    m = re.search(r"(\w+)=\"?\$\(openssl rand -hex 24\)", text)
    assert m, "generate the class password with `openssl rand -hex 24` into a variable"
    return m.group(1)


def test_the_password_never_appears_on_a_command_line_and_is_never_echoed():
    text = _all_run(_wf())
    var = _password_var(text)
    use = re.compile(r"\$\{?" + re.escape(var) + r"\b")
    for ln in text.splitlines():
        if not use.search(ln) and var not in ln:
            continue
        stripped = ln.strip()
        if "openssl rand -hex 24" in ln and "=" in ln:
            continue  # the assignment
        if "::add-mask::" in ln or "GITHUB_ENV" in ln or "GITHUB_OUTPUT" in ln:
            continue
        if "<<<" in ln:
            head = ln.split("<<<", 1)[0]
            assert not use.search(head), f"the password is an argument: {stripped}"
            continue
        if "|" in ln and use.search(ln.split("|", 1)[0]) and not use.search(ln.split("|", 1)[1]):
            head = ln.split("|", 1)[0].strip()
            assert re.match(r"(printf|echo)\b", head), f"the password feeds a pipe from {head!r}"
            continue
        assert re.match(rf"(export\s+)?\w*{re.escape(var)}\w*=", stripped) or re.match(
            r"\w+=\"?\$\{?" + re.escape(var), stripped
        ) or ("SMOKE_PASSWORD=" in ln and "terminal.py" in ln and ln.index("SMOKE_PASSWORD=") < ln.index("terminal.py")), (
            f"the password may only be generated, masked, piped on stdin or put in an environment: {stripped}"
        )


def test_the_password_is_masked_in_the_logs():
    assert "::add-mask::" in _all_run(_wf())


def test_the_password_is_never_a_secret_or_a_stored_value():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"secrets\.\w*(SMOKE|PASS)\w*", text, re.I)


def test_the_client_runs_from_the_repo_with_credentials_in_its_environment():
    wf = _wf()
    text = _all_run(wf)
    assert "labs/deploy/smoke/terminal.py" in text
    line = next(ln for ln in text.splitlines() if "terminal.py" in ln)
    assert "SMOKE_ID" in text and "SMOKE_PASSWORD" in text
    # the credentials are never arguments of the client
    after = line.split("terminal.py", 1)[1]
    assert "SMOKE_PASSWORD" not in after and "SMOKE_ID" not in after


def test_steps_run_in_order_enable_client_gone_check_then_disable():
    wf = _wf()
    enable = _step_index(wf, r"app\.cli smoke-student", skip=r"--disable")
    client = _step_index(wf, r"terminal\.py")
    disable = _step_index(wf, r"smoke-student\s+--disable")
    assert enable <= client < disable
    text = "\n".join(_runs(wf))
    assert text.index("terminal.py") < text.rindex("--disable")


def test_disable_runs_in_its_own_step_that_always_runs():
    wf = _wf()
    idx = _step_index(wf, r"smoke-student\s+--disable")
    step = _steps(wf)[idx]
    assert "always()" in str(step.get("if", "")), "the --disable step needs `if: always()`"
    assert "docker exec" in step["run"] and "lf-manager" in step["run"]
    assert "terminal.py" not in step["run"]


def test_disable_needs_no_password():
    wf = _wf()
    step = _steps(wf)[_step_index(wf, r"smoke-student\s+--disable")]
    assert "openssl rand" not in step["run"]
    assert "<<<" not in step["run"]


def test_it_checks_that_the_lab_container_is_gone_after_the_client():
    text = "\n".join(_runs(_wf()))
    tail = text.split("terminal.py", 1)[1]
    assert re.search(r"lab-\$\{?\w+", tail), "no check of container lab-<session id> after the client"
    assert re.search(r"docker\s+(ps|inspect|container)", tail)
    assert re.search(r"\b(30|seq 1 30|seq 30|\{1\.\.30\}|\{1\.\.15\})\b", tail), (
        "poll for about 30 seconds"
    )
    assert "sleep" in tail


def test_a_container_that_is_still_there_fails_the_run():
    tail = "\n".join(_runs(_wf())).split("terminal.py", 1)[1]
    assert "exit 1" in tail or "exit \"$" in tail or "exit $" in tail
