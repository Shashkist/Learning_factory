"""Exam for issue #25: deploy secrets live in a main-only environment; the host key is pinned.

CONTRACT (the builder works from this)

Every file in .github/workflows/ is held to the rules, so future deploy workflows are too:
  * a job that references secrets.SSH_PRIVATE_KEY, secrets.SERVER_HOST or secrets.SERVER_USER
    (in steps or env) declares `environment: production` (string, or `{name: production}`);
  * a job that calls a local reusable workflow (`uses: ./.github/workflows/<file>`) is fine when
    that file exists and each of its secret-using jobs declares production (one at least);
  * no `ssh-keyscan`, no `StrictHostKeyChecking=no`, no `accept-new`.

.github/workflows/factory-dashboard.yml
  * job `snapshot` (the one that runs build_snapshot.py) never references those secrets;
  * a separate publish job (the one that writes to /root/static/factory):
      - `environment: production`, `needs:` the snapshot job,
      - job-level `if:` containing `refs/heads/main`,
      - a step `uses: actions/download-artifact` with `name: factory-dashboard`,
      - env `SERVER_HOST_KEY: ${{ vars.SERVER_HOST_KEY }}`
        (never `vars.`/`secrets.` inline in run:),
      - run writes known_hosts from "$SERVER_HOST_KEY" combined with "$SERVER_HOST"
        (one line: `<host> <type> <base64>`), on a single line that mentions known_hosts,
      - after the "secrets not set -> skip" check (exit 0, kept from #19), a separate
        `if [ -z "$SERVER_HOST_KEY" ]; then ... exit 1; fi` (non-zero, no `exit 0` inside),
      - the `if: always()` step that removes the key lives in this job.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
WORKFLOWS = REPO / ".github" / "workflows"
DASHBOARD = WORKFLOWS / "factory-dashboard.yml"
SECRET_RE = re.compile(r"secrets\.(SSH_PRIVATE_KEY|SERVER_HOST|SERVER_USER)\b")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def workflow_files() -> list[Path]:
    files = sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))
    assert files, "no workflow files found"
    return files


def env_name(job: dict) -> str | None:
    e = job.get("environment")
    if isinstance(e, dict):
        e = e.get("name")
    return e


def jobs() -> dict:
    return load(DASHBOARD)["jobs"]


def snapshot_job() -> tuple[str, dict]:
    found = [
        (n, j) for n, j in jobs().items()
        # D149 (#568): the snapshot job runs the factory package now.
        if any("factory_tools.dashboard build" in str(s.get("run", "")) for s in j.get("steps", []))
    ]
    assert len(found) == 1, "expected exactly one job that runs factory_tools.dashboard build"
    return found[0]


def publish_job() -> tuple[str, dict]:
    found = [
        (n, j) for n, j in jobs().items()
        if any("/root/static/factory" in str(s.get("run", "")) for s in j.get("steps", []))
    ]
    assert len(found) == 1, "expected exactly one job that publishes to /root/static/factory"
    return found[0]


def publish_run() -> str:
    return "\n".join(str(s.get("run", "")) for s in publish_job()[1]["steps"])


def publish_env() -> dict:
    out: dict = dict(publish_job()[1].get("env", {}) or {})
    for s in publish_job()[1]["steps"]:
        out.update(s.get("env", {}) or {})
    return out


# ---- all workflows ---------------------------------------------------------

LOCAL_USES = "./.github/workflows/"


def _secret_jobs_declare_production(called: Path) -> bool:
    if not called.is_file():
        return False
    secret_jobs = [
        j for j in (load(called).get("jobs") or {}).values()
        if SECRET_RE.search(yaml.safe_dump(j))
    ]
    return bool(secret_jobs) and all(env_name(j) == "production" for j in secret_jobs)


def secret_offenders(workflows: Path) -> list[str]:
    offenders = []
    files = sorted(list(workflows.glob("*.yml")) + list(workflows.glob("*.yaml")))
    for path in files:
        for name, job in (load(path).get("jobs") or {}).items():
            if not SECRET_RE.search(yaml.safe_dump(job)) or env_name(job) == "production":
                continue
            uses = str(job.get("uses", ""))
            if uses.startswith(LOCAL_USES) and "@" not in uses:
                if _secret_jobs_declare_production(workflows / uses[len(LOCAL_USES):]):
                    continue
            offenders.append(f"{path.name}:{name}")
    return offenders


def test_every_job_using_deploy_secrets_declares_the_production_environment():
    offenders = secret_offenders(WORKFLOWS)
    assert not offenders, f"jobs use deploy secrets without environment: production: {offenders}"


def _write(tmp_path: Path, files: dict[str, dict]) -> Path:
    for fname, doc in files.items():
        (tmp_path / fname).write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tmp_path


_SECRETS = {"SERVER_HOST": "${{ secrets.SERVER_HOST }}"}


def _caller(uses: str) -> dict:
    return {"jobs": {"c": {"uses": uses, "secrets": _SECRETS}}}


_PROD_CALLEE = {"jobs": {"s": {"environment": "production", "env": _SECRETS, "steps": []}}}


def test_guard_local_caller_of_a_production_job_is_accepted(tmp_path):
    d = _write(tmp_path, {
        "callee.yml": _PROD_CALLEE,
        "caller.yml": _caller("./.github/workflows/callee.yml"),
    })
    assert secret_offenders(d) == []


def test_guard_remote_caller_with_secrets_is_flagged(tmp_path):
    d = _write(tmp_path, {
        "caller.yml": _caller("other/repo/.github/workflows/x.yml@main"),
    })
    assert secret_offenders(d) == ["caller.yml:c"]


def test_guard_caller_of_a_missing_local_file_is_flagged(tmp_path):
    d = _write(tmp_path, {
        "caller.yml": _caller("./.github/workflows/nope.yml"),
    })
    assert secret_offenders(d) == ["caller.yml:c"]


def test_guard_local_caller_whose_called_job_lacks_production_is_flagged(tmp_path):
    d = _write(tmp_path, {
        "callee.yml": {"jobs": {"s": {"env": _SECRETS, "steps": []}}},
        "caller.yml": _caller("./.github/workflows/callee.yml"),
    })
    assert "caller.yml:c" in secret_offenders(d)


def test_guard_plain_job_with_secrets_and_no_environment_is_flagged(tmp_path):
    d = _write(tmp_path, {"plain.yml": {"jobs": {"p": {"env": _SECRETS, "steps": []}}}})
    assert secret_offenders(d) == ["plain.yml:p"]


def test_no_workflow_runs_ssh_keyscan():
    for path in workflow_files():
        assert "ssh-keyscan" not in path.read_text(encoding="utf-8"), path.name


def test_no_workflow_disables_or_relaxes_host_key_checking():
    for path in workflow_files():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"StrictHostKeyChecking[=\s]+no\b", text), path.name
        assert "accept-new" not in text, path.name


# ---- factory-dashboard.yml: job split --------------------------------------

def test_snapshot_job_does_not_reference_the_deploy_secrets():
    _, job = snapshot_job()
    assert not SECRET_RE.search(yaml.safe_dump(job))
    assert env_name(job) != "production"


def test_publish_is_its_own_job_declaring_the_production_environment():
    (sn, _), (pn, job) = snapshot_job(), publish_job()
    assert pn != sn
    assert env_name(job) == "production"


def test_publish_job_needs_the_snapshot_job():
    sn, _ = snapshot_job()
    needs = publish_job()[1].get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert sn in needs


def test_publish_job_is_guarded_to_main():
    assert "refs/heads/main" in str(publish_job()[1].get("if", ""))


def test_publish_job_downloads_the_dashboard_artifact():
    steps = publish_job()[1]["steps"]
    assert any(
        str(s.get("uses", "")).startswith("actions/download-artifact")
        and (s.get("with", {}) or {}).get("name") == "factory-dashboard"
        for s in steps
    ), "no actions/download-artifact step for `factory-dashboard`"


# ---- factory-dashboard.yml: pinned host key --------------------------------

def test_host_key_variable_is_passed_through_env_never_inline():
    env = publish_env()
    assert str(env.get("SERVER_HOST_KEY", "")).strip() == "${{ vars.SERVER_HOST_KEY }}"
    for job in jobs().values():
        for s in job.get("steps", []):
            run = str(s.get("run", ""))
            assert "vars." not in run and "secrets." not in run, "context inline in run:"


def test_known_hosts_is_written_from_the_pinned_key_and_the_host_name():
    run = publish_run()
    lines = [
        ln for ln in run.splitlines()
        if "known_hosts" in ln
        and re.search(r"\$\{?SERVER_HOST_KEY\b", ln)
        and re.search(r"\$\{?SERVER_HOST(?![A-Za-z0-9_])", ln)
    ]
    assert lines, "no line writes known_hosts from $SERVER_HOST and $SERVER_HOST_KEY"


def test_empty_host_key_with_secrets_set_fails_the_job():
    run = publish_run()
    m = re.search(
        r"^[ \t]*if\b([^\n]*-z[^\n]*SERVER_HOST_KEY[^\n]*)\n(.*?)\n[ \t]*fi\b", run, re.S | re.M
    )
    assert m, 'no `if [ -z "$SERVER_HOST_KEY" ]; then ... fi` check'
    assert "SSH_PRIVATE_KEY" not in m.group(1), "host key check is folded into the skip condition"
    assert re.search(r"\bexit\s+[1-9]", m.group(2)), "empty host key does not exit non-zero"
    assert not re.search(r"\bexit\s+0\b", m.group(2)), "empty host key exits 0"


def test_host_key_check_comes_after_the_secrets_skip():
    run = publish_run()
    skip = re.search(r"\bexit\s+0\b", run)
    check = re.search(r"-z[^\n]*SERVER_HOST_KEY", run)
    assert skip and check and skip.start() < check.start(), (
        "secrets-empty skip (exit 0) must come first so absent secrets still degrade"
    )


def test_key_cleanup_runs_always_in_the_publish_job():
    steps = publish_job()[1]["steps"]
    assert any(
        "always()" in str(s.get("if", "")) and re.search(r"\brm\b", str(s.get("run", "")))
        for s in steps
    ), "no `if: always()` cleanup step in the publish job"
