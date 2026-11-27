# ruff: noqa: E501
"""Acceptance tests for issue #665: the host-shape step of the terminal smoke tells outcomes apart.

Follow-up to #655 (D182, D184). The step in `.github/workflows/server-smoke.yml` that runs
`iptables -C` and lists leftover `lab-*` containers must:
  1. tell the `iptables -C` outcomes apart: exit 1 is "rule missing" (one line naming the rule),
     exit 4 (xtables lock) is retried a few times with `-w`, an ssh failure (255) is reported as
     "ssh failed" and never as a missing rule;
  2. run even when an earlier terminal smoke step failed (`!cancelled() && env.CHECK == 'terminal'`)
     and be a no-op when ssh was never configured (the `ssh_config` guard of "Disable the smoke student");
  3. list only containers labelled `lf=1` (like the janitor), so a foreign `lab-*` container is not flagged;
  4. send only the three allowed remote commands (`iptables -C ...`, `docker ps ...`,
     `python -m app.cli live-sessions`); any other `docker exec` counts as a write in the fake.

The workflow cases parse the YAML. The behavioural cases extract the step's script and run it under
bash with fake `ssh`, `docker`, `iptables` and `sleep` executables on PATH; nothing touches a server.
Set LF_EXAM_FORCE_BASH=1 to run them on Windows (slow).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SMOKE = REPO_ROOT / ".github" / "workflows" / "server-smoke.yml"

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(
    BASH is None or (os.name == "nt" and not os.environ.get("LF_EXAM_FORCE_BASH")),
    reason="needs a POSIX bash with fast process creation (Linux CI)",
)

ALLOWED_REMOTE = re.compile(
    r"^(iptables\s+(-w(\s+\d+)?\s+)*-C\s+DOCKER-USER\b[^;&|`$]*"
    r"|docker\s+ps\b[^;&|`$]*"
    r"|docker\s+exec\s+lf-manager\s+python\s+-m\s+app\.cli\s+live-sessions)$"
)

# ---------------------------------------------------------------- workflow helpers


def _steps() -> list:
    yaml = pytest.importorskip("yaml")
    assert SMOKE.is_file(), "server-smoke.yml is missing"
    return yaml.safe_load(SMOKE.read_text(encoding="utf-8"))["jobs"]["smoke"]["steps"]


def _host_step() -> dict:
    found = [s for s in _steps() if "iptables" in str(s.get("run", ""))]
    assert len(found) == 1, "exactly one step must check the firewall rule"
    return found[0]


def _script() -> str:
    return str(_host_step()["run"])


# ---------------------------------------------------------------- criterion 2: the condition (static)


def test_ac2_the_step_runs_after_an_earlier_failed_step():
    cond = str(_host_step().get("if", ""))
    assert "!cancelled()" in cond.replace(" ", "") or "! cancelled()" in cond, (
        f"the step must run after a failed step (`!cancelled() && env.CHECK == 'terminal'`), got {cond!r}"
    )
    assert re.search(r"env\.CHECK\s*==\s*'terminal'", cond), f"the step must stay terminal-only: {cond!r}"
    assert "inputs." not in cond and "github.event_name" not in cond


def test_ac2_the_step_does_not_use_always_so_a_cancelled_run_stops():
    cond = str(_host_step().get("if", ""))
    assert "always()" not in cond, "a cancelled run must not run the step (use !cancelled())"


def test_ac2_the_step_guards_on_the_ssh_config_file_like_the_disable_step():
    script = _script()
    assert re.search(r"\[\s+!\s+-f\s+\"?\$RUNNER_TEMP/ssh_config\"?\s+\]", script), (
        "the step must exit 0 early when $RUNNER_TEMP/ssh_config does not exist (ssh never configured)"
    )


def test_ac2_the_step_still_sits_between_ssh_setup_and_key_removal():
    steps = _steps()
    names = [str(s.get("name", "")) for s in steps]
    idx = steps.index(_host_step())
    cfg = next(i for i, n in enumerate(names) if n.startswith("Configure SSH"))
    key = next(i for i, n in enumerate(names) if n.startswith("Remove the SSH key"))
    assert cfg < idx < key


# ---------------------------------------------------------------- criterion 3: label filter (static)


def test_ac3_the_listing_filters_on_label_lf_1_like_the_janitor():
    ps = [ln for ln in _script().splitlines() if re.search(r"\bdocker\s+ps\b", ln)]
    assert ps, "no docker ps line"
    for ln in ps:
        assert re.search(r"--filter[= ]['\"]?label=lf=1\b", ln), f"docker ps must filter on label=lf=1: {ln!r}"
        assert "name=lab-" in ln, f"docker ps must still be limited to lab-* names: {ln!r}"
        assert "{{.Names}}" in ln, f"docker ps must still print names only: {ln!r}"


# ---------------------------------------------------------------- fakes

FAKE_SSH = """#!/usr/bin/env bash
# fake ssh: record the remote command (the last argument), then run it locally under the fakes on PATH
cmd="${@: -1}"
echo "ssh" >> "$FAKE_STATE/log"
printf '%s\\n' "$cmd" >> "$FAKE_STATE/remote"
if [ -f "$FAKE_STATE/ssh_fail" ]; then
  echo "ssh: connect to host smoke port 22: Connection refused" >&2
  exit 255
fi
exec bash -c "$cmd"
"""

FAKE_SLEEP = """#!/usr/bin/env bash
echo "sleep $*" >> "$FAKE_STATE/log"
exit 0
"""

# rule file: "1" present, "0" missing (exit 1). lock file: number of calls that fail with exit 4.
FAKE_IPTABLES = """#!/usr/bin/env bash
echo "iptables $*" >> "$FAKE_STATE/log"
S="$FAKE_STATE"
waited=0; args=()
while [ $# -gt 0 ]; do
  if [ "$1" = "-w" ]; then waited=1; shift; case "${1:-}" in ''|*[!0-9]*) ;; *) shift ;; esac; continue; fi
  args+=("$1"); shift
done
set -- "${args[@]}"
if [ "$1" = "-C" ]; then
  n=$(cat "$S/lock" 2>/dev/null || echo 0)
  if [ "$n" -gt 0 ]; then
    echo $((n-1)) > "$S/lock"
    [ "$waited" = 1 ] && echo "waited" >> "$S/log"
    echo "Another app is currently holding the xtables lock." >&2
    exit 4
  fi
  if [ "$(cat "$S/rule" 2>/dev/null)" = "1" ] && \\
     [ "$*" = "-C DOCKER-USER -i br-lf-labs ! -s 172.31.240.2 -m comment --comment lf-labs -j DROP" ]; then
    exit 0
  fi
  echo "iptables: Bad rule (does a matching rule exist in that chain?)." >&2
  exit 1
fi
echo "WRITE: iptables $*" >> "$S/writes"
exit 0
"""

# ps lists: each line is "name" (labelled lf=1) or "name!" (no lf=1 label, a foreign container).
FAKE_DOCKER = """#!/usr/bin/env bash
S="$FAKE_STATE"
nth() {
  local n
  n=$(cat "$S/cnt_$1" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$S/cnt_$1"
  while [ "$n" -gt 0 ]; do
    if [ -f "$S/$1.$n" ]; then cat "$S/$1.$n"; return; fi
    n=$((n-1))
  done
}
case "$1" in
  ps)
    echo "docker-ps" >> "$S/log"
    labelled=0
    for a in "$@"; do
      case "$a" in label=lf=1) labelled=1 ;; esac
    done
    while IFS= read -r name; do
      [ -z "$name" ] && continue
      case "$name" in
        *!) [ "$labelled" = 1 ] && continue; name="${name%!}" ;;
      esac
      case "$name" in lab-*) echo "$name" ;; esac
    done < <(nth ps)
    ;;
  exec)
    case "$*" in
      *live-sessions*) echo "live-sessions" >> "$S/log"; nth live ;;
      *) echo "WRITE: docker $*" >> "$S/writes" ;;
    esac
    ;;
  *)
    echo "WRITE: docker $*" >> "$S/writes"
    ;;
esac
exit 0
"""


def _run(tmp_path: Path, *, rule=True, lock=0, ssh_fail=False, ssh_config=True, ps=(), live=()):
    bindir = tmp_path / "fakebin"
    state = tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("sleep", FAKE_SLEEP), ("iptables", FAKE_IPTABLES), ("docker", FAKE_DOCKER)):
        p = bindir / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    (state / "rule").write_text("1" if rule else "0")
    (state / "lock").write_text(str(lock))
    if ssh_fail:
        (state / "ssh_fail").write_text("1")
    for n, names in enumerate(ps or [[]], 1):
        (state / f"ps.{n}").write_text("\n".join(names) + "\n", newline="\n")
    for n, ids in enumerate(live or [[]], 1):
        (state / f"live.{n}").write_text("\n".join(ids) + "\n", newline="\n")
    if ssh_config:
        (tmp_path / "ssh_config").write_text("Host smoke" + chr(10), encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(_script() + "\n", encoding="utf-8", newline="\n")
    env = {
        **os.environ,
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_STATE": str(state),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "CHECK": "terminal",
    }
    for k in ("SSH_PRIVATE_KEY", "SERVER_HOST", "SERVER_USER"):
        env.pop(k, None)
    proc = subprocess.run(
        [BASH, "-eo", "pipefail", str(script)], env=env, capture_output=True, text=True, timeout=120, cwd=tmp_path
    )
    log = (state / "log").read_text().splitlines() if (state / "log").exists() else []
    writes = (state / "writes").read_text().splitlines() if (state / "writes").exists() else []
    remote = (state / "remote").read_text().splitlines() if (state / "remote").exists() else []
    return proc, proc.stdout + proc.stderr, log, writes, remote


A = "11111111-aaaa-4bbb-8ccc-000000000001"


# ---------------------------------------------------------------- criterion 1: iptables outcomes


@needs_bash
def test_ac1_exit_1_is_rule_missing_with_one_line_naming_the_rule(tmp_path):
    proc, out, log, writes, remote = _run(tmp_path, rule=False, ps=[[]], live=[[]])
    assert proc.returncode != 0, out
    lines = [ln for ln in out.splitlines() if "DOCKER-USER" in ln]
    assert len(lines) == 1 and "lf-labs" in lines[0], f"one line must name the rule, got {lines!r}"
    assert "ssh failed" not in out.lower()
    assert "xtables" not in out.lower() or "missing" in lines[0].lower()


@needs_bash
def test_ac1_an_ssh_failure_is_reported_as_ssh_failed_not_as_a_missing_rule(tmp_path):
    proc, out, log, writes, remote = _run(tmp_path, ssh_fail=True)
    assert proc.returncode != 0, out
    assert "ssh failed" in out.lower(), f"an ssh failure (255) must say 'ssh failed': {out!r}"
    assert "missing" not in out.lower(), f"an ssh failure must not be reported as a missing rule: {out!r}"
    assert "lf-labs" not in out and "DOCKER-USER" not in out, "no rule spec may be blamed for an ssh failure"


@needs_bash
def test_ac1_a_locked_xtables_is_retried_with_w_and_then_passes(tmp_path):
    proc, out, log, writes, remote = _run(tmp_path, rule=True, lock=2, ps=[[]], live=[[]])
    assert proc.returncode == 0, out
    checks = [ln for ln in log if ln.startswith("iptables ") and " -C " in ln + " "]
    assert len(checks) >= 3, f"exit 4 must be retried until the lock is free: {log}"
    assert any(re.search(r"(^|\s)-w(\s|$)", ln) for ln in checks[1:]), f"the retries must use -w: {checks}"
    assert "missing" not in out.lower()


@needs_bash
def test_ac1_a_lock_that_never_frees_fails_after_a_few_tries_without_blaming_the_rule(tmp_path):
    proc, out, log, writes, remote = _run(tmp_path, rule=True, lock=1000)
    assert proc.returncode != 0, out
    checks = [ln for ln in log if ln.startswith("iptables ")]
    assert 2 <= len(checks) <= 10, f"a few retries, not a loop without end: {len(checks)}"
    assert "missing" not in out.lower(), f"a held lock is not a missing rule: {out!r}"
    assert "ssh failed" not in out.lower()


@needs_bash
def test_ac1_a_missing_rule_is_not_retried(tmp_path):
    proc, out, log, writes, remote = _run(tmp_path, rule=False, ps=[[]], live=[[]])
    assert proc.returncode != 0
    assert len([ln for ln in log if ln.startswith("iptables ")]) == 1, "exit 1 is final, not retried"


@needs_bash
def test_ac1_rule_present_passes_and_leftover_check_still_runs(tmp_path):
    proc, out, log, writes, remote = _run(tmp_path, rule=True, ps=[[f"lab-{A}"]], live=[[], []])
    assert proc.returncode != 0, out
    assert f"lab-{A}" in out


# ---------------------------------------------------------------- criterion 2: the guard (behaviour)


@needs_bash
def test_ac2_without_ssh_config_the_step_does_nothing_and_succeeds(tmp_path):
    proc, out, log, writes, remote = _run(tmp_path, ssh_config=False)
    assert proc.returncode == 0, out
    assert remote == [] and not [ln for ln in log if ln == "ssh"], "no ssh call when ssh was never configured"


# ---------------------------------------------------------------- criterion 3: foreign containers (behaviour)


@needs_bash
def test_ac3_a_lab_container_without_the_lf_label_is_not_flagged(tmp_path):
    proc, out, log, writes, remote = _run(tmp_path, ps=[[f"lab-{A}!"]], live=[[], []])
    assert proc.returncode == 0, out
    assert f"lab-{A}" not in out


@needs_bash
def test_ac3_a_labelled_leftover_is_flagged_beside_a_foreign_one(tmp_path):
    other = "lab-22222222-aaaa-4bbb-8ccc-000000000002"
    proc, out, log, writes, remote = _run(tmp_path, ps=[[f"lab-{A}", f"{other}!"]], live=[[], []])
    assert proc.returncode != 0, out
    assert f"lab-{A}" in out
    assert other not in out


# ---------------------------------------------------------------- criterion 4: only the three remote forms


@needs_bash
def test_ac4_every_remote_command_is_one_of_the_three_allowed_forms_in_every_scenario(tmp_path):
    scenarios = [
        dict(rule=True, ps=[[]], live=[[]]),
        dict(rule=False, ps=[[]], live=[[]]),
        dict(rule=True, lock=2, ps=[[]], live=[[]]),
        dict(rule=True, ps=[[f"lab-{A}"], []], live=[[], []]),
        dict(rule=True, ps=[[f"lab-{A}"]], live=[[], []]),
        dict(rule=True, ps=[[f"lab-{A}!"]], live=[[], []]),
        dict(ssh_fail=True),
    ]
    for i, kw in enumerate(scenarios):
        d = tmp_path / f"s{i}"
        d.mkdir()
        proc, out, log, writes, remote = _run(d, **kw)
        assert remote, f"scenario {i} sent no remote command"
        for cmd in remote:
            assert ALLOWED_REMOTE.match(cmd), f"scenario {i}: remote command outside the allowed forms: {cmd!r}"
        assert not writes, f"scenario {i}: the step changed something: {writes}"


@needs_bash
def test_ac4_the_fake_treats_any_other_docker_exec_as_a_write(tmp_path):
    # guards the exam itself: a stray `docker exec lf-manager ...` must be seen as a write
    bindir = tmp_path / "fakebin"
    state = tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    p = bindir / "docker"
    p.write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
    p.chmod(0o755)
    subprocess.run(
        [BASH, str(p), "exec", "lf-manager", "python", "-m", "app.cli", "smoke-student", "--disable"],
        env={**os.environ, "FAKE_STATE": str(state)},
        check=True,
        capture_output=True,
    )
    assert (state / "writes").exists()
