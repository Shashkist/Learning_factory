"""Acceptance tests for issue #180: the bootstrap runtime check retries one failed runsc start.

Interface under test: `labs/deploy/bootstrap/bootstrap.sh`, step `runtime`. After `systemctl
reload docker` the script runs a test container (`docker run --rm --name lf-runsc-check
--runtime=runsc hello-world`). Right after a reload the first start can fail with
`OCI runtime start failed ... oom_score_adj: no such process`. The script then retries that one
`docker run` once (exactly one log line says so); if the second try fails too, the step fails and
rolls back as before.

Harness (same idea as the #54 exam, which runs the workflow text against stubs): the real script is
executed under `bash` on this machine, as a copy whose absolute server paths (`/etc/docker`,
`/run/lf-bootstrap.lock`, `/usr/local/sbin`, `/etc/systemd/system`, `/usr/local/bin`) point into
a temp directory. `docker`, `systemctl`, `iptables`, `runsc`, `dockerd`, `id`, `flock`, `swapon`
and `pgrep` are stubs on PATH. The `docker` stub records every call, answers `docker info` from
the daemon.json that the last `systemctl reload docker` saw (so a rollback that reloads docker
really makes runsc disappear), and fails the first N `docker run --runtime=runsc` calls with the
oom_score_adj message. Nothing real is touched, no Docker is needed.

Needs Linux with `jq` and `bash`: skipped elsewhere, runs in the CI `manager` job.
"""

import json
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
SCRIPT = REPO_ROOT / "labs" / "deploy" / "bootstrap" / "bootstrap.sh"
RESULT = "BOOTSTRAP RESULT:"
OVERLAY = "--overlay2=root:memory"
OOM_MESSAGE = (
    "docker: Error response from daemon: failed to create task for container: failed to create "
    "shim task: OCI runtime start failed: write /proc/self/oom_score_adj: no such process: unknown"
)
# A line that says "retrying" in any of its usual spellings. No bare digit strings (D62).
RETRY_LINE = re.compile(r"\bretr(y|ied|ying)\b|\btry(ing)? again\b", re.I)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not all(shutil.which(t) for t in ("bash", "jq")),
    reason="needs Linux with bash and jq",
)

DOCKER_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_DIR/docker.calls"
runs() { grep -c -E '^run .*--runtime=runsc' "$STUB_DIR/docker.calls" || true; }
case "$1" in
  info) cat "$STUB_DIR/live-runtimes" 2>/dev/null || echo '{}' ;;
  run)
    case "$*" in
      *--runtime=runsc*)
        if [ "$(runs)" -le "$STUB_RUNSC_FAILS" ]; then
          echo "$STUB_OOM_MESSAGE" >&2
          exit 125
        fi
        echo "Hello from Docker!" ;;
    esac ;;
  network)
    case "$2" in
      inspect)
        case "$*" in
          *EnableIPv6*) echo "br-lf-labs false false 172.31.240.0/24 172.31.240.128/25" ;;
          *.Subnet*) echo "172.31.240.0/24 172.31.240.128/25" ;;
        esac ;;
    esac ;;
esac
exit 0
"""

SYSTEMCTL_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_DIR/systemctl.calls"
case "$1 $2" in
  "reload docker")
    # What a reload does: docker now knows the runtimes of the file as it is at this moment.
    jq -c '.runtimes // {}' "$STUB_DAEMON_JSON" > "$STUB_DIR/live-runtimes" 2>/dev/null \
      || echo '{}' > "$STUB_DIR/live-runtimes" ;;
esac
case "$1" in is-enabled) exit 1 ;; esac
exit 0
"""

STUBS = {
    "docker": DOCKER_STUB,
    "systemctl": SYSTEMCTL_STUB,
    "iptables": "#!/bin/bash\nexit 0\n",
    "runsc": "#!/bin/bash\n[ \"$1\" = --version ] && echo 'runsc version exam'\nexit 0\n",
    "dockerd": "#!/bin/bash\nexit 0\n",
    "id": '#!/bin/bash\n[ "$1" = -u ] && echo 0\nexit 0\n',
    "flock": "#!/bin/bash\nexit 0\n",
    "swapon": "#!/bin/bash\necho '/swapfile-exam file 4G 0B -2'\nexit 0\n",
    "pgrep": "#!/bin/bash\nexit 1\n",
}

SEED = {"max-concurrent-uploads": 4}


class _Run:
    def __init__(self, tmp_path: Path, runsc_fails: int) -> None:
        self.dir = tmp_path
        bindir = tmp_path / "bin"
        stubdir = tmp_path / "stub"
        etc_docker = tmp_path / "etc" / "docker"
        for d in (
            bindir,
            stubdir,
            etc_docker,
            tmp_path / "sbin",
            tmp_path / "systemd",
            tmp_path / "usrbin",
        ):
            d.mkdir(parents=True)
        for name, text in STUBS.items():
            (bindir / name).write_text(text)
            (bindir / name).chmod(0o755)
        self.daemon_json = etc_docker / "daemon.json"
        self.seed = (json.dumps(SEED, indent=2) + "\n").encode()
        self.daemon_json.write_bytes(self.seed)
        (stubdir / "docker.calls").write_text("")

        text = SCRIPT.read_text(encoding="utf-8")
        for server_path, local in (
            ("/etc/docker", etc_docker),
            ("/run/lf-bootstrap.lock", tmp_path / "bootstrap.lock"),
            ("/usr/local/sbin", tmp_path / "sbin"),
            ("/etc/systemd/system", tmp_path / "systemd"),
            ("/usr/local/bin", tmp_path / "usrbin"),
        ):
            text = text.replace(server_path, str(local))
        script = tmp_path / "bootstrap.sh"
        script.write_text(text)

        env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "STUB_DIR": str(stubdir),
            "STUB_DAEMON_JSON": str(self.daemon_json),
            "STUB_RUNSC_FAILS": str(runsc_fails),
            "STUB_OOM_MESSAGE": OOM_MESSAGE,
            "LF_BASELINE_URLS": "",
            "LF_BASELINE_TCP": "",
            "LF_SWAP_MIN_FREE_GB": "1",
        }
        self.res = subprocess.run(
            ["bash", str(script)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.out = self.res.stdout + self.res.stderr
        self.docker_calls = (stubdir / "docker.calls").read_text().splitlines()
        sc = stubdir / "systemctl.calls"
        self.systemctl_calls = sc.read_text().splitlines() if sc.exists() else []
        self.runsc_runs = [
            c for c in self.docker_calls if c.startswith("run ") and "--runtime=runsc" in c
        ]
        self.retry_lines = [ln for ln in self.out.splitlines() if RETRY_LINE.search(ln)]
        self.reloads = [c for c in self.systemctl_calls if c == "reload docker"]
        self.last_line = next(
            (ln.strip() for ln in reversed(self.res.stdout.splitlines()) if ln.strip()), ""
        )

    def tail(self) -> str:
        return "---- script output (tail) ----\n" + self.out[-2500:]


def _daemon_has_runsc(r: _Run) -> bool:
    return b'"runsc"' in r.daemon_json.read_bytes()


# ---------------------------------------------------------------- first start fails, second works


def test_first_runsc_start_fails_second_succeeds_the_runtime_step_passes(tmp_path):
    r = _Run(tmp_path, runsc_fails=1)
    assert len(r.runsc_runs) >= 1, "exam precondition: the runtime check ran\n" + r.tail()
    assert r.res.returncode == 0, "the step failed on a single failed start\n" + r.tail()
    assert r.last_line == f"{RESULT} changed", r.tail()
    assert "step runtime failed" not in r.out and "rolling it back" not in r.out, r.tail()


def test_first_runsc_start_fails_second_succeeds_runtime_check_is_run_exactly_twice(tmp_path):
    r = _Run(tmp_path, runsc_fails=1)
    assert len(r.runsc_runs) == 2, (
        f"expected the failed start and one retry, got {r.runsc_runs}\n" + r.tail()
    )
    assert all("lf-runsc-check" in c for c in r.runsc_runs), r.runsc_runs


def test_first_runsc_start_fails_second_succeeds_one_log_line_says_it_retried(tmp_path):
    r = _Run(tmp_path, runsc_fails=1)
    assert r.res.returncode == 0, r.tail()
    assert len(r.retry_lines) == 1, (
        f"expected exactly one line about the retry, got {r.retry_lines}\n" + r.tail()
    )


def test_first_runsc_start_fails_second_succeeds_no_rollback_runsc_stays_registered(tmp_path):
    r = _Run(tmp_path, runsc_fails=1)
    assert r.res.returncode == 0, r.tail()
    assert "rolling back" not in r.out, r.tail()
    assert _daemon_has_runsc(r), "daemon.json lost runsc although the step passed\n" + r.tail()
    assert OVERLAY in r.daemon_json.read_text(), r.tail()
    assert (r.dir / "stub" / "live-runtimes").read_text().count('"runsc"') == 1, r.tail()


def test_first_runsc_start_fails_second_succeeds_docker_is_reloaded_once_and_never_restarted(
    tmp_path,
):
    r = _Run(tmp_path, runsc_fails=1)
    assert r.res.returncode == 0, r.tail()
    assert len(r.reloads) == 1, f"a retry must not reload docker again: {r.systemctl_calls}"
    assert not any(c.startswith("restart") for c in r.systemctl_calls), r.systemctl_calls


# ---------------------------------------------------------------- reference: no failure at all


def test_runsc_start_that_works_first_time_is_not_retried_and_logs_no_retry(tmp_path):
    r = _Run(tmp_path, runsc_fails=0)
    assert r.res.returncode == 0, r.tail()
    assert r.last_line == f"{RESULT} changed", r.tail()
    assert len(r.runsc_runs) == 1, f"{r.runsc_runs}\n" + r.tail()
    assert r.retry_lines == [], r.tail()


# ---------------------------------------------------------------- both starts fail


def test_both_runsc_starts_fail_the_step_fails(tmp_path):
    r = _Run(tmp_path, runsc_fails=2)
    assert len(r.runsc_runs) >= 1, "exam precondition: the runtime check ran\n" + r.tail()
    assert r.res.returncode != 0, "the step passed although runsc never started\n" + r.tail()
    assert r.last_line == f"{RESULT} failed", r.tail()
    assert f"{RESULT} changed" not in r.res.stdout, r.tail()


def test_both_runsc_starts_fail_only_one_retry_is_made(tmp_path):
    r = _Run(tmp_path, runsc_fails=2)
    assert len(r.runsc_runs) == 2, (
        f"expected the failed start and exactly one retry, got {r.runsc_runs}\n" + r.tail()
    )
    assert len(r.retry_lines) == 1, f"{r.retry_lines}\n" + r.tail()


def test_both_runsc_starts_fail_the_step_is_rolled_back_as_before(tmp_path):
    r = _Run(tmp_path, runsc_fails=2)
    assert r.res.returncode != 0, r.tail()
    assert "step 'runtime' failed; rolling it back" in r.out, r.tail()
    assert r.daemon_json.read_bytes() == r.seed, (
        "daemon.json was not restored byte for byte\n" + r.tail()
    )
    assert not _daemon_has_runsc(r), r.tail()
    assert "rm -f lf-runsc-check" in r.docker_calls, (
        "the test container was not removed\n" + "\n".join(r.docker_calls)
    )
    # The rollback reloads docker so that it forgets runsc; it never restarts it.
    assert len(r.reloads) >= 2, f"no reload after the restore: {r.systemctl_calls}"
    assert not any(c.startswith("restart") for c in r.systemctl_calls), r.systemctl_calls
    assert (r.dir / "stub" / "live-runtimes").read_text().strip() in ("{}", ""), (
        "docker still lists runsc after the rollback\n" + r.tail()
    )


def test_both_runsc_starts_fail_later_steps_do_not_run(tmp_path):
    r = _Run(tmp_path, runsc_fails=2)
    assert r.res.returncode != 0, r.tail()
    assert "== step runtime: applying" in r.out, r.tail()
    assert "== step swap" not in r.out and "== step iptables" not in r.out, r.tail()
