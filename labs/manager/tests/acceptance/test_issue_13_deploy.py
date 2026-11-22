# ruff: noqa: E501
"""Acceptance tests for issue #13: deploy workflow, server script, nginx `labs` block (D34).

Files under test (all in the repo, nothing is deployed by this exam):
- `.github/workflows/deploy.yml`
- `labs/deploy/deploy.sh`        (copied with compose.yml and nginx/ to /root/learning-factory/)
- `labs/deploy/nginx/labs.conf`  (one marked block `learning-factory:labs`)
- `docs/factory/OWNER.md`        (owner's create-admin command)

The real trusted-proxy proof (real nginx in front of the real manager image) is in
`test_issue_13_deploy_docker.py` (docker job).

Interfaces the implementation must provide (the exam runs the real files with stubs):

deploy.sh, run with `bash <root>/deploy.sh` where `<root>` holds deploy.sh, compose.yml and nginx/
(`nginx/install-block.sh` is replaced by a recording stub in the exam):
  env   LF_DEPLOY_ROOT     directory that holds .env, deployed-tag, compose.yml, nginx/
                           (default /root/learning-factory)
        LF_DOCKER          docker binary (default `docker`); every docker call goes through it
        LF_MANAGER_TAG     the tag to deploy (the commit sha). Missing or `latest` -> refuse
        LF_HEALTH_TIMEOUT  seconds to wait for the manager to become healthy (default is the
                           script's choice, the exam sets 3)
        curl, openssl, systemctl are taken from PATH.
  stdin lines `NAME=value` (any may be absent or empty):
        LF_DATABASE_URL    written to .env; an .env value is kept when none is sent
        GHCR_USER, GHCR_TOKEN
                           `docker login ghcr.io -u <user> --password-stdin`, then pulls, then logout
  files <root>/.env          mode 600, lines NAME=value; LF_ID_SECRET (openssl rand -hex 32) once
        <root>/deployed-tag  the last tag that passed the health check
  docker is only used through these forms (the stub in this exam understands exactly these):
        `docker version [--format ...]`, `docker compose version [--short]`,
        `docker compose [-p learning-factory] [-f ...] up -d | down | logs | config --quiet`
        `docker inspect [-f|--format <tmpl>] <name>` with templates that contain one of:
            `json .NetworkSettings.Networks` (a JSON map name -> {IPAddress, Gateway}),
            `range` over Networks printing `$k` and `$v.IPAddress` (one `name ip` per line),
            `IPAddress`, `Networks` (network name), `State.Health` (healthy|unhealthy),
            `State.Running` (true|false), `State.Status`; or no template (JSON array)
        `docker network inspect <name>` (optionally `--format` with `Gateway`), `docker ps`,
        `docker logs lf-manager`, `docker exec lf-manager ...` (`ip route` lives there),
        `docker login|logout|pull`.
  compose is run with LF_MANAGER_TAG, LF_NGINX_NETWORK and LF_TRUSTED_PROXY exported.
  install-block.sh is run as `<root>/nginx/install-block.sh labs <file>` and `... factory <file>`.
  Exit 0 only when the deploy is healthy and every live check passed.

deploy.yml: step `env:` mappings carry the values (`${{ ... }}` never appears inside a `run:`
script); the exam executes the `run:` steps with `ssh`/`scp`/`rsync` stubs on PATH.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
DEPLOY_DIR = REPO_ROOT / "labs" / "deploy"
DEPLOY_SH = DEPLOY_DIR / "deploy.sh"
LABS_CONF = DEPLOY_DIR / "nginx" / "labs.conf"
INSTALLER = DEPLOY_DIR / "nginx" / "install-block.sh"
OWNER_MD = REPO_ROOT / "docs" / "factory" / "OWNER.md"

# Synthetic values only (rule 2 and 7): nothing here is, or looks up, a real secret.
NEW_TAG = "a" * 40
OLD_TAG = "b" * 40
DSN_PASSWORD = "synthetic-pw-13-not-real"
DSN = f"postgresql://lf_app.testproj:{DSN_PASSWORD}@pooler.example.invalid:6543/postgres"
DSN2 = "postgresql://lf_app.testproj:synthetic-pw-13-second@pooler.example.invalid:6543/postgres"
GHCR_TOKEN = "ghs_synthetic13tokenvalue"
KEY_MATERIAL = "SYNTHETICKEYMATERIAL13"
STUB_ID_SECRET = "ab" * 32


def _find_bash() -> str:
    if sys.platform == "win32":
        for cand in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash") or "bash"


BASH = _find_bash()


def _bash_cmd(script: Path, stubs: Path, *flags: str) -> tuple[list[str], dict[str, str]]:
    """Command that runs `script` with `stubs` first on PATH.

    Git Bash on Windows puts /mingw64/bin and /usr/bin in front of the inherited PATH, so a stub
    named like a real tool would be shadowed; the wrapper puts the stub directory first again."""
    if sys.platform != "win32":
        return [BASH, *flags, str(script)], {}
    wrapper = 'PATH="$(cygpath -u "$LF_STUBS"):$PATH"; exec bash "$@" "$(cygpath -u "$LF_SCRIPT")"'
    return [BASH, "-c", wrapper, "wrapper", *flags], {"LF_STUBS": str(stubs), "LF_SCRIPT": str(script)}


def _yaml():
    return pytest.importorskip("yaml")


def _need(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(REPO_ROOT)} is missing"
    return path.read_text(encoding="utf-8")


# ================================================================== deploy.yml (static)


def _workflow() -> dict:
    data = _yaml().safe_load(_need(WORKFLOW))
    assert isinstance(data, dict)
    return data


def _on(wf: dict) -> dict:
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True
    assert isinstance(on, dict), "deploy.yml needs an `on:` mapping"
    return on


def _job(wf: dict) -> dict:
    jobs = wf.get("jobs") or {}
    assert jobs, "deploy.yml has no jobs"
    return next(iter(jobs.values()))


def _concurrency(wf: dict) -> dict:
    c = _job(wf).get("concurrency") or wf.get("concurrency")
    if isinstance(c, str):
        c = {"group": c}
    assert isinstance(c, dict), "no concurrency block"
    return c


def test_workflow_runs_after_ci_on_main_only_when_it_succeeded():
    wf = _workflow()
    wr = _on(wf).get("workflow_run")
    assert wr, "deploy.yml must trigger on workflow_run"
    assert list(wr["workflows"]) == ["CI"]
    assert list(wr.get("types") or []) == ["completed"]
    assert list(wr.get("branches") or []) == ["main"]
    cond = str(_job(wf).get("if", ""))
    assert re.search(r"workflow_run\.conclusion\s*==\s*'success'", cond), cond
    assert "workflow_dispatch" in cond or "event_name" in cond, "a manual run must not be blocked"


def test_workflow_can_be_started_by_hand_with_a_commit_input():
    wd = _on(_workflow()).get("workflow_dispatch")
    assert wd is not None, "deploy.yml must have workflow_dispatch"
    assert (wd or {}).get("inputs"), "workflow_dispatch needs an input for the commit sha"


def test_workflow_uses_the_production_environment_and_the_shared_host_lock():
    wf = _workflow()
    env = _job(wf).get("environment")
    assert (env.get("name") if isinstance(env, dict) else env) == "production"
    conc = _concurrency(wf)
    assert conc.get("group") == "server-lab-host"
    assert conc.get("cancel-in-progress") in (False, "false")


def test_workflow_token_can_read_packages_and_write_nothing():
    wf = _workflow()
    perms = {**(wf.get("permissions") or {}), **(_job(wf).get("permissions") or {})}
    assert perms.get("packages") == "read", perms
    assert "write" not in perms.values(), perms


def test_workflow_never_uses_a_secret_inside_a_run_script():
    wf = _workflow()
    for step in _job(wf)["steps"]:
        run = step.get("run", "")
        assert "${{" not in run, f"expression inside a run script: {step.get('name')}"
    text = _need(WORKFLOW)
    assert not re.search(r"set\s+-[a-z]*x|xtrace", text), "xtrace would print secrets"


def test_workflow_deploys_the_commit_sha_never_latest():
    text = _need(WORKFLOW)
    assert "head_sha" in text, "the workflow_run commit must be the tag"
    assert not re.search(r"LF_MANAGER_TAG\s*[=:]\s*['\"]?latest", text)
    assert "lf-manager:latest" not in text


def test_no_unquiet_compose_config_in_the_workflow_or_the_script():
    for f in (WORKFLOW, DEPLOY_SH):
        for line in _need(f).splitlines():
            if re.search(r"compose\b.*\bconfig\b", line):
                assert "--quiet" in line or re.search(r"\s-q\b", line), f"{f.name}: {line}"


# ------------------------------------------------ running the workflow's steps with stubs


def _write_exec(path: Path, body: str) -> None:
    path.write_bytes(body.replace("\r\n", "\n").encode())
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


SSH_STUB = r"""#!/usr/bin/env bash
d="${STUB_STATE:?}/calls"; mkdir -p "$d"
f=$(mktemp "$d/call.XXXXXX")
{ printf '%s\0' "$(basename "$0")"; printf '%s\0' "$@"; } > "$f.argv"
cat > "$f.stdin" 2>/dev/null || true
exit 0
"""


def _fake_for(expr: str) -> str:
    table = [
        ("SSH_PRIVATE_KEY", KEY_MATERIAL),
        ("SERVER_HOST_KEY", "ssh-ed25519 AAAAFAKEHOSTKEY"),
        ("SERVER_HOST", "server.example.invalid"),
        ("SERVER_USER", "root"),
        ("LF_DATABASE_URL", DSN),
        ("GITHUB_TOKEN", GHCR_TOKEN),
        ("github.token", GHCR_TOKEN),
        ("head_sha", NEW_TAG),
        ("github.sha", NEW_TAG),
        ("inputs.", NEW_TAG),
        ("github.actor", "lf-actor"),
    ]
    for needle, value in table:
        if needle in expr:
            return value
    return "1"


def _run_workflow_steps(tmp_path: Path, *, blank: tuple[str, ...] = ()) -> dict:
    """Run every `run:` step in order with stub ssh/scp/rsync. `blank` names secrets left empty."""
    wf = _workflow()
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name in ("ssh", "scp", "rsync", "ssh-keyscan", "ssh-keygen", "ssh-add", "sleep"):
        _write_exec(stubs / name, SSH_STUB if name != "sleep" else "#!/usr/bin/env bash\nexit 0\n")
    state = tmp_path / "state"
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    state.mkdir()
    out_file = runner_temp / "github_output"
    out_file.write_text("")
    logs = []
    for step in _job(wf)["steps"]:
        run = step.get("run")
        if not run:
            continue
        cond = str(step.get("if", ""))
        m = re.search(r"steps\.(\w+)\.outputs\.(\w+)\s*(==|!=)\s*'([^']*)'", cond)
        if m:
            got = ""
            for line in out_file.read_text().splitlines():
                if line.startswith(m.group(2) + "="):
                    got = line.split("=", 1)[1]
            if (got == m.group(4)) != (m.group(3) == "=="):
                continue
        env = {
            "PATH": str(stubs) + os.pathsep + os.environ.get("PATH", ""),
            "HOME": str(tmp_path),
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(out_file),
            "GITHUB_ENV": str(runner_temp / "github_env"),
            "GITHUB_STEP_SUMMARY": str(runner_temp / "summary"),
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_REPOSITORY": "Shashkist/Learning_factory",
            "GITHUB_WORKSPACE": str(REPO_ROOT),
            "GITHUB_SHA": NEW_TAG,
            "STUB_STATE": str(state),
        }
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        for key, expr in (step.get("env") or {}).items():
            val = _fake_for(str(expr))
            if any(b in str(expr) for b in blank):
                val = ""
            env[key] = val
        flags = "-eo pipefail" if step.get("shell") == "bash" else "-e"
        script = tmp_path / "step.sh"
        script.write_text(run, encoding="utf-8", newline="\n")
        cmd, extra_env = _bash_cmd(script, stubs, *flags.split())
        r = subprocess.run(
            cmd, env={**env, **extra_env}, cwd=REPO_ROOT, input="", capture_output=True,
            text=True, timeout=120,
        )
        logs.append((step.get("name"), r.returncode, r.stdout + r.stderr))
    calls = []
    for argv in sorted((state / "calls").glob("*.argv")) if (state / "calls").is_dir() else []:
        stdin = argv.with_suffix(".stdin")
        calls.append(
            {
                "argv": argv.read_bytes().decode().split("\0")[:-1],
                "stdin": stdin.read_text(errors="replace") if stdin.exists() else "",
            }
        )
    return {"steps": logs, "calls": calls}


@pytest.fixture
def full_run(tmp_path):
    return _run_workflow_steps(tmp_path)


def _deploy_call(run: dict) -> dict:
    cands = [c for c in run["calls"] if any("deploy.sh" in a for a in c["argv"])
             and c["argv"][0] == "ssh"]
    assert cands, f"no ssh call runs deploy.sh: {[c['argv'] for c in run['calls']]}"
    return cands[-1]


def test_missing_ssh_secrets_print_the_names_and_exit_zero(tmp_path):
    run = _run_workflow_steps(
        tmp_path, blank=("SSH_PRIVATE_KEY", "SERVER_HOST", "SERVER_USER", "SERVER_HOST_KEY")
    )
    out = "\n".join(o for _, _, o in run["steps"])
    for name, rc, o in run["steps"]:
        assert rc == 0, f"step {name} failed: {o}"
    for secret_name in ("SSH_PRIVATE_KEY", "SERVER_HOST", "SERVER_USER", "SERVER_HOST_KEY"):
        assert secret_name in out, f"{secret_name} is not named in the output"
    assert run["calls"] == [], "ssh/scp ran although the SSH secrets are missing"


def test_one_missing_secret_is_named_alone(tmp_path):
    run = _run_workflow_steps(tmp_path, blank=("SERVER_USER",))
    out = "\n".join(o for _, _, o in run["steps"])
    assert all(rc == 0 for _, rc, _ in run["steps"])
    assert "SERVER_USER" in out
    assert "SSH_PRIVATE_KEY" not in out
    assert run["calls"] == []


def test_database_url_travels_on_ssh_stdin_and_never_on_a_command_line(full_run):
    for name, rc, o in full_run["steps"]:
        assert rc == 0, f"step {name} failed: {o}"
        assert DSN not in o and DSN_PASSWORD not in o, f"step {name} printed the database URL"
    call = _deploy_call(full_run)
    assert f"LF_DATABASE_URL={DSN}" in call["stdin"]
    for c in full_run["calls"]:
        assert DSN not in " ".join(c["argv"]) and DSN_PASSWORD not in " ".join(c["argv"])


def test_ghcr_token_and_ssh_key_stay_off_command_lines(full_run):
    call = _deploy_call(full_run)
    assert GHCR_TOKEN in call["stdin"], "the job token must reach the server on stdin"
    for c in full_run["calls"]:
        joined = " ".join(c["argv"])
        assert GHCR_TOKEN not in joined and KEY_MATERIAL not in joined


def test_deploy_runs_the_sha_tag_and_never_latest(full_run):
    call = _deploy_call(full_run)
    argv = " ".join(call["argv"])
    assert NEW_TAG in argv or f"LF_MANAGER_TAG={NEW_TAG}" in call["stdin"], argv
    assert not re.search(r"LF_MANAGER_TAG=['\"]?latest", argv + call["stdin"])


def test_deploy_uses_one_ssh_connection_with_controlmaster(full_run):
    ssh_calls = [c for c in full_run["calls"] if c["argv"][0] in ("ssh", "scp", "rsync")]
    assert len(ssh_calls) >= 2
    for c in ssh_calls:
        joined = " ".join(c["argv"])
        assert "ControlPath" in joined, joined
    assert any("ControlMaster" in " ".join(c["argv"]) for c in ssh_calls)
    paths = {re.search(r"ControlPath=(\S+)", " ".join(c["argv"])).group(1) for c in ssh_calls}
    assert len(paths) == 1, "every call must share one control socket"


def test_deploy_sends_the_deploy_files_to_the_server_directory(full_run):
    joined = "\n".join(" ".join(c["argv"]) for c in full_run["calls"])
    assert "/root/learning-factory" in joined
    assert "deploy.sh" in joined


def test_missing_database_secret_still_deploys_and_sends_no_value(tmp_path):
    run = _run_workflow_steps(tmp_path, blank=("LF_DATABASE_URL",))
    assert all(rc == 0 for _, rc, _ in run["steps"])
    call = _deploy_call(run)
    assert not re.search(r"^LF_DATABASE_URL=\S", call["stdin"], re.M)


# ================================================================== deploy.sh (stubs on the runner)

DOCKER_STUB = r"""#!/usr/bin/env bash
S="${STUB_STATE:?}"
all="$*"
cur() { cat "$S/current_tag" 2>/dev/null || true; }
bad() { [ -n "${STUB_BAD_TAG:-}" ] && [ "$(cur)" = "$STUB_BAD_TAG" ]; }
case " $all " in
  *" compose "*)
    printf 'docker %s ## TAG=%s NET=%s PROXY=%s\n' "$all" "${LF_MANAGER_TAG-<unset>}" \
      "${LF_NGINX_NETWORK-<unset>}" "${LF_TRUSTED_PROXY-<unset>}" >> "$S/calls.log"
    case " $all " in
      *" version "*)
        case "$all" in
          *--short*) echo "${STUB_COMPOSE_VERSION:-2.40.0}" ;;
          *--format*) echo "${STUB_COMPOSE_VERSION:-2.40.0}" ;;
          *) echo "Docker Compose version v${STUB_COMPOSE_VERSION:-2.40.0}" ;;
        esac ;;
      *" up "*) echo "${LF_MANAGER_TAG:-}" > "$S/current_tag" ;;
      *" logs "*)
        [ -n "${STUB_NO_MIGRATION_LINE:-}" ] || \
          echo "lab-manager database ready, migrations applied: ['001_initial', '002_auth_sessions']" ;;
      *" config "*) echo "LF_DATABASE_URL=SHOULD-NEVER-BE-PRINTED" ;;
    esac
    exit 0 ;;
esac
printf 'docker %s\n' "$all" >> "$S/calls.log"
sub=""
for a in "$@"; do case "$a" in -*) ;; *) sub="$a"; break;; esac; done
fmt=""; target=""; seen=0
for ((i=1; i<=$#; i++)); do
  a="${!i}"
  if [ "$a" = "$sub" ] && [ $seen = 0 ]; then seen=1; continue; fi
  [ $seen = 1 ] || continue
  case "$a" in
    -f|--format) j=$((i+1)); fmt="${!j}"; i=$j ;;
    --format=*) fmt="${a#--format=}" ;;
    -*) ;;
    *) [ -n "$target" ] || target="$a" ;;
  esac
done
case "$sub" in
  version)
    v="${STUB_DOCKER_VERSION:-28.1.0}"
    if [ -n "$fmt" ]; then echo "$v"; else
      printf 'Client:\n Version:           %s\n\nServer: Docker Engine - Community\n Engine:\n  Version:          %s\n' "$v" "$v"
    fi ;;
  login) cat > "$S/login_stdin"; echo "Login Succeeded" ;;
  network)
    case " $all " in *" inspect "*|*" ls "*) ;; *) exit 0 ;; esac
    for a in "$@"; do case "$a" in -*|"$fmt") ;; *) target="$a" ;; esac; done
    if [ "$target" = lf-labs ] && [ -n "${STUB_NO_LABS_NET:-}" ]; then echo "no such network" >&2; exit 1; fi
    gw=172.20.0.1; [ "$target" = lf-labs ] && gw=172.31.240.1
    case "$fmt" in *Gateway*) echo "$gw" ;; *) echo "[{\"Name\":\"${target:-lf-labs}\",\"IPAM\":{\"Config\":[{\"Gateway\":\"$gw\"}]}}]"; echo "lf-labs" ;; esac ;;
  inspect)
    if [ "$target" = nginx-proxy3 ] && [ -n "${STUB_NO_NGINX:-}" ]; then echo "No such object" >&2; exit 1; fi
    health=healthy; running=true; status=running
    if [ "$target" = lf-manager ] && bad; then health=unhealthy; running=false; status=restarting; fi
    ip=172.20.0.5; [ "$target" = lf-manager ] && ip=172.20.0.9
    case "$fmt" in
      "") echo "[{\"Name\":\"/$target\",\"State\":{\"Running\":$running,\"Status\":\"$status\",\"Health\":{\"Status\":\"$health\"}},\"NetworkSettings\":{\"Networks\":{\"proxy_net\":{\"IPAddress\":\"$ip\",\"Gateway\":\"172.20.0.1\"}}}}]" ;;
      *"json .NetworkSettings.Networks"*) echo "{\"proxy_net\":{\"IPAddress\":\"$ip\",\"Gateway\":\"172.20.0.1\"}}" ;;
      *range*Networks*)
        case "$fmt" in
          *'{{$k}}'*IPAddress*|*IPAddress*'{{$k}}'*) echo "proxy_net $ip" ;;
          *IPAddress*) echo "$ip" ;;
          *) echo proxy_net ;;
        esac ;;
      *State.Health*) echo "$health" ;;
      *State.Running*) echo "$running" ;;
      *State.Status*) echo "$status" ;;
      *IPAddress*) echo "$ip" ;;
      *Networks*) echo proxy_net ;;
      *) echo "$target" ;;
    esac ;;
  ps)
    echo nginx-proxy3 | { [ -n "${STUB_NO_NGINX:-}" ] && cat >/dev/null || cat; }
    printf 'openclaw\nn8n\nlf-manager\n' ;;
  logs) [ -n "${STUB_NO_MIGRATION_LINE:-}" ] || \
          echo "lab-manager database ready, migrations applied: ['001_initial', '002_auth_sessions']"
        [ -n "${STUB_NO_PROXY_LINE:-}" ] || echo "trusted proxy nginx-proxy3 -> 172.20.0.5" ;;
  exec)
    case " $all " in
      *" ip "*)
        if [ -n "${STUB_ROUTE_LABS:-}" ]; then d="default via 172.31.240.1 dev eth1"; else d="default via 172.20.0.1 dev eth0"; fi
        case "$all" in *default*) echo "$d" ;; *) echo "$d"; echo "172.20.0.0/16 dev eth0 scope link"; echo "172.31.240.0/24 dev eth1 scope link" ;; esac ;;
      *) bad && exit 1; echo '{"ok": true}' ;;
    esac ;;
  *) ;;
esac
exit 0
"""

CURL_STUB = r"""#!/usr/bin/env bash
S="${STUB_STATE:?}"
printf 'curl %s\n' "$*" >> "$S/calls.log"
cur() { cat "$S/current_tag" 2>/dev/null || true; }
if [ -n "${STUB_BAD_TAG:-}" ] && [ "$(cur)" = "$STUB_BAD_TAG" ]; then exit 22; fi
case "$*" in
  *live.json*) printf 'HTTP/2 200\r\ncache-control: no-cache\r\nx-robots-tag: noindex\r\n\r\n' ;;
  */api/health*)
    if [ -n "${STUB_DB_DOWN:-}" ]; then echo '{"status":"degraded","database":"unavailable"}'
    else echo '{"status":"ok","database":"ok"}'; fi ;;
  */healthz*) echo '{"ok":true}' ;;
esac
case "$*" in *http_code*) printf '200' ;; esac
exit 0
"""

OPENSSL_STUB = r"""#!/usr/bin/env bash
S="${STUB_STATE:?}"
printf 'openssl %s\n' "$*" >> "$S/calls.log"
case "$*" in *"rand -hex 32"*) echo "__ID_SECRET__" ;; *) exit 0 ;; esac
""".replace("__ID_SECRET__", STUB_ID_SECRET)

LOG_STUB = r"""#!/usr/bin/env bash
printf '%s %s\n' "$(basename "$0")" "$*" >> "${STUB_STATE:?}/calls.log"
exit 0
"""

INSTALL_STUB = r"""#!/usr/bin/env bash
printf 'install-block %s\n' "$*" >> "${STUB_STATE:?}/calls.log"
exit 0
"""


class Server:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.root = tmp / "root"
        self.state = tmp / "state"
        self.bin = tmp / "bin"
        self.cwd = tmp / "cwd"
        for d in (self.root, self.state, self.bin, self.cwd):
            d.mkdir()
        for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB), ("openssl", OPENSSL_STUB),
                           ("systemctl", LOG_STUB), ("service", LOG_STUB)):
            _write_exec(self.bin / name, body)
        assert DEPLOY_SH.is_file(), "labs/deploy/deploy.sh is missing"
        shutil.copy(DEPLOY_SH, self.root / "deploy.sh")
        shutil.copy(DEPLOY_DIR / "compose.yml", self.root / "compose.yml")
        shutil.copytree(DEPLOY_DIR / "nginx", self.root / "nginx")
        _write_exec(self.root / "nginx" / "install-block.sh", INSTALL_STUB)
        self.labs_conf = self.root / "nginx" / "labs.conf"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    def env_values(self) -> dict[str, str]:
        out = {}
        for line in self.env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
        return out

    def run(self, stdin: str = "", tag: str | None = NEW_TAG, **extra: str):
        env = {
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "HOME": str(self.tmp),
            "LF_DEPLOY_ROOT": str(self.root),
            "LF_DOCKER": str(self.bin / "docker"),
            "LF_HEALTH_TIMEOUT": "3",
            "STUB_STATE": str(self.state),
        }
        if sys.platform == "win32":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        if tag is not None:
            env["LF_MANAGER_TAG"] = tag
        env.update(extra)
        # bytes in and out: text mode would rewrite line endings on Windows
        cmd, extra_env = _bash_cmd(self.root / "deploy.sh", self.bin)
        r = subprocess.run(
            cmd, env={**env, **extra_env}, cwd=self.cwd, input=stdin.encode(),
            capture_output=True, timeout=120,
        )
        return subprocess.CompletedProcess(
            r.args, r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")
        )

    def calls(self) -> list[str]:
        log = self.state / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def calls_of(self, prefix: str) -> list[str]:
        return [c for c in self.calls() if c.startswith(prefix)]

    def compose_up(self) -> list[str]:
        return [c for c in self.calls_of("docker compose") if re.search(r"\bup\b", c.split("##")[0])]


@pytest.fixture
def server(tmp_path) -> Server:
    return Server(tmp_path)


def _stdin(dsn: str | None = DSN, token: str | None = GHCR_TOKEN) -> str:
    lines = []
    if dsn is not None:
        lines.append(f"LF_DATABASE_URL={dsn}")
    lines += ["GHCR_USER=lf-actor"]
    if token is not None:
        lines.append(f"GHCR_TOKEN={token}")
    return "\n".join(lines) + "\n"


def _ok(r) -> None:
    assert r.returncode == 0, f"exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_script_creates_env_with_mode_600(server):
    _ok(server.run(_stdin()))
    assert stat.S_IMODE(server.env_file.stat().st_mode) == 0o600


def test_id_secret_is_generated_once_and_kept(server):
    _ok(server.run(_stdin()))
    first = server.env_values().get("LF_ID_SECRET")
    assert first == STUB_ID_SECRET
    _ok(server.run(_stdin()))
    assert server.env_values().get("LF_ID_SECRET") == first
    rand = [c for c in server.calls_of("openssl") if "rand" in c]
    assert len(rand) == 1 and "-hex 32" in rand[0], rand


def test_database_url_from_stdin_is_written_and_kept_when_stdin_is_empty(server):
    _ok(server.run(_stdin()))
    assert server.env_values().get("LF_DATABASE_URL") == DSN
    _ok(server.run(""))
    assert server.env_values().get("LF_DATABASE_URL") == DSN, "an empty stdin must not erase it"
    _ok(server.run(_stdin(dsn=DSN2)))
    assert server.env_values().get("LF_DATABASE_URL") == DSN2, "a new value replaces the old"


def test_no_secret_value_reaches_output_or_any_command_line(server):
    r = server.run(_stdin())
    _ok(r)
    seen = r.stdout + r.stderr + "\n".join(server.calls())
    for secret in (DSN, DSN_PASSWORD, STUB_ID_SECRET, GHCR_TOKEN):
        assert secret not in seen, "a secret value leaked"
    assert "SHOULD-NEVER-BE-PRINTED" not in seen


def test_compose_gets_tag_network_and_trusted_proxy_exported(server):
    _ok(server.run(_stdin()))
    up = server.compose_up()
    assert up, server.calls()
    assert re.search(r"\bup\b.*-d\b|-d\b.*\bup\b", up[0].split("##")[0])
    assert up[0].endswith(f"TAG={NEW_TAG} NET=proxy_net PROXY=nginx-proxy3"), up[0]
    for line in server.calls_of("docker compose"):
        if re.search(r"\bversion\b", line.split("##")[0]):
            continue
        assert "NET=proxy_net" in line and "PROXY=nginx-proxy3" in line, line


def test_images_are_pulled_through_a_stdin_login_before_up_and_the_login_is_removed(server):
    _ok(server.run(_stdin()))
    calls = server.calls()
    idx = lambda pred: next(i for i, c in enumerate(calls) if pred(c))  # noqa: E731
    login = idx(lambda c: c.startswith("docker") and re.search(r"\blogin\b", c) is not None)
    assert "--password-stdin" in calls[login] and "ghcr.io" in calls[login]
    assert (server.state / "login_stdin").read_text().strip() == GHCR_TOKEN
    pull_mgr = idx(lambda c: f"ghcr.io/shashkist/lf-manager:{NEW_TAG}" in c and "pull" in c)
    pull_lab = idx(lambda c: "ghcr.io/shashkist/lf-lab-term:latest" in c and "pull" in c)
    logout = idx(lambda c: c.startswith("docker") and re.search(r"\blogout\b", c) is not None)
    up = idx(lambda c: c.startswith("docker compose") and re.search(r"\bup\b", c.split("##")[0]))
    assert login < pull_mgr < up and login < pull_lab < up
    assert max(pull_mgr, pull_lab) < logout


@pytest.mark.parametrize("tag", ["", "latest"])
def test_deploy_refuses_a_missing_or_latest_tag(server, tag):
    r = server.run(_stdin(), tag=tag)
    assert r.returncode != 0
    assert server.compose_up() == []


@pytest.mark.parametrize(
    ("docker_v", "compose_v"),
    [("27.5.1", "2.40.0"), ("28.1.0", "2.33.0"), ("28.1.0", "2.4.0")],
)
def test_preflight_refuses_old_docker_or_compose(server, docker_v, compose_v):
    r = server.run(_stdin(), STUB_DOCKER_VERSION=docker_v, STUB_COMPOSE_VERSION=compose_v)
    assert r.returncode != 0, r.stdout
    assert server.compose_up() == [], "nothing may be started after a failed preflight"
    assert not server.calls_of("install-block")


@pytest.mark.parametrize(("docker_v", "compose_v"), [("28.0.0", "2.33.1"), ("29.1.0", "2.100.0")])
def test_preflight_accepts_the_minimum_and_newer_versions(server, docker_v, compose_v):
    _ok(server.run(_stdin(), STUB_DOCKER_VERSION=docker_v, STUB_COMPOSE_VERSION=compose_v))


@pytest.mark.parametrize("missing", ["STUB_NO_LABS_NET", "STUB_NO_NGINX"])
def test_preflight_refuses_without_the_lab_network_or_the_nginx_container(server, missing):
    r = server.run(_stdin(), **{missing: "1"})
    assert r.returncode != 0
    assert server.compose_up() == []


def test_deploy_prints_the_compose_version_and_the_migration_line(server):
    r = server.run(_stdin(), STUB_COMPOSE_VERSION="2.40.0")
    _ok(r)
    assert "2.40.0" in r.stdout
    assert "migrations applied" in r.stdout


def test_deploy_prints_the_managers_trusted_proxy_startup_line(server):
    r = server.run(_stdin())
    _ok(r)
    assert "trusted proxy nginx-proxy3 -> 172.20.0.5" in r.stdout + r.stderr


def test_deploy_does_not_pin_the_proxy_address_and_survives_a_missing_proxy_line(server):
    r = server.run(_stdin(), STUB_NO_PROXY_LINE="1")
    _ok(r)
    out = r.stdout + r.stderr
    # no manager startup line: nothing may pretend an address was found or pinned
    assert "->" not in out and "172.20.0.5" not in out
    assert "deployed lf-manager" in out
    assert "PROXY=172.20.0.5" not in " ".join(server.calls())


def test_missing_migration_line_in_the_manager_logs_fails_the_deploy(server):
    assert server.run(_stdin(), STUB_NO_MIGRATION_LINE="1").returncode != 0


def test_script_never_restarts_stops_or_removes_anything_and_stays_on_lf_containers(server):
    _ok(server.run(_stdin()))
    server.run(_stdin(), tag=OLD_TAG, STUB_BAD_TAG=OLD_TAG)  # the failure path too
    for line in server.calls():
        tool = line.split()[0]
        if tool in ("systemctl", "service"):
            assert "restart" not in line and "docker" not in line, line
        if tool == "docker" and not line.startswith("docker compose"):
            assert not re.search(r"\b(restart|stop|kill|rm|prune|pause|update)\b", line), line
            if re.search(r"\bexec\b", line):
                assert " lf-manager " in f" {line} ", f"exec outside lf-manager: {line}"
    for line in server.calls_of("docker compose"):
        cmd = line.split("##")[0]
        assert not re.search(r"\brestart\b", cmd), line
        if re.search(r"\bconfig\b", cmd):
            assert "--quiet" in cmd or re.search(r"\s-q\b", cmd), line


def test_nginx_blocks_for_labs_and_factory_are_installed_after_the_manager_is_up(server):
    _ok(server.run(_stdin()))
    calls = server.calls()
    inst = [c for c in calls if c.startswith("install-block ")]
    names = {c.split()[1]: c.split()[2] for c in inst}
    assert set(names) == {"labs", "factory"}, inst
    assert names["labs"].endswith("labs.conf") and names["factory"].endswith("factory.conf")
    first_up = next(i for i, c in enumerate(calls) if c in server.compose_up())
    assert all(calls.index(c) > first_up for c in inst)


def test_a_successful_deploy_records_the_tag(server):
    _ok(server.run(_stdin()))
    assert (server.root / "deployed-tag").read_text().strip() == NEW_TAG


def test_a_failed_health_check_redeploys_the_previous_tag_and_fails(server):
    (server.root / "deployed-tag").write_text(OLD_TAG + "\n")
    r = server.run(_stdin(), STUB_BAD_TAG=NEW_TAG)
    assert r.returncode != 0
    tags = [re.search(r"TAG=(\S*)", c).group(1) for c in server.compose_up()]
    assert tags[:2] == [NEW_TAG, OLD_TAG], tags
    assert (server.root / "deployed-tag").read_text().strip() == OLD_TAG
    assert not server.calls_of("install-block"), "no nginx change for a deploy that failed"


def test_a_failed_first_deploy_fails_without_starting_an_invented_tag(server):
    r = server.run(_stdin(), STUB_BAD_TAG=NEW_TAG)
    assert r.returncode != 0
    tags = [re.search(r"TAG=(\S*)", c).group(1) for c in server.compose_up()]
    assert tags and set(tags) <= {NEW_TAG}, tags


def test_term_upstream_test_seam_in_env_fails_the_deploy_without_echoing_it(server):
    server.env_file.write_text("LF_TERM_UPSTREAM=http://sentinel-term-upstream.invalid:7681\n")
    r = server.run(_stdin())
    assert r.returncode != 0
    assert "LF_TERM_UPSTREAM" in r.stdout + r.stderr
    assert "sentinel-term-upstream" not in r.stdout + r.stderr


def test_default_route_on_the_lab_network_fails_the_deploy(server):
    assert server.run(_stdin(), STUB_ROUTE_LABS="1").returncode != 0


def test_database_not_ok_in_api_health_fails_the_deploy(server):
    assert server.run(_stdin(), STUB_DB_DOWN="1").returncode != 0


def test_live_checks_go_through_curl_resolve_to_localhost(server):
    _ok(server.run(_stdin()))
    curls = server.calls_of("curl")
    labs = [c for c in curls if "labs.shashkist.com" in c]
    assert any("/healthz" in c for c in labs)
    assert any("/api/health" in c for c in labs)
    assert all("--resolve labs.shashkist.com:443:127.0.0.1" in c for c in labs), labs
    live = [c for c in curls if "factory.shashkist.com" in c and "live.json" in c]
    assert live and all("--resolve factory.shashkist.com:443:127.0.0.1" in c for c in live)


# ================================================================== labs.conf


def _labs() -> str:
    return _need(LABS_CONF)


def _block(text: str, header: str) -> str:
    m = re.search(re.escape(header) + r"[^{]*\{([^{}]*)\}", text)
    assert m, f"no `{header}` block"
    return m.group(1)


def test_labs_conf_is_one_marked_block_named_labs():
    lines = _labs().rstrip("\n").splitlines()
    assert lines[0] == "# BEGIN learning-factory:labs"
    assert lines[-1] == "# END learning-factory:labs"
    assert sum(1 for ln in lines if ln.startswith("# BEGIN")) == 1
    assert sum(1 for ln in lines if ln.startswith("# END")) == 1


def test_installer_accepts_labs_conf_and_leaves_the_rest_of_the_site_config_alone(tmp_path):
    labs = _labs()  # fails clearly when the file is missing
    conf = tmp_path / "default.conf"
    other = "server {\n    listen 443 ssl;\n    server_name other.example.invalid;\n}\n"
    placeholder = (
        "# BEGIN learning-factory:labs\nserver {\n    listen 443 ssl;\n"
        "    server_name labs.shashkist.com;\n    root /var/www/static/labs;\n}\n"
        "# END learning-factory:labs\n"
    )
    conf.write_text(other + "\n" + placeholder + "\n# tail\n", newline="\n")
    fake_docker = tmp_path / "docker"
    _write_exec(fake_docker, "#!/usr/bin/env bash\nexit 0\n")
    r = subprocess.run(
        [BASH, str(INSTALLER), "labs", str(LABS_CONF)],
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "LF_NGINX_CONF": str(conf),
             "LF_DOCKER": str(fake_docker),
             **({"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")} if sys.platform == "win32" else {})},
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    after = conf.read_text()
    assert after.startswith(other + "\n") and after.endswith("\n# tail\n")
    assert "root /var/www/static/labs" not in after, "the placeholder must be replaced"
    assert labs.strip().splitlines()[1] in after


def test_labs_conf_serves_labs_on_443_with_the_shared_certificate():
    text = _labs()
    assert re.search(r"^\s*server_name\s+labs\.shashkist\.com\s*;", text, re.M)
    assert re.search(r"^\s*listen\s+443\s+ssl\s*;", text, re.M)
    assert not re.search(r"listen\s+80\b|default_server", text)
    assert "ssl_certificate /etc/letsencrypt/live/teams.shashkist.com/fullchain.pem;" in text
    assert "ssl_certificate_key /etc/letsencrypt/live/teams.shashkist.com/privkey.pem;" in text


def test_labs_conf_resolves_the_manager_by_docker_dns_at_request_time():
    text = _labs()
    assert re.search(r"^\s*resolver\s+127\.0\.0\.11\b", text, re.M)
    assert re.search(r"^\s*set\s+\$upstream\s+http://lf-manager:8000\s*;", text, re.M)
    assert re.search(r"proxy_pass\s+\$upstream\s*;", text), "proxy_pass must use the variable"


def test_labs_conf_overwrites_x_forwarded_for_with_the_socket_address():
    text = _labs()
    assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr\s*;", text)
    assert "$proxy_add_x_forwarded_for" not in text, "a client could choose its own limiter key"


def test_labs_conf_upgrades_websockets_on_the_session_path():
    block = _block(_labs(), "location /s/")
    assert re.search(r"proxy_http_version\s+1\.1\s*;", block)
    assert re.search(r"proxy_set_header\s+Upgrade\s+\$http_upgrade\s*;", block)
    assert re.search(r"proxy_set_header\s+Connection\s+[\"']?(upgrade|\$connection_upgrade)", block, re.I)
    assert re.search(r"proxy_read_timeout\s+\d+", block), "a quiet terminal must not be cut at 60s"


def test_labs_conf_tells_robots_not_to_index():
    assert re.search(r"add_header\s+X-Robots-Tag\s+[\"']noindex[\"']\s+always\s*;", _labs())


# ================================================================== OWNER.md


def test_owner_md_has_the_create_admin_command():
    assert "docker exec -i lf-manager python -m app.cli create-admin" in _need(OWNER_MD)
