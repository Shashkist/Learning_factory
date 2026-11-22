"""Acceptance tests for issue #56: bootstrap log hygiene and the iptables=false guard.

(Review of #52.)

Interface under test: `labs/deploy/bootstrap/bootstrap.sh` (its output goes to a public Actions
log) and the lock refusal in `.github/workflows/bootstrap.yml`.

Harness (same idea as the #180 and #54 exams, no Docker needed): the real script is run under
`bash` as a copy whose absolute server paths (`/etc/docker`, `/run/lf-bootstrap.lock`,
`/usr/local/sbin`, `/etc/systemd/system`, `/usr/local/bin`, `/proc/`) point into a temp directory.
`docker`, `systemctl`, `iptables`, `runsc`, `dockerd`, `id`, `flock`, `swapon`, `pgrep` and
`apt-get` are stubs on PATH:
- `pgrep -xo dockerd` answers a fake pid and `<tmp>/proc/<pid>/cmdline` holds the NUL-separated
  command line of the fake dockerd (that is where the script reads the live flags);
- `docker info --format '{{json .Runtimes}}'` answers a fixed JSON, or what the last
  `systemctl reload docker` saw in daemon.json;
- `dockerd --help` lists `--validate`, `dockerd ... --validate` fails with a chosen message;
- `apt-get install -y jq` puts a real jq on PATH (the jq-missing scenarios hide the real one).
All data is fake. Needs Linux with `jq` and `bash`: the harness cases are skipped elsewhere and run
in the CI `manager-exam` job (not marked `docker`). The static cases and the D29 grep run
everywhere.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
REPO_ROOT = next(p for p in _HERE.parents if (p / "labs").is_dir() or (p / ".git").exists())
SCRIPT = REPO_ROOT / "labs" / "deploy" / "bootstrap" / "bootstrap.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "bootstrap.yml"
DECISIONS = REPO_ROOT / "docs" / "factory" / "DECISIONS.md"
RESULT = "BOOTSTRAP RESULT:"
OVERLAY = "--overlay2=root:memory"
PID = "4242"

needs_linux = pytest.mark.skipif(
    sys.platform != "linux" or not all(shutil.which(t) for t in ("bash", "jq")),
    reason="needs Linux with bash and jq",
)

_spec = importlib.util.spec_from_file_location(
    "lf_issue54_helpers", _HERE / "test_issue_54_bootstrap_workflow_shell.py"
)
assert _spec and _spec.loader
h54 = importlib.util.module_from_spec(_spec)
sys.modules["lf_issue54_helpers"] = h54
_spec.loader.exec_module(h54)


def _code() -> str:
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))


# ---------------------------------------------------------------- harness

DOCKER_STUB = r"""#!/bin/bash
echo "docker $*" >> "$STUB_DIR/seq.log"
case "$1" in
  info)
    if [ -f "$STUB_DIR/live-fixed" ]; then cat "$STUB_DIR/live-fixed"
    else cat "$STUB_DIR/live-runtimes" 2>/dev/null || echo '{}'; fi ;;
  network)
    case "$2" in
      inspect)
        case "$*" in
          *EnableIPv6*) echo "br-lf-labs false false 172.31.240.0/24 172.31.240.128/25" ;;
          *.Subnet*) echo "172.31.240.0/24 172.31.240.128/25" ;;
        esac ;;
    esac ;;
  run) echo "Hello from Docker!" ;;
esac
exit 0
"""

SYSTEMCTL_STUB = r"""#!/bin/bash
echo "systemctl $*" >> "$STUB_DIR/seq.log"
case "$1 $2" in
  "reload docker")
    jq -c '.runtimes // {}' "$STUB_DAEMON_JSON" > "$STUB_DIR/live-runtimes" 2>/dev/null \
      || echo '{}' > "$STUB_DIR/live-runtimes" ;;
esac
case "$1" in is-enabled) exit 1 ;; esac
exit 0
"""

DOCKERD_STUB = r"""#!/bin/bash
echo "dockerd $*" >> "$STUB_DIR/seq.log"
case "$*" in
  *--help*)
    echo "Usage: dockerd [OPTIONS]"
    echo "      --validate   Validate daemon configuration and exit"
    exit 0 ;;
  *--validate*)
    if [ -n "${STUB_VALIDATE_ERROR:-}" ]; then echo "$STUB_VALIDATE_ERROR" >&2; exit 1; fi
    exit 0 ;;
esac
exit 0
"""

APT_STUB = r"""#!/bin/bash
echo "apt-get $*" >> "$STUB_DIR/seq.log"
case "$*" in
  *install*jq*) [ -e "$STUB_BIN/jq" ] || ln -s "$REAL_JQ" "$STUB_BIN/jq" ;;
esac
exit 0
"""


def _logging_stub(name: str, body: str = "exit 0") -> str:
    return f'#!/bin/bash\necho "{name} $*" >> "$STUB_DIR/seq.log"\n{body}\n'


STUBS = {
    "docker": DOCKER_STUB,
    "systemctl": SYSTEMCTL_STUB,
    "dockerd": DOCKERD_STUB,
    "apt-get": APT_STUB,
    "iptables": _logging_stub("iptables"),
    "runsc": "#!/bin/bash\n[ \"$1\" = --version ] && echo 'runsc version exam'\nexit 0\n",
    "id": '#!/bin/bash\n[ "$1" = -u ] && echo 0\nexit 0\n',
    "flock": "#!/bin/bash\nexit 0\n",
    "swapon": "#!/bin/bash\necho '/swapfile-exam file 4G 0B -2'\nexit 0\n",
    "pgrep": f"#!/bin/bash\necho {PID}\nexit 0\n",
}


class _Host:
    """One run of the real bootstrap.sh against the stubs."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        daemon: dict | None,
        cmdline: list[str] | None = None,
        live: dict | None = None,
        dry: bool = True,
        jq: bool = True,
        validate_error: str = "",
    ) -> None:
        self.dir = tmp_path
        bindir = tmp_path / "bin"
        stubdir = tmp_path / "stub"
        self.etc_docker = tmp_path / "etc" / "docker"
        for d in (bindir, stubdir, self.etc_docker, tmp_path / "sbin", tmp_path / "systemd",
                  tmp_path / "usrbin", tmp_path / "proc"):
            d.mkdir(parents=True)
        for name, text in STUBS.items():
            (bindir / name).write_text(text)
            (bindir / name).chmod(0o755)
        (stubdir / "seq.log").write_text("")
        if live is not None:
            (stubdir / "live-fixed").write_text(json.dumps(live) + "\n")
        if cmdline is not None:
            (tmp_path / "proc" / PID).mkdir()
            (tmp_path / "proc" / PID / "cmdline").write_bytes(
                b"".join(a.encode() + b"\0" for a in cmdline)
            )
        self.daemon_json = self.etc_docker / "daemon.json"
        self.seed: bytes | None = None
        if daemon is not None:
            self.seed = (json.dumps(daemon, indent=2) + "\n").encode()
            self.daemon_json.write_bytes(self.seed)

        text = SCRIPT.read_text(encoding="utf-8")
        for server_path, local in (
            ("/etc/docker", self.etc_docker),
            ("/run/lf-bootstrap.lock", tmp_path / "bootstrap.lock"),
            ("/usr/local/sbin", tmp_path / "sbin"),
            ("/etc/systemd/system", tmp_path / "systemd"),
            ("/usr/local/bin", tmp_path / "usrbin"),
            ("/proc/", f"{tmp_path}/proc/"),
        ):
            text = text.replace(server_path, str(local))
        script = tmp_path / "bootstrap.sh"
        script.write_text(text)

        real_jq = shutil.which("jq")
        path = f"{bindir}{os.pathsep}{os.environ['PATH']}"
        if not jq:
            # Every tool of the real PATH except jq, so that `command -v jq` fails.
            realbin = tmp_path / "realbin"
            realbin.mkdir()
            for d in os.environ["PATH"].split(os.pathsep):
                if not os.path.isdir(d):
                    continue
                for entry in os.listdir(d):
                    if entry != "jq" and not (realbin / entry).exists():
                        try:
                            os.symlink(os.path.join(d, entry), realbin / entry)
                        except OSError:
                            pass
            path = f"{bindir}{os.pathsep}{realbin}"
        env = {
            "PATH": path,
            "HOME": str(tmp_path),
            "STUB_DIR": str(stubdir),
            "STUB_BIN": str(bindir),
            "REAL_JQ": real_jq or "",
            "STUB_DAEMON_JSON": str(self.daemon_json),
            "STUB_VALIDATE_ERROR": validate_error,
            "LF_BASELINE_URLS": "",
            "LF_BASELINE_TCP": "",
            "LF_SWAP_MIN_FREE_GB": "1",
            "DRY_RUN": "1" if dry else "0",
        }
        self.res = subprocess.run(
            ["bash", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True,
            timeout=300,
        )
        self.stdout = self.res.stdout
        self.out = self.res.stdout + self.res.stderr
        self.seq = (stubdir / "seq.log").read_text().splitlines()
        self.last_line = next(
            (ln.strip() for ln in reversed(self.res.stdout.splitlines()) if ln.strip()), ""
        )
        self.after: bytes | None = (
            self.daemon_json.read_bytes() if self.daemon_json.exists() else None
        )

    def tail(self) -> str:
        return "---- script output (tail) ----\n" + self.out[-3000:]

    def section(self, header: str) -> str:
        """The report lines after a `-- <header>` line, up to the next `--`/`==` line."""
        lines = self.out.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("-- ") and header in ln:
                body = []
                for nxt in lines[i + 1:]:
                    if nxt.startswith("-- ") or nxt.startswith("== "):
                        break
                    body.append(nxt)
                return "\n".join(body)
        raise AssertionError(f"no report section '{header}'\n" + self.tail())


def _assert_no_change(h: _Host) -> None:
    assert h.after == h.seed, "daemon.json changed\n" + h.tail()
    assert [p.name for p in h.etc_docker.iterdir()] == (["daemon.json"] if h.seed else []), \
        "files were left in /etc/docker\n" + h.tail()
    for bad in ("systemctl reload", "docker run", "docker network create", "docker pull"):
        assert not [c for c in h.seq if c.startswith(bad)], f"'{bad}' ran\n" + h.tail()


# ---------------------------------------------------------------- 1: daemon.json diff

NEIGHBOURS = {
    "log-driver": "exam-log-driver-value",
    "registry-mirrors": ["https://exam-user:EXAMSECRET@mirror.invalid"],
    "labels": ["exam-label-value"],
    "runtimes": {"other": {"path": "/usr/bin/exam-other"}},
}


def _diff_lines(out: str) -> list[str]:
    start = next(i for i, ln in enumerate(out.splitlines()) if "diff" in ln and "planned" in ln)
    return out.splitlines()[start + 1:]


@needs_linux
def test_dry_run_diff_shows_only_the_runtimes_key_and_no_neighbouring_values(tmp_path):
    h = _Host(tmp_path, daemon=NEIGHBOURS, dry=True)
    assert h.res.returncode == 0, h.tail()
    assert h.last_line == f"{RESULT} dry run", h.tail()
    for leaked in ("EXAMSECRET", "exam-log-driver-value", "exam-label-value", "mirror.invalid"):
        assert leaked not in h.out, f"'{leaked}' (a neighbouring value) is in the log\n" + h.tail()
    diff = "\n".join(_diff_lines(h.out))
    assert re.search(r'^\+\s+"runsc"', diff, re.M), "no runsc entry in the diff\n" + h.tail()
    assert "exam-other" in diff, "the diff lacks the runtimes key\n" + h.tail()


@needs_linux
def test_applied_run_diff_shows_only_the_runtimes_key_and_no_neighbouring_values(tmp_path):
    h = _Host(tmp_path, daemon=NEIGHBOURS, dry=False)
    for leaked in ("EXAMSECRET", "exam-log-driver-value", "exam-label-value", "mirror.invalid"):
        assert leaked not in h.out, f"'{leaked}' (a neighbouring value) is in the log\n" + h.tail()
    assert h.after is not None
    written = json.loads(h.after)
    for key, value in NEIGHBOURS.items():
        if key != "runtimes":
            assert written.get(key) == value, f"the run changed {key}\n" + h.tail()
    assert "runsc" in written.get("runtimes", {}), h.tail()


# ---------------------------------------------------------------- 2: dockerd command line

CMDLINE = [
    "dockerd", "-H", "fd://", "--host=tcp://EXAMHOST:2375", "-H", "tcp://EXAMHOST2.invalid:2376",
    "-H", "unix:///var/run/docker.sock", "--host=unix:///run/exam-extra.sock",
    "--iptables=true", "--ip-forward=true", "--live-restore=true",
    "--log-opt=syslog-address=tcp://exam-user:EXAMSECRET@log.invalid",
]


@needs_linux
def test_dockerd_host_values_are_printed_only_for_fd_and_unix(tmp_path):
    h = _Host(tmp_path, daemon=None, cmdline=CMDLINE)
    assert h.res.returncode == 0, h.tail()
    line = h.section("live dockerd command line")
    assert "fd://" in line and "unix:///var/run/docker.sock" in line, line
    assert "unix:///run/exam-extra.sock" in line, "--host=unix:// value is masked\n" + line
    for leaked in ("EXAMHOST", "EXAMHOST2"):
        assert leaked not in h.out, f"a tcp host value ('{leaked}') is in the log\n" + h.tail()


@needs_linux
def test_dockerd_iptables_ip_forward_and_live_restore_show_their_values(tmp_path):
    h = _Host(tmp_path, daemon=None, cmdline=CMDLINE)
    line = h.section("live dockerd command line")
    for want in ("--iptables=true", "--ip-forward=true", "--live-restore=true"):
        assert want in line, f"'{want}' is not shown\n" + line


@needs_linux
def test_dockerd_other_flag_values_stay_redacted(tmp_path):
    h = _Host(tmp_path, daemon=None, cmdline=CMDLINE)
    assert "EXAMSECRET" not in h.out and "log.invalid" not in h.out, h.tail()


# ---------------------------------------------------------------- 3: iptables=false guard

IPTABLES_FALSE_CASES = {
    "flag": dict(daemon={"log-driver": "json-file"}, cmdline=["dockerd", "--iptables=false"]),
    "file": dict(daemon={"iptables": False}, cmdline=["dockerd", "-H", "fd://"]),
}


@needs_linux
@pytest.mark.parametrize("how", sorted(IPTABLES_FALSE_CASES))
def test_run_fails_when_dockerd_has_iptables_false_in_a_dry_run(tmp_path, how):
    h = _Host(tmp_path, dry=True, **IPTABLES_FALSE_CASES[how])
    assert h.res.returncode != 0, "a dry run passed with iptables=false\n" + h.tail()
    assert h.last_line == f"{RESULT} failed", h.tail()
    assert "iptables" in h.out and "DOCKER-USER" in h.out, \
        "the message does not say why (lab isolation relies on DOCKER-USER)\n" + h.tail()
    _assert_no_change(h)


@needs_linux
@pytest.mark.parametrize("how", sorted(IPTABLES_FALSE_CASES))
def test_run_fails_before_any_change_when_dockerd_has_iptables_false(tmp_path, how):
    h = _Host(tmp_path, dry=False, **IPTABLES_FALSE_CASES[how])
    assert h.res.returncode != 0, "an apply run passed with iptables=false\n" + h.tail()
    assert h.last_line == f"{RESULT} failed", h.tail()
    assert "iptables" in h.out and "DOCKER-USER" in h.out, h.tail()
    _assert_no_change(h)
    assert not [c for c in h.seq if c.startswith("iptables") and re.search(r" -[IAN] ", c)], \
        "an iptables rule was added\n" + h.tail()


@needs_linux
@pytest.mark.parametrize("how", ["flag", "file"])
def test_iptables_true_does_not_fail_the_run(tmp_path, how):
    kw = (dict(daemon={"iptables": True}, cmdline=["dockerd"]) if how == "file"
          else dict(daemon=None, cmdline=["dockerd", "--iptables=true"]))
    h = _Host(tmp_path, dry=True, **kw)
    assert h.res.returncode == 0, h.tail()
    assert h.last_line == f"{RESULT} dry run", h.tail()


# ---------------------------------------------------------------- 4: --validate errors masked


@needs_linux
def test_dockerd_validate_errors_are_printed_with_their_values_masked(tmp_path):
    msg = ("unable to configure the Docker daemon with file daemon.json: invalid value "
           "https://exam-user:EXAMSECRET@mirror.invalid for registry-mirrors "
           "(from flag: exam-flag-value-9, from file: exam-file-value-9)")
    h = _Host(tmp_path, daemon={"log-driver": "json-file"}, dry=False, validate_error=msg)
    assert any(c.startswith("dockerd") and "--validate" in c for c in h.seq), \
        "exam precondition: the candidate was validated\n" + h.tail()
    assert h.res.returncode != 0, "the run passed although --validate failed\n" + h.tail()
    assert h.last_line == f"{RESULT} failed", h.tail()
    for leaked in ("EXAMSECRET", "mirror.invalid", "exam-flag-value-9", "exam-file-value-9"):
        assert leaked not in h.out, f"'{leaked}' (a validate error value) is logged\n" + h.tail()
    assert h.after == h.seed, "daemon.json was replaced although --validate failed\n" + h.tail()


# ---------------------------------------------------------------- 5: jq, no fallbacks


def test_script_has_no_grep_or_substring_fallback_for_jq():
    code = _code()
    assert not re.search(r"grep[^\n]*(\"runsc\"|RUNSC_OVERLAY)", code), \
        "a grep fallback on daemon.json for runsc or the overlay arg is still there"
    assert not re.search(r"\[\[\s*\"\$info\"\s*==", code), \
        "a substring match on the docker info JSON is still there"
    assert not re.search(r"jq is not installed", code, re.I), "a jq-missing message is still there"
    assert not re.search(r"(elif|else)[^\n]*command -v jq", code), "an if/else on jq is still there"


def test_runsc_loaded_overlay_reads_only_the_runsc_key():
    code = _code()
    start = code.index("runsc_loaded_overlay() {")
    body = code[start: code.index("\n}\n", start)]
    assert ".runsc" in body and "jq" in body, body
    assert not re.search(r"\.\[\]|to_entries|\.\*|map\(", body), \
        "the check looks at runtimes other than runsc\n" + body


@needs_linux
def test_jq_is_installed_before_the_runtime_step_is_decided(tmp_path):
    h = _Host(tmp_path, daemon={"log-driver": "json-file"}, dry=False, jq=False)
    assert "jq is not installed" not in h.out.lower(), h.tail()
    install = [i for i, ln in enumerate(h.out.splitlines())
               if "apt-get" in ln and "install" in ln and "jq" in ln and ln.startswith("+")]
    step = [i for i, ln in enumerate(h.out.splitlines()) if ln.startswith("== step runtime")]
    assert install, "jq was never installed with apt-get\n" + h.tail()
    assert step, h.tail()
    assert install[0] < step[0], "jq is installed only after the runtime step started\n" + h.tail()
    assert h.res.returncode == 0, h.tail()


@needs_linux
def test_overlay_arg_under_another_runtime_does_not_count_as_runsc_overlay(tmp_path):
    live = {
        "runc": {"path": "runc"},
        "exam-other": {"path": "/usr/bin/exam-other", "runtimeArgs": [OVERLAY]},
        "runsc": {"path": "/usr/local/bin/runsc"},
    }
    h = _Host(tmp_path, daemon={"log-driver": "json-file"}, live=live, dry=False, jq=False)
    assert h.res.returncode != 0, \
        "the run passed: the overlay arg of another runtime was taken for runsc's\n" + h.tail()
    assert h.last_line == f"{RESULT} failed", h.tail()
    assert h.after == h.seed, "daemon.json was touched\n" + h.tail()


# ---------------------------------------------------------------- 6: live runtimes

FEATURES = '{"org.opencontainers.runtime-spec.features":"{\\"ociVersionMax\\":\\"EXAMFEATURE\\"}"}'
LIVE = {
    "runc": {"path": "runc", "status": json.loads(FEATURES)},
    "runsc": {
        "path": "/usr/local/bin/runsc",
        "runtimeArgs": [OVERLAY],
        "status": json.loads(FEATURES),
    },
}


@needs_linux
def test_live_runtimes_in_the_report_show_names_and_paths_only(tmp_path):
    h = _Host(tmp_path, daemon=None, live=LIVE, cmdline=["dockerd", "-H", "fd://"])
    assert h.res.returncode == 0, h.tail()
    section = h.section("live docker runtimes")
    assert "runc" in section and "runsc" in section, section
    assert "/usr/local/bin/runsc" in section, "the runsc path is not shown\n" + section
    assert "EXAMFEATURE" not in h.out and "ociVersionMax" not in h.out, \
        "the docker info feature JSON is in the log\n" + h.tail()


@needs_linux
def test_live_runtimes_in_the_flag_registered_failure_show_names_and_paths_only(tmp_path):
    live = {"runsc": {"path": "/usr/local/bin/runsc", "status": json.loads(FEATURES)}}
    h = _Host(tmp_path, daemon={"log-driver": "json-file"}, live=live, dry=False,
              cmdline=["dockerd", "-H", "fd://", "--add-runtime", "runsc=/usr/local/bin/runsc"])
    assert h.res.returncode != 0, h.tail()
    assert "EXAMFEATURE" not in h.out and "ociVersionMax" not in h.out, h.tail()


# ---------------------------------------------------------------- 7: lock held, orphan log

SERVER_LOCK = h54.SERVER_LOCK
SERVER_DEST = h54.SERVER_DEST

SSH_STUB = r"""#!/bin/bash
# stands in for ssh: the last argument is the remote command; run it here with sh -c.
echo "ssh $*" >> "$STUB_LOG"
cmd="${@: -1}"
cmd="${cmd//$SERVER_LOCK/$STUB_LOCK}"
case "$cmd" in
  *sudo*|*"rm "*|*reboot*|*"$SERVER_DEST"*)
    echo "ssh stub: refusing remote command: $cmd" >&2; exit 99 ;;
esac
exec sh -c "$cmd"
"""


def _lock_run(tmp_path: Path, *, hold_lock: bool, orphans: bool) -> SimpleNamespace:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, text in (("ssh", SSH_STUB), ("scp", h54.SCP_STUB)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    stub = tmp_path / "stub-bootstrap.sh"
    stub.write_text("#!/bin/bash\necho 'BOOTSTRAP RESULT: stub'\nexit 0\n")
    lock = tmp_path / "bootstrap.lock"
    dest_root = tmp_path / "server"
    dest_root.mkdir()
    base = time.time() - 100000
    layout = [  # (name, has rc, log text), oldest first
        ("run-100-1", True, "FINISHED-RUN-LOG-LINE"),
        ("run-200-1", False, "ORPHAN-OLDER-LOG-LINE"),
        ("run-300-1", False, "ORPHAN-NEWEST-LOG-LINE"),
        ("run-400-1", True, "FINISHED-NEWEST-RUN-LOG-LINE"),
    ]
    for i, (name, has_rc, text) in enumerate(layout if orphans else layout[:1] + layout[3:]):
        d = dest_root / name
        d.mkdir()
        (d / "bootstrap.log").write_text(f"{text}\n")
        if has_rc:
            (d / "bootstrap.rc").write_text("0\n")
        for p in (*d.iterdir(), d):
            os.utime(p, (base + i * 1000, base + i * 1000))
    calls = tmp_path / "calls.log"
    calls.write_text("")
    script = tmp_path / "step.sh"
    script.write_text(h54._step_text().replace(SERVER_DEST, str(dest_root)))
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path), "RUNNER_TEMP": str(tmp_path),
        "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1",
        "SSH_PRIVATE_KEY": "not-a-key", "SERVER_HOST": "server.invalid",
        "SERVER_USER": "root", "SERVER_HOST_KEY": "ssh-ed25519 AAAA",
        "INPUT_APPLY": "false", "HEAD_COMMIT_MESSAGE": "chore: test",
        "STUB_LOG": str(calls), "STUB_LOCK": str(lock), "STUB_SCRIPT": str(stub),
        "SERVER_LOCK": SERVER_LOCK, "SERVER_DEST": SERVER_DEST,
    }
    holder = None
    if hold_lock:
        import fcntl  # Linux only (see needs_linux)

        holder = open(lock, "w")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        res = subprocess.run(
            ["bash", "-e", "-o", "pipefail", str(script)], cwd=REPO_ROOT, env=env,
            capture_output=True, text=True, timeout=120,
        )
    finally:
        if holder:
            holder.close()
    return SimpleNamespace(res=res, out=res.stdout + res.stderr, calls=calls.read_text())


@needs_linux
def test_lock_refusal_prints_the_log_of_the_newest_run_without_bootstrap_rc(tmp_path):
    r = _lock_run(tmp_path, hold_lock=True, orphans=True)
    assert r.res.returncode != 0, "the step passed with the lock held\n" + r.out[-2000:]
    assert "still running" in r.out, r.out[-2000:]
    assert "scp " not in r.calls, "the script was copied although the lock was held"
    assert "ORPHAN-NEWEST-LOG-LINE" in r.out, \
        "the orphaned run's log was not printed\n" + r.out[-2000:]


@needs_linux
def test_lock_refusal_prints_no_other_run_directory_log(tmp_path):
    r = _lock_run(tmp_path, hold_lock=True, orphans=True)
    assert "ORPHAN-NEWEST-LOG-LINE" in r.out, "exam precondition\n" + r.out[-2000:]
    for other in ("ORPHAN-OLDER-LOG-LINE", "FINISHED-RUN-LOG-LINE",
                  "FINISHED-NEWEST-RUN-LOG-LINE"):
        assert other not in r.out, f"'{other}' is a log of a run that is not the newest orphan"


@needs_linux
def test_lock_refusal_without_an_orphan_directory_still_refuses_cleanly(tmp_path):
    r = _lock_run(tmp_path, hold_lock=True, orphans=False)
    assert r.res.returncode != 0, r.out[-2000:]
    assert "still running" in r.out, r.out[-2000:]
    assert "scp " not in r.calls, "the script was copied although the lock was held"
    assert "FINISHED-RUN-LOG-LINE" not in r.out and "FINISHED-NEWEST-RUN-LOG-LINE" not in r.out


@needs_linux
def test_no_orphan_log_is_printed_when_the_lock_is_free(tmp_path):
    r = _lock_run(tmp_path, hold_lock=False, orphans=True)
    assert r.res.returncode == 0, r.out[-2000:]
    assert "ORPHAN-NEWEST-LOG-LINE" not in r.out, r.out[-2000:]


# ---------------------------------------------------------------- 8: D29 records the layout


def _d29() -> str:
    text = DECISIONS.read_text(encoding="utf-8")
    start = text.index("## D29 ")
    nxt = re.search(r"^## D\d+ ", text[start + 5:], re.M)
    return text[start: start + 5 + nxt.start()] if nxt else text[start:]


def test_d29_records_the_bootstrap_lock_path():
    assert "/run/lf-bootstrap.lock" in _d29()


def test_d29_records_the_per_run_directory_layout():
    d29 = _d29()
    assert "/root/lf-bootstrap/run-" in d29, "the run directory path is not recorded"
    for name in ("bootstrap.sh", "bootstrap.log", "bootstrap.rc"):
        assert name in d29, f"{name} is not named in the per-run layout"
