"""Acceptance tests for issue #651 (D179, slice 1 of #47): a second gVisor runtime `runsc-suid`.

gVisor ignores setuid bits unless runsc runs with `--allow-suid`; the flag is per runtime, so sudo
labs need their own runtime: the same runsc binary and `runtimeArgs` as `runsc`, plus
`--allow-suid`. Slice 1: the bootstrap's runtime step registers it on the server, the CI `docker`
job registers it on the runner.

Interface under test: `labs/deploy/bootstrap/bootstrap.sh` (the real script under `bash`, server
paths redirected into a temp directory, stubs on PATH; the harness of the #180 and #294 exams) and
`.github/workflows/ci.yml`. Nothing real is touched, no Docker is needed for the harness cases.

Stubs: `docker info` prints the live runtimes; `systemctl reload docker` makes them what the file
says, with Docker's reload quirk (the live runtimes change only when the file has a "runtimes" key,
so an old runtime stays loaded when the restored file has none). With STUB_TRIP=1 a foreign
container appears as soon as `runsc-suid` is live, so the baseline fails right after the step (the
rollback case); it goes away again when `runsc-suid` is no longer live.

Harness cases need Linux with `jq` and `bash` (they run in the CI `manager` job); the runner case
is docker-marked and runs in the CI `docker` job.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import lab_image
import pytest
import yaml

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SCRIPT = REPO_ROOT / "labs" / "deploy" / "bootstrap" / "bootstrap.sh"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RESULT = "BOOTSTRAP RESULT:"
OVERLAY = "--overlay2=root:memory"
SUID_ARG = "--allow-suid"
SUID = "runsc-suid"

needs_linux = pytest.mark.skipif(
    sys.platform != "linux" or not all(shutil.which(t) for t in ("bash", "jq")),
    reason="needs Linux with bash and jq",
)

DOCKER_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_DIR/docker.calls"
live() { cat "$STUB_DIR/live-runtimes" 2>/dev/null || echo '{}'; }
case "$1" in
  info) live ;;
  ps)
    if [ "$STUB_TRIP" = 1 ] && live | grep -q '"runsc-suid"'; then echo exam0foreign; fi ;;
  inspect)
    case "$*" in
      *.Name*) echo "/exam-foreign true 2026-01-01T00:00:00Z" ;;
      *) echo '[{}]' ;;
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
    # Docker reloads the runtimes of the file only when it has a "runtimes" key.
    if jq -e 'has("runtimes")' "$STUB_DAEMON_JSON" >/dev/null 2>&1; then
      jq -c '.runtimes' "$STUB_DAEMON_JSON" > "$STUB_DIR/live-runtimes"
    fi ;;
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

OTHER_KEYS = {"max-concurrent-uploads": 4, "log-opts": {"max-size": "10m"}}
SERVER_RUNSC = {"path": "/opt/exam/runsc", "runtimeArgs": [OVERLAY, "--platform=systrap"]}


def _bytes(cfg: dict) -> bytes:
    return (json.dumps(cfg, indent=2) + "\n").encode()


def _live(cfg: dict) -> str:
    return json.dumps(cfg["runtimes"], separators=(",", ":"))


class _Harness:
    """One temp host. `run()` executes the real script once; state carries over between runs."""

    def __init__(self, tmp_path: Path, seed: dict, *, live: bool = False, trip: bool = False):
        self.dir = tmp_path
        self.bindir = tmp_path / "bin"
        self.stub = tmp_path / "stub"
        self.etc_docker = tmp_path / "etc" / "docker"
        for d in (self.bindir, self.stub, self.etc_docker, tmp_path / "sbin",
                  tmp_path / "systemd", tmp_path / "usrbin", tmp_path / "empty-proc"):
            d.mkdir(parents=True)
        for name, text in STUBS.items():
            (self.bindir / name).write_text(text)
            (self.bindir / name).chmod(0o755)
        self.daemon_json = self.etc_docker / "daemon.json"
        self.seed = _bytes(seed)
        self.daemon_json.write_bytes(self.seed)
        (self.stub / "docker.calls").write_text("")
        if live:
            (self.stub / "live-runtimes").write_text(_live(seed))
        text = SCRIPT.read_text(encoding="utf-8")
        for server_path, local in (
            ("/etc/docker", self.etc_docker),
            ("/run/lf-bootstrap.lock", tmp_path / "bootstrap.lock"),
            ("/usr/local/sbin", tmp_path / "sbin"),
            ("/etc/systemd/system", tmp_path / "systemd"),
            ("/usr/local/bin", tmp_path / "usrbin"),
        ):
            text = text.replace(server_path, str(local))
        self.script = tmp_path / "bootstrap.sh"
        self.script.write_text(text)
        self.trip = trip
        self.runsc_path = str(self.bindir / "runsc")
        self.res: subprocess.CompletedProcess[str] | None = None

    def run(self, dry: bool = False) -> "_Harness":
        env = {
            "PATH": f"{self.bindir}{os.pathsep}{os.environ['PATH']}",
            "PROC_ROOT": str(self.dir / "empty-proc"),  # D169: no IPv6 in the kernel
            "HOME": str(self.dir),
            "STUB_DIR": str(self.stub),
            "STUB_DAEMON_JSON": str(self.daemon_json),
            "STUB_TRIP": "1" if self.trip else "0",
            "LF_BASELINE_URLS": "",
            "LF_BASELINE_TCP": "",
            "LF_SWAP_MIN_FREE_GB": "1",
        }
        if dry:
            env["DRY_RUN"] = "1"
        (self.stub / "systemctl.calls").write_text("")
        self.res = subprocess.run(["bash", str(self.script)], cwd=self.dir, env=env,
                                  capture_output=True, text=True, timeout=300)
        return self

    # ---- what the run left behind
    @property
    def out(self) -> str:
        assert self.res is not None
        return self.res.stdout + self.res.stderr

    @property
    def last_line(self) -> str:
        assert self.res is not None
        return next((ln.strip() for ln in reversed(self.res.stdout.splitlines()) if ln.strip()), "")

    @property
    def systemctl_calls(self) -> list[str]:
        return (self.stub / "systemctl.calls").read_text().splitlines()

    @property
    def reloads(self) -> list[str]:
        return [c for c in self.systemctl_calls if c == "reload docker"]

    @property
    def restarts(self) -> list[str]:
        return [c for c in self.systemctl_calls if "restart" in c]

    @property
    def daemon(self) -> dict:
        return json.loads(self.daemon_json.read_text())

    @property
    def live(self) -> dict:
        p = self.stub / "live-runtimes"
        return json.loads(p.read_text()) if p.exists() and p.read_text().strip() else {}

    @property
    def etc_files(self) -> list[str]:
        return sorted(p.name for p in self.etc_docker.iterdir())

    def tail(self) -> str:
        return ("---- script output (tail) ----\n" + self.out[-3000:]
                + "\n---- systemctl ----\n" + "\n".join(self.systemctl_calls))


def _other_keys(cfg: dict) -> str:
    """`jq -S 'del(.runtimes)'`: every key except runtimes, sorted."""
    return json.dumps({k: v for k, v in cfg.items() if k != "runtimes"}, sort_keys=True)


def _after_report(h: _Harness) -> str:
    assert "== after" in h.out, h.tail()
    return h.out[h.out.index("== after"):]


FRESH = dict(OTHER_KEYS)
LOADED = {**OTHER_KEYS, "runtimes": {"runsc": SERVER_RUNSC}}


# ---------------------------------------------------------------- 1: the runtime step adds it


@needs_linux
def test_fresh_host_runtime_step_adds_runsc_suid_with_runsc_path_and_args_plus_allow_suid(tmp_path):
    h = _Harness(tmp_path, FRESH).run()
    assert h.res.returncode == 0, h.tail()
    rt = h.daemon.get("runtimes", {})
    assert "runsc" in rt and SUID in rt, f"runtimes in daemon.json: {rt}\n" + h.tail()
    assert rt[SUID]["path"] == rt["runsc"]["path"] == h.runsc_path, rt
    assert rt[SUID]["runtimeArgs"] == rt["runsc"]["runtimeArgs"] + [SUID_ARG], rt
    assert OVERLAY in rt[SUID]["runtimeArgs"], rt
    assert h.last_line == f"{RESULT} changed", h.tail()


@needs_linux
def test_existing_runsc_entry_is_copied_path_and_args_then_allow_suid_is_appended(tmp_path):
    """The server case: runsc is in daemon.json and loaded, with its own path and extra args."""
    h = _Harness(tmp_path, LOADED, live=True).run()
    assert h.res.returncode == 0, h.tail()
    rt = h.daemon["runtimes"]
    assert rt["runsc"] == SERVER_RUNSC, "the runsc entry was changed\n" + h.tail()
    assert rt.get(SUID) == {
        "path": "/opt/exam/runsc",
        "runtimeArgs": [OVERLAY, "--platform=systrap", SUID_ARG],
    }, f"{rt}\n" + h.tail()


@needs_linux
@pytest.mark.parametrize("seed", [FRESH, LOADED], ids=["fresh", "runsc-loaded"])
def test_runtime_step_changes_no_other_daemon_json_key_jq_s_equal(tmp_path, seed):
    h = _Harness(tmp_path, seed, live="runtimes" in seed).run()
    assert h.res.returncode == 0, h.tail()
    assert SUID in h.daemon.get("runtimes", {}), "exam precondition: entry added\n" + h.tail()
    assert _other_keys(h.daemon) == _other_keys(seed)


@needs_linux
@pytest.mark.parametrize("seed", [FRESH, LOADED], ids=["fresh", "runsc-loaded"])
def test_runtime_step_applies_it_with_one_docker_reload_and_never_a_restart(tmp_path, seed):
    h = _Harness(tmp_path, seed, live="runtimes" in seed).run()
    assert h.res.returncode == 0, h.tail()
    assert SUID in h.live, f"docker does not list {SUID} after the step: {h.live}\n" + h.tail()
    assert len(h.reloads) == 1, f"expected one reload: {h.systemctl_calls}"
    assert h.restarts == [], h.systemctl_calls


@needs_linux
def test_runtime_step_does_not_leave_a_stray_temp_file_in_etc_docker(tmp_path):
    h = _Harness(tmp_path, FRESH).run()
    assert h.res.returncode == 0, h.tail()
    stray = [n for n in h.etc_files if ".lf-new." in n or ".lf-stage." in n or ".lf-restore." in n]
    assert stray == [], stray


# ---------------------------------------------------------------- 2: idempotent


@needs_linux
@pytest.mark.parametrize("seed", [FRESH, LOADED], ids=["fresh", "runsc-loaded"])
def test_second_run_reports_the_runtime_step_already_in_place_and_changes_nothing(tmp_path, seed):
    h = _Harness(tmp_path, seed, live="runtimes" in seed).run()
    assert h.res.returncode == 0 and SUID in h.daemon.get("runtimes", {}), \
        "exam precondition: the first run adds the entry\n" + h.tail()
    after_first = h.daemon_json.read_bytes()
    files_first = h.etc_files
    h.run()
    assert h.res.returncode == 0, h.tail()
    assert "== step runtime: already in place" in h.out, h.tail()
    assert "== step runtime: applying" not in h.out, h.tail()
    assert h.daemon_json.read_bytes() == after_first, "daemon.json changed on the second run"
    assert h.etc_files == files_first, f"new files in /etc/docker: {h.etc_files}"
    assert h.reloads == [] and h.restarts == [], h.systemctl_calls
    assert not [ln for ln in h.out.splitlines() if "WARNING" in ln and SUID in ln], h.tail()


DIFFERING = [
    {"path": "/opt/other/runsc", "runtimeArgs": [SUID_ARG]},
    {"path": SERVER_RUNSC["path"], "runtimeArgs": [OVERLAY]},  # without --allow-suid
]


@needs_linux
@pytest.mark.parametrize("entry", DIFFERING, ids=["other-path", "no-allow-suid"])
def test_existing_differing_runsc_suid_entry_is_left_untouched(tmp_path, entry):
    seed = {**OTHER_KEYS, "runtimes": {"runsc": SERVER_RUNSC, SUID: entry}}
    h = _Harness(tmp_path, seed, live=True).run()
    assert h.daemon_json.read_bytes() == h.seed, "daemon.json was edited\n" + h.tail()
    assert h.reloads == [] and h.restarts == [], h.systemctl_calls
    assert h.res.returncode == 0, "a differing entry is a warning, not a failed run\n" + h.tail()
    assert h.etc_files == ["daemon.json"], h.etc_files


@needs_linux
@pytest.mark.parametrize("entry", DIFFERING, ids=["other-path", "no-allow-suid"])
def test_existing_differing_runsc_suid_entry_gets_one_warning_line(tmp_path, entry):
    """One warning text (the report prints it once per report, like the runsc overlay warning)."""
    seed = {**OTHER_KEYS, "runtimes": {"runsc": SERVER_RUNSC, SUID: entry}}
    h = _Harness(tmp_path, seed, live=True).run()
    warnings = [ln for ln in h.out.splitlines() if "WARNING" in ln and SUID in ln]
    assert warnings, "no WARNING line naming runsc-suid\n" + h.tail()
    assert len(set(warnings)) == 1, f"more than one warning text: {set(warnings)}"
    assert len(warnings) <= 2, f"the warning is repeated: {warnings}"
    assert "left untouched" in warnings[0], warnings[0]


# ---------------------------------------------------------------- 3: failed baseline rolls back


@needs_linux
@pytest.mark.parametrize("seed", [FRESH, LOADED], ids=["fresh", "runsc-loaded"])
def test_failed_baseline_after_the_step_restores_daemon_json_byte_for_byte(tmp_path, seed):
    h = _Harness(tmp_path, seed, live="runtimes" in seed, trip=True).run()
    assert "baseline differs after step 'runtime'" in h.out, \
        f"exam precondition: the baseline failed after the runtime step\n{h.tail()}"
    assert h.res.returncode != 0, h.tail()
    assert h.last_line == f"{RESULT} failed", h.tail()
    assert h.daemon_json.read_bytes() == h.seed, "daemon.json was not restored byte for byte"
    assert [n for n in h.etc_files if ".lf-new." in n or ".lf-stage." in n] == [], h.etc_files


@needs_linux
@pytest.mark.parametrize("seed", [FRESH, LOADED], ids=["fresh", "runsc-loaded"])
def test_failed_baseline_after_the_step_reloads_docker_and_runsc_suid_is_no_longer_loaded(
    tmp_path, seed
):
    h = _Harness(tmp_path, seed, live="runtimes" in seed, trip=True).run()
    assert "baseline differs after step 'runtime'" in h.out, \
        f"exam precondition: the baseline failed after the runtime step\n{h.tail()}"
    assert SUID not in h.live, f"docker still lists {SUID}: {h.live}\n" + h.tail()
    if "runtimes" in seed:
        assert "runsc" in h.live, f"the rollback also dropped runsc: {h.live}"
    assert len(h.reloads) >= 2, f"no reload after the restore: {h.systemctl_calls}"
    assert h.restarts == [], h.systemctl_calls
    assert "baseline restored" in h.out, h.tail()


# ---------------------------------------------------------------- 4: dry run and report


@needs_linux
@pytest.mark.parametrize("seed", [FRESH, LOADED], ids=["fresh", "runsc-loaded"])
def test_dry_run_prints_the_planned_diff_with_runsc_suid_and_changes_nothing(tmp_path, seed):
    h = _Harness(tmp_path, seed, live="runtimes" in seed).run(dry=True)
    assert h.res.returncode == 0, h.tail()
    assert h.last_line == f"{RESULT} dry run", h.tail()
    assert h.daemon_json.read_bytes() == h.seed, "a dry run edited daemon.json"
    assert h.etc_files == ["daemon.json"], h.etc_files
    assert h.reloads == [] and h.restarts == [], h.systemctl_calls
    added = [ln for ln in h.out.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert any(SUID in ln for ln in added), f"the diff does not add {SUID}\n" + h.tail()
    assert any(SUID_ARG in ln for ln in added), f"the diff does not add {SUID_ARG}\n" + h.tail()


@needs_linux
def test_report_lists_runsc_suid_among_the_live_runtimes_and_the_file_runtimes(tmp_path):
    h = _Harness(tmp_path, LOADED, live=True).run()
    assert h.res.returncode == 0, h.tail()
    rep = _after_report(h)
    live_part = rep[rep.index("-- live docker runtimes"):]
    live_line = live_part.splitlines()[1]
    assert SUID in live_line and "runsc" in live_line, f"live runtimes line: {live_line!r}"
    file_line = next(ln for ln in rep.splitlines() if ln.startswith("runtimes: "))
    assert SUID in file_line, file_line


# ---------------------------------------------------------------- 5: CI runner


def _ci_register_step() -> str:
    wf = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    for step in wf["jobs"]["docker"]["steps"]:
        if "register it with Docker" in step.get("name", ""):
            return step["run"]
    raise AssertionError("the docker job has no 'Install gVisor runsc and register it' step")


def test_ci_docker_job_registers_runsc_suid_with_allow_suid_and_checks_it_is_loaded():
    run = _ci_register_step()
    assert SUID in run and SUID_ARG in run, \
        "the docker job's gVisor step does not register runsc-suid with --allow-suid"
    check = [ln for ln in run.splitlines() if "docker info" in ln]
    assert any(SUID in ln for ln in check), "no `docker info` check that runsc-suid is loaded"


def test_ci_docker_job_still_registers_plain_runsc_and_reloads_docker():
    run = _ci_register_step()
    assert re.search(r"runsc install", run), run
    assert "systemctl reload docker" in run, run


@pytest.mark.docker
def test_runner_has_runsc_suid_registered_with_allow_suid():
    res = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    rt = json.loads(res.stdout)
    assert SUID in rt and "runsc" in rt, f"live runtimes: {sorted(rt)}"
    # D181 amendment 1: the runner's runsc-suid is the release pinned in bootstrap.sh, installed
    # apart from the apt runsc, so the paths differ; check the pinned release and the server's args.
    pinned = re.search(r"^RUNSC_RELEASE=(\S+)", SCRIPT.read_text(encoding="utf-8"), re.M).group(1)
    ver = subprocess.run([rt[SUID]["path"], "--version"], capture_output=True, text=True,
                         timeout=60)
    assert ver.returncode == 0, f"{rt[SUID]['path']}: {ver.stderr}"
    assert f"release-{pinned}" in ver.stdout, f"pinned {pinned}, got: {ver.stdout}"
    suid_args = rt[SUID].get("runtimeArgs") or []
    assert OVERLAY in suid_args, rt[SUID]
    assert SUID_ARG in suid_args, rt[SUID]
    assert SUID_ARG not in (rt["runsc"].get("runtimeArgs") or []), rt["runsc"]


def _euid_of_setuid_perl_run_by_1001(runtime: str) -> str:
    """D26 recipe: a setuid-root copy of the image's perl, run by uid 1001 (real, effective and
    saved) inside one container; prints the effective uid perl sees."""
    script = (
        "cp \"$(command -v perl)\" /tmp/suid-perl && chown root:root /tmp/suid-perl "
        "&& chmod 4755 /tmp/suid-perl && "
        "setpriv --reuid=1001 --regid=1001 --clear-groups /tmp/suid-perl -e 'print $>'"
    )
    res = subprocess.run(
        ["docker", "run", "--rm", "--runtime", runtime, "--user", "0", "--entrypoint", "sh",
         lab_image.ensure_lab_image(), "-c", script],
        capture_output=True, text=True, timeout=300,
    )
    assert res.returncode == 0, f"{runtime}: {res.stdout}\n{res.stderr}"
    return res.stdout.strip()


@pytest.mark.docker
def test_setuid_root_binary_run_by_uid_1001_gets_euid_0_under_runsc_suid():
    assert _euid_of_setuid_perl_run_by_1001(SUID) == "0"


@pytest.mark.docker
def test_setuid_root_binary_run_by_uid_1001_keeps_euid_1001_under_plain_runsc():
    assert _euid_of_setuid_perl_run_by_1001("runsc") == "1001"
