"""Acceptance tests for issue #54 (criterion: the workflow's launch, follow and readback commands).

The step `run:` text of `.github/workflows/bootstrap.yml` is executed for real under `bash`, with
`ssh` and `scp` replaced by local stubs that run the remote command with `sh -c` on this machine.
The bootstrap script it would copy is a stub that exits with a chosen code. Nothing in the
workflow is edited: the exam reads the YAML, and only rewrites the run directory and the lock
path (both absolute server paths) to temp paths.

Needs Linux (`flock`, `setsid`, `sh`): it is skipped elsewhere and runs in the CI `manager` job.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "bootstrap.yml"
SERVER_LOCK = "/run/lf-bootstrap.lock"
SERVER_DEST = "/root/lf-bootstrap"

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not all(shutil.which(t) for t in ("flock", "setsid", "bash")),
    reason="needs Linux with flock, setsid and bash",
)

SSH_STUB = r"""#!/bin/bash
# stands in for ssh: the last argument is the remote command; run it here with sh -c.
# STUB_SKIP_REMOTE=launch,follow drops those commands (they exit 0 and do nothing).
echo "ssh $*" >> "$STUB_LOG"
cmd="${@: -1}"
case "$cmd" in
  setsid*) kind=launch ;;
  tail*) kind=follow ;;
  *) kind=other ;;
esac
case ",${STUB_SKIP_REMOTE:-}," in *",$kind,"*) exit 0 ;; esac
cmd="${cmd//$SERVER_LOCK/$STUB_LOCK}"
exec sh -c "$cmd"
"""

SCP_STUB = r"""#!/bin/bash
# stands in for scp: copy the file to the path after the `host:` part of the last argument.
echo "scp $*" >> "$STUB_LOG"
src="${@: -2:1}"
dst="${@: -1}"
cp "$STUB_SCRIPT" "${dst#*:}"
"""


def _step_text() -> str:
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = wf["jobs"]["bootstrap"]["steps"]
    runs = [s["run"] for s in steps if "bootstrap.sh" in s.get("run", "")
            and "SSH_PRIVATE_KEY" in " ".join(str(v) for v in (s.get("env") or {}))]
    assert len(runs) == 1, "exam precondition: one step runs bootstrap.sh on the server"
    return runs[0]


class _Run:
    def __init__(self, tmp_path: Path, rc: int, skip_remote: str = "",
                 hold_lock: bool = False) -> None:
        self.dir = tmp_path
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name, text in (("ssh", SSH_STUB), ("scp", SCP_STUB)):
            (bindir / name).write_text(text)
            (bindir / name).chmod(0o755)
        stub = tmp_path / "stub-bootstrap.sh"
        stub.write_text(f"#!/bin/bash\necho 'BOOTSTRAP RESULT: stub'\nexit {rc}\n")
        self.lock = tmp_path / "bootstrap.lock"
        self.dest_root = tmp_path / "server"
        self.dest_root.mkdir()
        self.log = tmp_path / "calls.log"
        self.log.write_text("")
        text = _step_text()
        assert SERVER_DEST in text and SERVER_LOCK in text, \
            "exam precondition: the step uses the server run directory and lock path"
        script = tmp_path / "step.sh"
        script.write_text(text.replace(SERVER_DEST, str(self.dest_root)))
        env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1",
            "SSH_PRIVATE_KEY": "not-a-key", "SERVER_HOST": "server.invalid",
            "SERVER_USER": "root", "SERVER_HOST_KEY": "ssh-ed25519 AAAA",
            "INPUT_APPLY": "false", "HEAD_COMMIT_MESSAGE": "chore: test",
            "STUB_LOG": str(self.log), "STUB_LOCK": str(self.lock),
            "STUB_SCRIPT": str(stub), "SERVER_LOCK": SERVER_LOCK,
            "STUB_SKIP_REMOTE": skip_remote,
        }
        holder = None
        if hold_lock:
            import fcntl  # Linux only (see the module skip)

            holder = open(self.lock, "w")
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.res = subprocess.run(
                ["bash", "-e", "-o", "pipefail", str(script)], cwd=REPO_ROOT, env=env,
                capture_output=True, text=True, timeout=120,
            )
        finally:
            if holder:
                holder.close()
        self.out = self.res.stdout + self.res.stderr
        self.calls = self.log.read_text()
        self.rc_files = list(self.dest_root.glob("*/bootstrap.rc"))


def test_step_fails_when_the_server_script_exits_3(tmp_path):
    r = _Run(tmp_path, rc=3)
    assert r.rc_files and r.rc_files[0].read_text().strip() == "3", \
        "exam precondition: the launched stub ran and wrote its exit code\n" + r.out[-2000:]
    assert "BOOTSTRAP RESULT: stub" in r.out, "the log was not followed\n" + r.out[-2000:]
    assert r.res.returncode != 0, \
        "the job step passed although the script exited 3\n" + r.out[-2000:]
    assert "exit code: 3" in r.out, r.out[-2000:]


def test_step_passes_when_the_server_script_exits_0(tmp_path):
    r = _Run(tmp_path, rc=0)
    assert r.res.returncode == 0, r.out[-2000:]
    assert "exit code: 0" in r.out, r.out[-2000:]


def test_step_fails_when_the_exit_code_file_is_missing(tmp_path):
    r = _Run(tmp_path, rc=0, skip_remote="launch,follow")
    assert not r.rc_files, "exam precondition: no exit code file was written"
    assert r.res.returncode != 0, "the job step passed without an exit code file\n" + r.out[-2000:]
    assert "exit code: unknown" in r.out, r.out[-2000:]


def test_step_refuses_before_its_scp_when_the_bootstrap_lock_is_held(tmp_path):
    r = _Run(tmp_path, rc=0, hold_lock=True)
    assert r.res.returncode != 0, \
        "the job step passed with the bootstrap lock held\n" + r.out[-2000:]
    assert "lock" in r.out.lower(), r.out[-2000:]
    assert "scp " not in r.calls, "the script was copied although the lock was held\n" + r.calls
    assert not r.rc_files, "a run was launched although the lock was held"
