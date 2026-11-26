# ruff: noqa: E501
"""Acceptance tests for issue #655 (D182, D184): the host-shape step of the terminal smoke.

The nightly and the deploy-called `terminal` smoke run one extra, read-only step on the server:
  - `iptables -C DOCKER-USER -i br-lf-labs ! -s 172.31.240.2 -m comment --comment lf-labs -j DROP`
    must succeed; a missing rule fails the run with one line naming the rule.
  - `lab-*` containers are listed between two `python -m app.cli live-sessions` reads (the manager
    prints one live session id per line); a container whose id is in neither read and that is still
    there after a wait of about 30 s fails the run. Container names only are printed.

The workflow cases parse the YAML. The behavioural cases extract the step's script and run it under
bash with fake `ssh`, `docker`, `iptables` and `sleep` executables on PATH; nothing touches a server.
The manager cases call `app.cli` without a database.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SMOKE = REPO_ROOT / ".github" / "workflows" / "server-smoke.yml"
MANAGER_DIR = REPO_ROOT / "labs" / "manager"

RULE = "-C DOCKER-USER -i br-lf-labs ! -s 172.31.240.2 -m comment --comment lf-labs -j DROP"
RULE_RE = re.compile(
    r"iptables\s+-C\s+DOCKER-USER\s+-i\s+br-lf-labs\s+!\s+-s\s+172\.31\.240\.2\s+"
    r"-m\s+comment\s+--comment\s+lf-labs\s+-j\s+DROP"
)

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(
    BASH is None or os.name == "nt",
    reason="needs a POSIX bash with fast process creation (Linux CI); Git bash on Windows takes ~30 s per case",
)


# ---------------------------------------------------------------- workflow helpers


def _load() -> dict:
    yaml = pytest.importorskip("yaml")
    assert SMOKE.is_file(), "server-smoke.yml is missing"
    data = yaml.safe_load(SMOKE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _steps() -> list:
    return _load()["jobs"]["smoke"]["steps"]


def _host_step() -> dict:
    found = [s for s in _steps() if "iptables" in str(s.get("run", ""))]
    assert found, "server-smoke.yml has no step that runs `iptables -C` on the server"
    assert len(found) == 1, "exactly one step must check the firewall rule"
    return found[0]


def _script() -> str:
    return str(_host_step()["run"])


# ---------------------------------------------------------------- 1. the step and its condition


def test_ac1_a_step_checks_the_firewall_rule_with_the_exact_command():
    script = _script()
    assert RULE_RE.search(script), f"the step must run `iptables {RULE}`"


def test_ac1_the_rule_is_checked_on_the_server_through_the_smoke_connection_alias():
    script = _script()
    line = next(ln for ln in script.splitlines() if "iptables" in ln and "-C" in ln)
    assert re.search(r"\bssh\b.*ssh_config.*\bsmoke\b", script, re.S), "the step must use the smoke alias"
    # the iptables command is sent to the server, not run on the runner
    assert "smoke" in line or re.search(r"smoke\s+[\"'][^\n]*iptables", script), (
        "iptables -C must be sent through `ssh ... smoke`"
    )


def test_ac1_the_step_runs_only_for_the_terminal_check():
    cond = str(_host_step().get("if", ""))
    assert re.search(r"env\.CHECK\s*==\s*'terminal'", cond), (
        f"the step must be conditioned on env.CHECK == 'terminal' (the style of the other terminal steps), got {cond!r}"
    )
    assert "!=" not in cond.replace("!= ''", ""), f"unexpected negation in {cond!r}"


def test_ac1_the_condition_does_not_depend_on_the_manual_input_or_on_the_event():
    cond = str(_host_step().get("if", ""))
    # scheduled and deploy-called runs have no inputs.check: CHECK is `inputs.check || 'terminal'`
    assert "github.event_name" not in cond
    assert "inputs." not in cond, "use env.CHECK so scheduled and called runs take the step"


def test_ac1_the_step_runs_after_ssh_is_configured_and_before_the_key_is_removed():
    steps = _steps()
    names = [str(s.get("name", "")) for s in steps]
    idx = steps.index(_host_step())
    cfg = next(i for i, n in enumerate(names) if n.startswith("Configure SSH"))
    key = next(i for i, n in enumerate(names) if n.startswith("Remove the SSH key"))
    assert cfg < idx < key


def test_ac1_a_missing_rule_fails_the_step_with_a_message_naming_the_rule():
    script = _script()
    assert re.search(r"lf-labs", script) and "DOCKER-USER" in script
    assert re.search(r"\bexit\s+1\b|\|\|\s*\{|\bexit\s+\"?\$", script), "the step must fail on a missing rule"


# ---------------------------------------------------------------- 2. read-only


_WRITE_IPT = re.compile(
    r"\bip6?tables\b[^\n]*?\s(-[AIDFXNPRZ]|--append|--insert|--delete|--flush|--new-chain|--delete-chain|--policy|--replace|--zero)\b"
)
_WRITE_DOCKER = re.compile(
    r"\bdocker\s+(rm|kill|stop|restart|pause|prune|rmi|run|start|create|update)\b"
    r"|\bdocker\s+(container|system|network|volume|image)\s+(rm|kill|stop|prune|remove|create|run|start)\b"
    r"|\bdocker\s+exec\b[^\n]*\b(rm|kill)\b"
)


def test_ac3_the_step_contains_no_iptables_writes():
    m = _WRITE_IPT.search(_script())
    assert m is None, f"write option in the step: {m.group(0) if m else ''}"


def test_ac3_the_step_removes_nothing_with_docker():
    m = _WRITE_DOCKER.search(_script())
    assert m is None, f"docker write command in the step: {m.group(0) if m else ''}"


def test_ac3_the_step_does_not_use_ip6tables_or_other_firewall_tools():
    script = _script()
    assert "ip6tables" not in script
    assert not re.search(r"\b(nft|ufw|firewall-cmd|iptables-restore|iptables-save)\b", script)


def test_ac3_the_command_list_is_iptables_check_docker_ps_and_live_sessions():
    script = _script()
    assert RULE_RE.search(script)
    assert re.search(r"\bdocker\s+ps\b", script), "containers are listed with docker ps"
    assert re.search(r"docker\s+exec\s+lf-manager\s+python\s+-m\s+app\.cli\s+live-sessions", script), (
        "live sessions come from `docker exec lf-manager python -m app.cli live-sessions`"
    )
    assert script.count("live-sessions") >= 2, "the live sessions are read twice (before and after the listing)"


def test_ac3_the_step_writes_no_files_on_the_server():
    script = _script()
    assert "scp" not in script
    assert not re.search(r"\bsmoke\s+\"[^\"]*(\brm\b|\bmkdir\b|>\s*/|\btee\b)", script)


# ---------------------------------------------------------------- 3. leftovers (static)


def test_ac2_the_listing_filters_lab_containers_and_prints_names_only():
    script = _script()
    ps = [ln for ln in script.splitlines() if re.search(r"\bdocker\s+ps\b", ln)]
    assert ps, "no docker ps line"
    for ln in ps:
        assert "lab-" in ln, f"docker ps must be limited to lab-* containers: {ln!r}"
        assert re.search(r"--format\s+['\"]?\{\{\s*\.Names\s*\}\}['\"]?(?=[\s'\"]|$)", ln), (
            f"docker ps must print names only (--format '{{{{.Names}}}}'): {ln!r}"
        )
    assert not re.search(r"\{\{\s*\.(Labels|Image|Command|Ports|Mounts|Networks|ID)", script)
    assert "docker inspect" not in script and "docker logs" not in script


def test_ac2_the_step_waits_about_thirty_seconds_before_the_recheck():
    nums = [int(n) for n in re.findall(r"\bsleep\s+(\d+)\b", _script())]
    assert nums, "the step must wait (sleep) before it re-checks"
    assert 25 <= max(nums) <= 60 or sum(nums) >= 25, f"wait of about 30 s expected, got sleeps {nums}"


# ---------------------------------------------------------------- 5. secrets


def test_the_step_references_no_secret_and_adds_none():
    step = _host_step()
    text = str(step)
    assert "secrets." not in text
    assert "secrets" not in {str(k).lower() for k in (step.get("env") or {})}
    for name in ("SSH_PRIVATE_KEY", "SERVER_HOST", "SERVER_USER", "SERVER_HOST_KEY", "SMOKE_PASSWORD", "class_pw"):
        assert name not in step.get("run", ""), f"the step must not touch {name}"
    raw = SMOKE.read_text(encoding="utf-8")
    names = {m.upper() for m in re.findall(r"secrets\.([A-Za-z0-9_]+)", raw)} - {"GITHUB_TOKEN"}
    assert names == {"SSH_PRIVATE_KEY", "SERVER_HOST", "SERVER_USER"}, f"secrets in the workflow: {sorted(names)}"


def test_the_step_prints_no_environment_or_key_material():
    script = _script()
    assert not re.search(r"\b(printenv|env\s*\||set\s+-x|cat\s+[^\n]*(smoke_key|known_hosts))", script)


# ---------------------------------------------------------------- behaviour under fake executables

FAKE_SSH = """#!/usr/bin/env bash
# fake ssh: run the last argument (the remote command) locally, under the fakes on PATH
cmd="${@: -1}"
echo "ssh" >> "$FAKE_STATE/log"
exec bash -c "$cmd"
"""

FAKE_SLEEP = """#!/usr/bin/env bash
echo "sleep $*" >> "$FAKE_STATE/log"
exit 0
"""

FAKE_IPTABLES = """#!/usr/bin/env bash
echo "iptables $*" >> "$FAKE_STATE/log"
if [ "$1" = "-C" ]; then
  if [ "$(cat "$FAKE_STATE/rule" 2>/dev/null)" = "1" ] && \\
     [ "$*" = "-C DOCKER-USER -i br-lf-labs ! -s 172.31.240.2 -m comment --comment lf-labs -j DROP" ]; then
    exit 0
  fi
  echo "iptables: Bad rule (does a matching rule exist in that chain?)." >&2
  exit 1
fi
echo "WRITE: iptables $*" >> "$FAKE_STATE/writes"
exit 0
"""

FAKE_DOCKER = """#!/usr/bin/env bash
S="$FAKE_STATE"
nth() {  # nth <prefix>: the next numbered file, or the last one that exists
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
    names_only=0; lab_only=0
    for a in "$@"; do
      case "$a" in
        "{{.Names}}"|"{{ .Names }}") names_only=1 ;;
        name=*lab-*) lab_only=1 ;;
      esac
    done
    while IFS= read -r name; do
      [ -z "$name" ] && continue
      if [ "$lab_only" = 1 ]; then case "$name" in lab-*) ;; *) continue ;; esac; fi
      if [ "$names_only" = 1 ]; then echo "$name"; else printf '%s\\tSTUDENT-DATA-COLUMN\\n' "$name"; fi
    done < <(nth ps)
    ;;
  exec)
    case "$*" in
      *live-sessions*) echo "live-sessions" >> "$S/log"; nth live ;;
      *) echo "docker-exec-other $*" >> "$S/log" ;;
    esac
    ;;
  *)
    echo "WRITE: docker $*" >> "$S/writes"
    ;;
esac
exit 0
"""


def _fakes(tmp_path: Path) -> tuple[Path, Path]:
    bindir = tmp_path / "fakebin"
    state = tmp_path / "state"
    bindir.mkdir()
    state.mkdir()
    for name, body in (
        ("ssh", FAKE_SSH),
        ("sleep", FAKE_SLEEP),
        ("iptables", FAKE_IPTABLES),
        ("docker", FAKE_DOCKER),
    ):
        p = bindir / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    return bindir, state


def _run(tmp_path: Path, *, rule: bool = True, ps=(), live=()):
    """ps and live are lists of lists: the n-th call returns the n-th list (the last repeats)."""
    bindir, state = _fakes(tmp_path)
    (state / "rule").write_text("1" if rule else "0")
    for n, names in enumerate(ps or [[]], 1):
        (state / f"ps.{n}").write_text("\n".join(names) + "\n", newline="\n")
    for n, ids in enumerate(live or [[]], 1):
        (state / f"live.{n}").write_text("\n".join(ids) + "\n", newline="\n")
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
        [BASH, "-eo", "pipefail", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    log = (state / "log").read_text().splitlines() if (state / "log").exists() else []
    writes = (state / "writes").read_text().splitlines() if (state / "writes").exists() else []
    return proc, proc.stdout + proc.stderr, log, writes


A = "11111111-aaaa-4bbb-8ccc-000000000001"
B = "22222222-aaaa-4bbb-8ccc-000000000002"


@needs_bash
def test_ac1_rule_present_and_no_leftovers_passes(tmp_path):
    proc, out, log, writes = _run(tmp_path, ps=[["lf-manager", "lf-db"]], live=[[]])
    assert proc.returncode == 0, out
    assert any(ln.startswith("iptables -C DOCKER-USER") for ln in log)
    assert not writes


@needs_bash
def test_ac1_a_missing_rule_fails_with_one_line_naming_the_rule(tmp_path):
    proc, out, log, writes = _run(tmp_path, rule=False, ps=[[]], live=[[]])
    assert proc.returncode != 0, out
    lines = [ln for ln in out.splitlines() if "DOCKER-USER" in ln]
    assert len(lines) == 1, f"one line must name the rule, got {lines!r}"
    assert "lf-labs" in lines[0]
    assert "Traceback" not in out
    assert not writes


@needs_bash
def test_ac2_a_lab_container_no_session_owns_fails_and_is_named(tmp_path):
    name = f"lab-{A}"
    proc, out, log, writes = _run(tmp_path, ps=[[name, "lf-manager"]], live=[[], []])
    assert proc.returncode != 0, out
    assert name in out
    assert "lf-manager" not in out, "only the leftover is named"


@needs_bash
def test_ac2_the_output_carries_container_names_only(tmp_path):
    proc, out, log, writes = _run(tmp_path, ps=[[f"lab-{A}"]], live=[[]])
    assert proc.returncode != 0, out
    assert "STUDENT-DATA-COLUMN" not in out, "other columns of docker ps must not be printed"


@needs_bash
def test_ac2_only_the_unowned_container_is_named_when_another_is_owned(tmp_path):
    proc, out, log, writes = _run(tmp_path, ps=[[f"lab-{A}", f"lab-{B}"]], live=[[B], [B]])
    assert proc.returncode != 0, out
    assert f"lab-{A}" in out
    assert f"lab-{B}" not in out


@needs_bash
def test_ac2_a_container_that_a_live_session_owns_passes(tmp_path):
    proc, out, log, writes = _run(tmp_path, ps=[[f"lab-{A}"]], live=[[A], [A]])
    assert proc.returncode == 0, out


@needs_bash
def test_ac2_a_session_that_started_during_the_check_is_not_a_leftover(tmp_path):
    # the id shows up in the second read only (and stays): the listing raced with the start
    proc, out, log, writes = _run(tmp_path, ps=[[f"lab-{A}"]], live=[[], [A]])
    assert proc.returncode == 0, out


@needs_bash
def test_ac2_a_container_that_is_gone_on_the_recheck_passes(tmp_path):
    proc, out, log, writes = _run(tmp_path, ps=[[f"lab-{A}"], []], live=[[], []])
    assert proc.returncode == 0, out


@needs_bash
def test_ac2_the_listing_sits_between_two_live_reads_and_the_recheck_waits(tmp_path):
    proc, out, log, writes = _run(tmp_path, ps=[[f"lab-{A}"]], live=[[], []])
    assert proc.returncode != 0, out
    first_ps = log.index("docker-ps")
    assert "live-sessions" in log[:first_ps], f"a live read must come before the listing: {log}"
    assert "live-sessions" in log[first_ps:], f"a live read must come after the listing: {log}"
    sleeps = [(i, ln) for i, ln in enumerate(log) if ln.startswith("sleep ")]
    assert sleeps, f"no wait before the re-check: {log}"
    total = sum(int(float(ln.split()[1])) for _, ln in sleeps)
    assert 25 <= total <= 90, f"wait of about 30 s expected, got {total} s ({log})"
    last_sleep = sleeps[-1][0]
    assert "docker-ps" in log[last_sleep:], f"the containers must be listed again after the wait: {log}"


@needs_bash
def test_ac3_nothing_is_changed_on_the_server_in_any_scenario(tmp_path):
    for i, (rule, ps, live) in enumerate(
        [
            (True, [[f"lab-{A}"]], [[], []]),
            (False, [[f"lab-{A}"]], [[], []]),
            (True, [[f"lab-{A}"], []], [[A], []]),
        ]
    ):
        d = tmp_path / f"s{i}"
        d.mkdir()
        proc, out, log, writes = _run(d, rule=rule, ps=ps, live=live)
        assert not writes, f"the step changed something: {writes}"
        for ln in log:
            if ln.startswith("iptables "):
                assert ln.startswith("iptables -C "), f"non-check iptables call: {ln}"


# ---------------------------------------------------------------- 4. the manager command


def _cli():
    sys.path.insert(0, str(MANAGER_DIR))
    try:
        from app import cli
    finally:
        sys.path.pop(0)
    return cli


def test_the_live_sessions_command_is_listed_in_the_cli_help(capsys):
    cli = _cli()
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "live-sessions" in capsys.readouterr().out


def test_live_sessions_has_its_own_help(capsys):
    cli = _cli()
    with pytest.raises(SystemExit) as exc:
        cli.main(["live-sessions", "--help"])
    assert exc.value.code == 0
    assert "live" in capsys.readouterr().out.lower()


def test_live_sessions_without_a_database_url_fails_with_one_line(monkeypatch, capsys):
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    cli = _cli()
    rc = cli.main(["live-sessions"])
    cap = capsys.readouterr()
    assert rc not in (0, None)
    assert cap.out == "", "nothing may be printed on stdout when no ids can be listed"
    lines = [ln for ln in cap.err.splitlines() if ln.strip()]
    assert len(lines) == 1, f"one line on stderr expected, got {lines!r}"
    assert "LF_DATABASE_URL" in lines[0]
    assert "Traceback" not in cap.err


def test_live_sessions_as_a_module_exits_non_zero_without_a_traceback():
    env = {k: v for k, v in os.environ.items() if not k.startswith("LF_")}
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "live-sessions"],
        cwd=MANAGER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    assert proc.stdout == ""
    assert "Traceback" not in proc.stderr
    assert "invalid choice" not in proc.stderr, "the live-sessions command does not exist yet"
    assert len([ln for ln in proc.stderr.splitlines() if ln.strip()]) == 1, proc.stderr
