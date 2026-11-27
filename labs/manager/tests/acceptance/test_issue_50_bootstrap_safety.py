"""Acceptance tests for issue #50: bootstrap safety before the first apply run (review of #46).

Interface under test: the same as the #3 exam (`labs/deploy/bootstrap/bootstrap.sh`, run as root
with `sudo env ...`, `BOOTSTRAP RESULT:` last line, `LF_BASELINE_URLS`/`LF_BASELINE_TCP`) plus:
- the previous `/usr/local/sbin/lf-labs-iptables` and `lf-labs-iptables.service` are kept as
  `*.lf-prev` while the iptables step runs, and come back on a rollback;
- the rollback ignores HUP/INT/TERM/PIPE;
- runsc comes from a pinned release (sha512 in the script), is installed under a temp name and
  moved into place.

Static tests run everywhere. Docker tests run in the CI `docker` job on the runner (passwordless
sudo, Docker with runsc registered). Every scenario below is a module-scoped fixture that resets
the runner first, runs the real script, snapshots what the tests need and restores the runner
before it returns, so the scenarios do not leak into each other or into the #3 exam.

How the "runsc registered by a dockerd flag" scenario is set up on the runner: a systemd drop-in
for docker.service re-declares ExecStart with `--add-runtime runsc=<path>` added, daemon.json
has no `runtimes` key, docker is restarted (by the exam, never by the script). dockerd has no
flag for runtime args, so the flag-registered runsc can not carry `--overlay2=root:memory`; the
exam therefore asserts "daemon.json untouched" and "the report shows the dockerd command line"
unconditionally, and "docker info shows --overlay2=root:memory" for every run that exits 0
(a run that exits non-zero must say so with `BOOTSTRAP RESULT: failed`, and still leave
daemon.json alone and runsc loaded).
"""
import contextlib
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "lf_issue3_helpers", _HERE / "test_issue_3_bootstrap.py"
)
assert _spec and _spec.loader
h3 = importlib.util.module_from_spec(_spec)
sys.modules["lf_issue3_helpers"] = h3
_spec.loader.exec_module(h3)

SCRIPT: Path = h3.SCRIPT
WORKFLOW: Path = h3.WORKFLOW
DAEMON_JSON: str = h3.DAEMON_JSON
BACKUP_GLOB: str = h3.BACKUP_GLOB
SWAPFILE: str = h3.SWAPFILE
RESULT: str = h3.RESULT
CHAIN: str = h3.CHAIN
UNIT: str = h3.UNIT
_run = h3._run
_sudo = h3._sudo
_docker = h3._docker
_serve = h3._serve
_last_line = h3._last_line
_script_code = h3._script_code
_daemon_bytes = h3._daemon_bytes
_write_daemon = h3._write_daemon
_runtimes = h3._runtimes
_reload_docker_and_wait = h3._reload_docker_and_wait
_chain_rules = h3._chain_rules
_lf_rules = h3._lf_rules
_swap_names = h3._swap_names
_remove_backups = h3._remove_backups
_snapshot_daemon_state = h3._snapshot_daemon_state
_check_clean_start = h3._check_clean_start
_restore_daemon_state = h3._restore_daemon_state
_wait_daemon_state_stable = h3._wait_daemon_state_stable

SBIN = "/usr/local/sbin/lf-labs-iptables"
UNIT_PATH = f"/etc/systemd/system/{UNIT}"
RUNSC_DEST = "/usr/local/bin/runsc"
OVERLAY = "--overlay2=root:memory"
SUID_ARG = "--allow-suid"
DROPIN = "/etc/systemd/system/docker.service.d/lf-exam-50-runsc-flag.conf"
OUTDATED_SBIN = b"#!/bin/sh\n# outdated lf-labs rules copy (exam 50)\nexit 0\n"
ASIDE = ".lf-exam-aside"


# ---------------------------------------------------------------- static: script


def _code() -> str:
    return "\n".join(_script_code())


def test_script_validates_runsc_against_the_live_dockerd_args():
    code = _code()
    assert re.search(r"/proc/\S*cmdline", code), "no read of the live dockerd /proc/<pid>/cmdline"
    assert "--validate" in code and "--config-file" in code
    assert "dockerd" in code


def test_script_rollback_ignores_hup_int_term_and_pipe():
    code = _code()
    ignored: set[str] = set()
    for m in re.finditer(r"""\btrap\s+(?:''|"")\s+([A-Za-z0-9 ]+)""", code):
        ignored |= {s.removeprefix("SIG") for s in m.group(1).split()}
    assert {"HUP", "INT", "TERM", "PIPE"} <= ignored, f"signals ignored with trap '': {ignored}"


def test_validate_probe_does_not_depend_on_a_pipeline_under_pipefail():
    code = _code()
    assert "pipefail" in code, "exam precondition: the script runs under pipefail"
    # `dockerd --help | grep -q --validate` dies with SIGPIPE under pipefail once grep exits early.
    assert not re.search(r"dockerd[^\n|]*\|[^\n]*grep", code), "dockerd output piped into grep"
    assert not re.search(r"--validate[^\n]*\|\s*(grep|head)", code)


def test_runsc_is_installed_from_a_pinned_release_with_its_sha512_in_the_script():
    code = _code()
    assert "storage.googleapis.com/gvisor/releases" in code
    assert "latest" not in code.lower(), "the download must not follow `latest`"
    assert re.search(r"\b20\d{6}(\.\d+)?\b", code), "no pinned release number (e.g. 20250113.0)"
    assert re.search(r"\b[0-9a-f]{128}\b", code), "no sha512 (128 hex) literal in the script"
    assert "sha512sum" in code


def test_runsc_is_installed_under_a_temp_name_and_moved_into_place():
    code = _code()
    assert re.search(r"\bmv\b[^\n]*(RUNSC_DEST|/usr/local/bin/runsc)", code), \
        "no mv of the downloaded runsc into place"
    assert not re.search(r"\binstall\b[^\n]*\$\{?RUNSC_DEST\}?\"?\s*$", code, re.M), \
        "runsc is installed directly onto its final path (not atomic)"
    assert not re.search(r"-o\s+\"?\$\{?RUNSC_DEST", code)


def test_every_apt_get_waits_for_the_dpkg_lock():
    code = _code()
    calls = [ln for ln in code.splitlines() if re.search(r"\bapt-get\b", ln)]
    assert calls, "no apt-get call found"
    for ln in calls:
        assert "-o DPkg::Lock::Timeout=120" in ln, f"apt-get without the lock timeout: {ln.strip()}"


# ---------------------------------------------------------------- static: workflow


def test_workflow_runs_the_script_detached_and_follows_its_log():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"\bsetsid\b", text), "the script must run under setsid on the server"
    assert re.search(r"\bnohup\b", text), "the script must run under nohup on the server"
    assert re.search(r"setsid[\s\S]{0,400}nohup|nohup[\s\S]{0,400}setsid", text)
    assert re.search(r"nohup[\s\S]{0,600}>\s*\S*\.?log", text, re.I), "output not sent to a log"
    assert re.search(r"\btail\s+(-\w*[fF]|--follow|--pid)", text), "the log is not followed"


def test_workflow_still_fails_when_the_detached_script_fails():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"BOOTSTRAP RESULT|exit[-_. ]?(code|status)|\.rc\b|\.exit\b", text, re.I), \
        "the workflow must read the detached script's result back and fail on it"


# ---------------------------------------------------------------- docker helpers


def _stat(path: str) -> str | None:
    res = _run(["sudo", "-n", "stat", "-c", "%a %U:%G", path])
    return res.stdout.strip() if res.returncode == 0 else None


def _read(path: str) -> bytes | None:
    res = subprocess.run(["sudo", "-n", "cat", path], capture_output=True)
    return res.stdout if res.returncode == 0 else None


def _put(path: str, data: bytes, mode: str) -> None:
    subprocess.run(["sudo", "-n", "tee", path], input=data, capture_output=True, check=True)
    _sudo("chmod", mode, path)


def _dump(cfg: dict) -> bytes:
    return (json.dumps(cfg, indent=2) + "\n").encode()


def _base_cfg(original: bytes | None) -> dict:
    try:
        cfg = json.loads(original) if original and original.strip() else {}
    except ValueError:
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def _wait_docker(wait: float = 60.0) -> None:
    deadline = time.time() + wait
    while time.time() < deadline:
        if _run(["docker", "info", "--format", "{{.ServerVersion}}"]).returncode == 0:
            return
        time.sleep(1)


def _wait_runsc(want: bool, wait: float = 40.0) -> bool:
    deadline = time.time() + wait
    while time.time() < deadline:
        if ("runsc" in _runtimes()) == want:
            return True
        time.sleep(1)
    return ("runsc" in _runtimes()) == want


def _reset_full() -> None:
    """Pre-bootstrap state: no lf-labs rules/network/unit/boot copy, no daemon.json backups."""
    h3._reset_runner()
    for p in (SBIN, UNIT_PATH):
        _sudo("rm", "-f", p, f"{p}.lf-prev", check=False)
    _sudo("systemctl", "daemon-reload", check=False)
    _remove_backups()
    _run(["sudo", "-n", "sh", "-c", f"rm -f {DAEMON_JSON}.lf-new.* {DAEMON_JSON}.lf-restore.* "
          f"{DAEMON_JSON}.lf-stage.*"])


@contextlib.contextmanager
def _runner_scope():
    """Reset the runner, hand out a scope, and put everything back afterwards."""
    # The runner's daemon.json, mode, owner, backups and runtimes, taken once the runner is quiet.
    # An earlier scenario (or module) that left a backup or a changed file behind fails here as
    # an exam precondition, not as a product failure.
    snap = _snapshot_daemon_state()
    _check_clean_start(snap, "test_issue_50_bootstrap_safety")
    sc = SimpleNamespace(
        restore_problem="", original=snap.original, undo=[], orig_swaps=_swap_names(),
        had_runsc=snap.had_runsc, snap=snap,
    )
    sc.orig_stat = _stat(DAEMON_JSON) if sc.original is not None else None
    sc.runsc_path = shutil.which("runsc") or "/usr/bin/runsc"
    _run(["sudo", "-n", "mkdir", "-p", "/etc/docker"], check=True)
    baseline = _serve("127.0.0.1")
    sc.baseline = baseline
    sc.good_env = {
        "LF_BASELINE_URLS": f"http://127.0.0.1:{baseline.server_address[1]}/",
        "LF_BASELINE_TCP": f"127.0.0.1:{baseline.server_address[1]}",
        "LF_SWAP_MIN_FREE_GB": "1",  # the runner's disk is small
    }
    try:
        _reset_full()
        yield sc
    finally:
        for fn in reversed(sc.undo):
            with contextlib.suppress(Exception):
                fn()
        _reset_full()
        restart = os.path.exists(DROPIN)
        if restart:
            _sudo("rm", "-f", DROPIN, check=False)
            _sudo("systemctl", "daemon-reload", check=False)
        # Original bytes, mode, owner first, then restart (drop-in gone: dockerd drops the
        # flag-registered runsc) or reload; no backups or temp files left.
        restore_problem = _restore_daemon_state(sc.snap, reload=True, restart=restart)
        _sudo("swapoff", SWAPFILE, check=False)
        _sudo("rm", "-f", SWAPFILE, check=False)
        _sudo("sed", "-i", f"\\|^{SWAPFILE} |d", "/etc/fstab", check=False)
        for name in _swap_names():
            if name not in sc.orig_swaps and name != SWAPFILE:
                _sudo("swapoff", name, check=False)
        for name in sc.orig_swaps:
            _sudo("swapon", name, check=False)
        baseline.shutdown()
        sc.restore_problem = restore_problem  # reported last, after the tests ran


def _scope_result(s, sc):
    """Fixture tail, outside the `with`: the scope is already torn down (module-scoped fixtures
    must not hold a runner scope open until the end of the module), the tests run, then a failed
    restore is reported last."""
    yield s
    h3._precondition(not sc.restore_problem, sc.restore_problem)


def _unregister_runsc(sc) -> dict:
    """Make docker forget runsc (reload only drops a runtime while the key is present)."""
    cfg = _base_cfg(sc.original)
    cfg["runtimes"] = {}
    _write_daemon(_dump(cfg))
    _reload_docker_and_wait(runsc=False)
    assert "runsc" not in _runtimes(), "could not unregister runsc before the scenario"
    return cfg


def _out(res: subprocess.CompletedProcess) -> str:
    return (res.stdout or "") + (res.stderr or "")


def _tail(out: str) -> str:
    """The end of the script output, appended to assertion messages so a CI failure shows which
    step rolled back."""
    return "---- script output (tail) ----\n" + out[-2000:]


def _bootstrap(env: dict[str, str]) -> subprocess.CompletedProcess:
    return h3._bootstrap(env, unset=("DRY_RUN",))


def _failing_env(port: int) -> dict[str, str]:
    return {"LF_BASELINE_URLS": "", "LF_BASELINE_TCP": f"127.0.0.1:{port}",
            "LF_SWAP_MIN_FREE_GB": "1"}


class _TripListener:
    """A TCP listener that closes itself as soon as `cond()` is true: the baseline then passes
    until that moment and fails right after, which makes the step in progress roll back."""

    def __init__(self, cond) -> None:
        self.cond = cond
        self.sock = h3.socket.socket()
        self.sock.setsockopt(h3.socket.SOL_SOCKET, h3.socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.closed = threading.Event()
        self.stop = threading.Event()
        self._accepter = threading.Thread(target=self._accept, daemon=True)
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._accepter.start()
        self._watcher.start()

    def _shut(self) -> None:
        """Stop listening at once. close() alone would leave the socket listening (and the kernel
        completing handshakes from its backlog) while another thread sits in accept(); shutdown()
        wakes that accept() and ends the listening state first."""
        with contextlib.suppress(OSError):
            self.sock.shutdown(h3.socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self.sock.close()

    def _accept(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.settimeout(0.2)
        while not self.stop.is_set() and not self.closed.is_set() and self.sock.fileno() != -1:
            try:
                c, _ = self.sock.accept()
                c.close()
            except OSError:
                pass

    def _watch(self) -> None:
        while not self.stop.is_set():
            try:
                hit = bool(self.cond())
            except Exception:
                hit = False
            if hit:
                self._shut()
                self.closed.set()
                return
            time.sleep(0.01)

    def finish(self) -> None:
        self.stop.set()
        self._shut()
        self._accepter.join(5)
        self._watcher.join(5)
        assert not self._accepter.is_alive(), "the listener's accept thread did not exit"


def _boot_copy_is_new() -> bool:
    try:
        return Path(SBIN).read_bytes() == SCRIPT.read_bytes()
    except OSError:
        return False


def _prev_leftovers() -> list[str]:
    return sorted(glob.glob(f"{SBIN}.lf-prev") + glob.glob(f"{UNIT_PATH}.lf-prev"))


def _iptables_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        sbin=_read(SBIN), unit=_read(UNIT_PATH), sbin_mode=_stat(SBIN),
        rules=_lf_rules(), chain=_chain_rules(CHAIN), leftovers=_prev_leftovers(),
        enabled=_run(["systemctl", "is-enabled", UNIT]).returncode == 0,
    )


# ---------------------------------------------------------------- scenario 1: outdated copy


@pytest.fixture(scope="module")
def prev_copy():
    """Bootstrap applied once, an outdated boot copy + unit, a baseline failing after iptables."""
    s = SimpleNamespace()
    with _runner_scope() as sc:
        s.first = _bootstrap(sc.good_env)
        unit_now = _read(UNIT_PATH) or b""
        _put(SBIN, OUTDATED_SBIN, "755")
        s.old_unit = unit_now + b"\n# outdated unit text (exam 50)\n"
        _put(UNIT_PATH, s.old_unit, "644")
        _sudo("systemctl", "daemon-reload", check=False)
        s.before = _iptables_snapshot()
        stop = threading.Event()
        s.prev_seen = False

        def _watch_prev() -> None:
            while not stop.is_set():
                if os.path.exists(f"{SBIN}.lf-prev"):
                    s.prev_seen = True
                    return
                time.sleep(0.02)

        threading.Thread(target=_watch_prev, daemon=True).start()
        trip = _TripListener(lambda: os.path.exists(f"{SBIN}.lf-prev"))
        try:
            s.res = _bootstrap(_failing_env(trip.port))
        finally:
            trip.finish()
            stop.set()
        s.out = _out(s.res)
        s.tripped = trip.closed.is_set()
        time.sleep(1)
        s.after = _iptables_snapshot()
    yield from _scope_result(s, sc)


def _precondition_prev(s) -> None:
    assert s.first.returncode == 0, s.first.stdout + s.first.stderr
    assert s.before.sbin == OUTDATED_SBIN and s.before.unit == s.old_unit
    assert s.before.rules, "exam precondition: lf-labs rules exist before the failing run"
    assert s.tripped, "exam precondition: the listener closed after the .lf-prev marker " \
        "appeared\n" + _tail(s.out)


@pytest.mark.docker
def test_rollback_after_the_iptables_step_exits_non_zero_and_reports_baseline(prev_copy):
    _precondition_prev(prev_copy)
    out = prev_copy.out
    assert prev_copy.res.returncode != 0, _tail(out)
    assert "baseline" in out.lower(), _tail(out)
    assert "baseline differs after step 'iptables'" in out, \
        "the baseline check that failed is not the one after the iptables step\n" + _tail(out)
    assert f"{RESULT} changed" not in prev_copy.res.stdout, _tail(out)


@pytest.mark.docker
def test_rollback_restores_the_previous_boot_copy_of_the_rules_script(prev_copy):
    _precondition_prev(prev_copy)
    assert prev_copy.after.sbin == OUTDATED_SBIN, \
        "the old /usr/local/sbin/lf-labs-iptables is gone\n" + _tail(prev_copy.out)
    assert prev_copy.after.sbin_mode == prev_copy.before.sbin_mode, \
        f"{prev_copy.after.sbin_mode}\n" + _tail(prev_copy.out)


@pytest.mark.docker
def test_rollback_restores_the_previous_unit_file(prev_copy):
    _precondition_prev(prev_copy)
    assert prev_copy.after.unit == prev_copy.old_unit, \
        "the old lf-labs-iptables.service is gone\n" + _tail(prev_copy.out)
    assert prev_copy.after.enabled == prev_copy.before.enabled, \
        "unit enablement changed\n" + _tail(prev_copy.out)


@pytest.mark.docker
def test_rollback_leaves_the_previous_lf_labs_rules_intact(prev_copy):
    _precondition_prev(prev_copy)
    assert prev_copy.after.rules == prev_copy.before.rules, \
        "lf-labs rules lost or reordered\n" + _tail(prev_copy.out)
    assert prev_copy.after.chain == prev_copy.before.chain, \
        "LF-LABS-INPUT chain changed\n" + _tail(prev_copy.out)


@pytest.mark.docker
def test_previous_boot_copy_is_kept_as_lf_prev_while_the_step_runs(prev_copy):
    _precondition_prev(prev_copy)
    assert prev_copy.prev_seen, f"{SBIN}.lf-prev never appeared during the run\n" + \
        _tail(prev_copy.out)


@pytest.mark.docker
def test_rollback_leaves_no_lf_prev_files_behind(prev_copy):
    _precondition_prev(prev_copy)
    assert prev_copy.after.leftovers == [], \
        f"{prev_copy.after.leftovers}\n" + _tail(prev_copy.out)


# ---------------------------------------------------------------- scenario 2: mixed state


@pytest.fixture(scope="module")
def mixed_copy():
    """Only the boot copy pre-exists (no unit, no rules); baseline fails after iptables."""
    s = SimpleNamespace()
    bystanders = ["/usr/local/sbin/lf-exam-50-bystander", "/etc/systemd/system/lf-exam-50.service"]
    with _runner_scope() as sc:
        sc.undo.append(lambda: _sudo("rm", "-f", *bystanders, check=False))
        _put(SBIN, OUTDATED_SBIN, "755")
        _put(bystanders[0], b"#!/bin/sh\n", "755")
        _put(bystanders[1], b"[Unit]\nDescription=exam 50 bystander\n", "644")
        _sudo("systemctl", "daemon-reload", check=False)
        s.bystanders = bystanders
        s.before = _iptables_snapshot()
        trip = _TripListener(_boot_copy_is_new)
        try:
            s.res = _bootstrap(_failing_env(trip.port))
        finally:
            trip.finish()
        s.out = _out(s.res)
        s.tripped = trip.closed.is_set()
        time.sleep(1)
        s.after = _iptables_snapshot()
        s.bystander_bytes = [_read(p) for p in bystanders]
    yield from _scope_result(s, sc)


def _precondition_mixed(s) -> None:
    assert s.before.sbin == OUTDATED_SBIN and s.before.unit is None and not s.before.rules
    assert s.tripped, "exam precondition: the listener closed after the new boot copy " \
        "appeared\n" + _tail(s.out)
    assert s.res.returncode != 0, _tail(s.out)


@pytest.mark.docker
def test_rollback_removes_the_unit_this_run_created(mixed_copy):
    _precondition_mixed(mixed_copy)
    assert mixed_copy.after.unit is None, \
        "the unit written by the failed run is still there\n" + _tail(mixed_copy.out)
    assert not os.path.exists(UNIT_PATH), _tail(mixed_copy.out)


@pytest.mark.docker
def test_rollback_restores_the_boot_copy_that_existed_even_when_the_unit_did_not(mixed_copy):
    _precondition_mixed(mixed_copy)
    assert mixed_copy.after.sbin == OUTDATED_SBIN, _tail(mixed_copy.out)


@pytest.mark.docker
def test_rollback_removes_the_lf_labs_rules_this_run_added(mixed_copy):
    _precondition_mixed(mixed_copy)
    assert mixed_copy.after.rules == [], f"{mixed_copy.after.rules}\n" + _tail(mixed_copy.out)
    assert mixed_copy.after.chain is None, \
        "the LF-LABS-INPUT chain created by the run is left\n" + _tail(mixed_copy.out)


@pytest.mark.docker
def test_rollback_deletes_only_files_this_run_created(mixed_copy):
    _precondition_mixed(mixed_copy)
    assert mixed_copy.bystander_bytes[0] == b"#!/bin/sh\n", \
        "an unrelated sbin file was touched\n" + _tail(mixed_copy.out)
    assert mixed_copy.bystander_bytes[1] is not None, \
        "an unrelated unit file was deleted\n" + _tail(mixed_copy.out)
    assert mixed_copy.after.leftovers == [], \
        f"{mixed_copy.after.leftovers}\n" + _tail(mixed_copy.out)


# ---------------------------------------------------------------- scenario 3: no runtimes key


def _signal_script() -> list[str]:
    """Send HUP, TERM, INT to the script's main bash process (the lowest pid of its name)."""
    res = _run(["sudo", "-n", "pgrep", "-f", f"^bash {SCRIPT}$"])
    pids = sorted(int(p) for p in res.stdout.split())
    if not pids:
        return []
    for sig in ("HUP", "TERM", "INT"):
        _run(["sudo", "-n", "kill", f"-{sig}", str(pids[0])])
        time.sleep(0.3)
    return ["HUP", "TERM", "INT"]


@pytest.fixture(scope="module")
def no_runtimes_key():
    """Seed without a `runtimes` key, baseline failing after the runtime step; HUP/TERM/INT are
    sent to the script as soon as it announces the rollback."""
    s = SimpleNamespace()
    with _runner_scope() as sc:
        cfg = _unregister_runsc(sc)
        cfg.pop("runtimes", None)
        cfg["max-concurrent-uploads"] = 4
        s.seed = _dump(cfg)
        _write_daemon(s.seed)
        s.mode = _stat(DAEMON_JSON)
        assert "runsc" not in _runtimes()
        trip = h3._OneShotListener()
        cmd = ["sudo", "-n", "env", "-u", "DRY_RUN",
               *[f"{k}={v}" for k, v in _failing_env(trip.port).items()], "bash", str(SCRIPT)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines: list[str] = []
        s.signals: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line)
                if not s.signals and re.search(r"rolling (it )?back", line, re.I):
                    s.signals = _signal_script()
            proc.wait()
        finally:
            trip.finish()
        s.rc = proc.returncode
        s.out = "".join(lines)
        s.tripped = trip.closed.is_set()
        s.runsc_gone = _wait_runsc(False)
        _wait_daemon_state_stable()
        s.after = _daemon_bytes()
        s.after_mode = _stat(DAEMON_JSON)
        s.state = h3._state_text("after the rollback run")
    yield from _scope_result(s, sc)


@pytest.mark.docker
def test_rollback_without_a_runtimes_key_restores_daemon_json_bytes(no_runtimes_key):
    s = no_runtimes_key
    assert s.tripped, "exam precondition: the listener closed after the backup appeared\n" \
        + _tail(s.out)
    assert s.rc != 0, _tail(s.out)
    assert "baseline" in s.out.lower(), _tail(s.out)
    assert "baseline differs after step 'runtime'" in s.out, \
        "the baseline check that failed is not the one after the runtime step\n" + _tail(s.out)
    assert s.after == s.seed, ("daemon.json was not restored byte for byte\n"
                               f"--- seeded\n{s.seed.decode(errors='replace')}\n"
                               f"{s.state}" + _tail(s.out))
    assert s.after_mode == s.mode, \
        f"mode/owner changed: {s.mode} -> {s.after_mode}\n" + _tail(s.out)


@pytest.mark.docker
def test_rollback_without_a_runtimes_key_removes_runsc_from_docker(no_runtimes_key):
    assert no_runtimes_key.tripped, _tail(no_runtimes_key.out)
    assert no_runtimes_key.runsc_gone, \
        "docker still lists runsc after the rollback\n" + _tail(no_runtimes_key.out)


@pytest.mark.docker
def test_signals_during_the_rollback_do_not_stop_it(no_runtimes_key):
    s = no_runtimes_key
    assert s.signals == ["HUP", "TERM", "INT"], \
        f"exam precondition: could not signal the script during its rollback ({s.signals})\n" \
        + _tail(s.out)
    assert s.rc != 0, _tail(s.out)
    assert "interrupted by a signal" not in s.out, \
        "the rollback reacted to a signal instead of ignoring it\n" + _tail(s.out)
    assert _last_line(SimpleNamespace(stdout=s.out)) == f"{RESULT} failed", _tail(s.out)
    assert s.after == s.seed, \
        "a signal cut the rollback short: daemon.json not restored\n" + _tail(s.out)
    assert s.runsc_gone, \
        "a signal cut the rollback short: docker still lists runsc\n" + _tail(s.out)


# ---------------------------------------------------------------- scenario 4: download path


@pytest.fixture(scope="module")
def download_path():
    """The runner's runsc binary is moved aside, so the script must download and install one."""
    s = SimpleNamespace()
    with _runner_scope() as sc:
        cfg = _unregister_runsc(sc)
        cfg["max-concurrent-uploads"] = 4
        _write_daemon(_dump(cfg))
        moved = []
        for p in ("/usr/local/bin/runsc", "/usr/bin/runsc", "/bin/runsc", "/usr/sbin/runsc",
                  "/usr/local/sbin/runsc", "/sbin/runsc"):
            if os.path.lexists(p) and os.path.realpath(p) not in [m[2] for m in moved]:
                _sudo("mv", p, p + ASIDE)
                moved.append((p, p + ASIDE, os.path.realpath(p + ASIDE)))

        def _undo() -> None:
            if not any(p == RUNSC_DEST for p, _, _ in moved):
                _sudo("rm", "-f", RUNSC_DEST, check=False)
            for p, aside, _ in moved:
                _sudo("mv", "-f", aside, p, check=False)

        sc.undo.append(_undo)
        s.moved = [m[0] for m in moved]
        s.which = _run(["sudo", "-n", "sh", "-c", "command -v runsc"]).stdout.strip()
        s.res = _bootstrap(sc.good_env)
        s.dest_exists = os.path.isfile(RUNSC_DEST)
        s.dest_exec = os.access(RUNSC_DEST, os.X_OK)
        s.version = _run([RUNSC_DEST, "--version"])
        s.leftovers = sorted(n for n in os.listdir(os.path.dirname(RUNSC_DEST))
                             if n.startswith("runsc") and n != "runsc" and ASIDE not in n)
        s.cfg = _base_cfg(_daemon_bytes())
        s.runtimes = _runtimes()
        s.hello = _run(
            ["docker", "run", "--rm", "--pull=never", "--runtime=runsc", "hello-world"], timeout=600
        )
    yield from _scope_result(s, sc)


@pytest.mark.docker
def test_run_with_runsc_moved_aside_downloads_and_installs_it(download_path):
    s = download_path
    assert s.moved, "exam precondition: the runner has a runsc binary to move aside"
    assert s.which == "", f"exam precondition: runsc still on root's PATH at {s.which}"
    assert s.res.returncode == 0, _tail(_out(s.res))
    assert _last_line(s.res) == f"{RESULT} changed", _tail(_out(s.res))
    assert s.dest_exists and s.dest_exec, f"{RUNSC_DEST} was not installed\n" + _tail(_out(s.res))
    assert s.version.returncode == 0, s.version.stderr + _tail(_out(s.res))


@pytest.mark.docker
def test_downloaded_runsc_is_registered_and_runs_containers(download_path):
    s = download_path
    assert s.res.returncode == 0, _tail(_out(s.res))
    entry = (s.cfg.get("runtimes") or {}).get("runsc") or {}
    assert entry.get("path") == RUNSC_DEST, f"{entry}\n" + _tail(_out(s.res))
    assert OVERLAY in (entry.get("runtimeArgs") or []), f"{entry}\n" + _tail(_out(s.res))
    assert "runsc" in s.runtimes, _tail(_out(s.res))
    assert s.hello.returncode == 0, s.hello.stderr + _tail(_out(s.res))
    assert "Hello from Docker" in s.hello.stdout


@pytest.mark.docker
def test_download_leaves_no_temp_files_next_to_runsc(download_path):
    assert download_path.res.returncode == 0, _tail(_out(download_path.res))
    assert download_path.leftovers == [], \
        f"{download_path.leftovers}\n" + _tail(_out(download_path.res))


# ---------------------------------------------------------------- scenario 5: dockerd flag


def _live_execstart() -> str:
    text = _run(["systemctl", "cat", "docker.service"], check=True).stdout
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("ExecStart=/")]
    assert lines, "exam precondition: docker.service has an ExecStart"
    return lines[-1].removeprefix("ExecStart=")


@pytest.fixture(scope="module")
def flag_registered():
    """runsc registered by `--add-runtime runsc=<path>` on dockerd, not in daemon.json."""
    s = SimpleNamespace()
    with _runner_scope() as sc:
        cfg = _base_cfg(sc.original)
        cfg.pop("runtimes", None)  # a `runtimes` key next to the flag stops dockerd
        cfg["max-concurrent-uploads"] = 4
        execstart = _live_execstart()
        _sudo("mkdir", "-p", os.path.dirname(DROPIN))
        flag = f"--add-runtime runsc={sc.runsc_path}"
        dropin = f"[Service]\nExecStart=\nExecStart={execstart} {flag}\n"
        _put(DROPIN, dropin.encode(), "644")
        s.seed = _dump(cfg)
        _write_daemon(s.seed)
        _sudo("systemctl", "daemon-reload")
        _sudo("systemctl", "restart", "docker")
        _wait_docker()
        s.preloaded = _wait_runsc(True)
        s.seed_mode = _stat(DAEMON_JSON)
        s.res = _bootstrap(sc.good_env)
        s.out = s.res.stdout + s.res.stderr
        s.after = _daemon_bytes()
        s.after_mode = _stat(DAEMON_JSON)
        s.runtimes = _runtimes()
        s.cmdline = _run(["sudo", "-n", "sh", "-c", "tr '\\0' ' ' < /proc/$(pidof dockerd)/cmdline"]
                         ).stdout
    yield from _scope_result(s, sc)


def _precondition_flag(s) -> None:
    assert s.preloaded, "exam precondition: runsc was not loaded through the dockerd flag"
    assert "--add-runtime" in s.cmdline, f"exam precondition: dockerd cmdline is {s.cmdline}"


@pytest.mark.docker
def test_flag_registered_runsc_leaves_daemon_json_byte_identical(flag_registered):
    _precondition_flag(flag_registered)
    assert flag_registered.after == flag_registered.seed, \
        "daemon.json was rewritten\n" + _tail(flag_registered.out)
    assert flag_registered.after_mode == flag_registered.seed_mode, _tail(flag_registered.out)


@pytest.mark.docker
def test_flag_registered_runsc_report_shows_the_dockerd_command_line_and_runtimes(flag_registered):
    _precondition_flag(flag_registered)
    out = flag_registered.out
    assert "--add-runtime" in out, \
        "the report does not show the live dockerd command line\n" + _tail(out)
    assert re.search(r"runsc=\S+", out) or "runsc" in out, _tail(out)
    assert "dockerd" in out, _tail(out)


@pytest.mark.docker
def test_flag_registered_runsc_run_never_claims_success_without_overlay2_root_memory(
    flag_registered,
):
    s = flag_registered
    _precondition_flag(s)
    assert "runsc" in s.runtimes, "the run left docker without runsc\n" + _tail(s.out)
    if s.res.returncode == 0:
        args = (s.runtimes.get("runsc") or {}).get("runtimeArgs") or []
        assert OVERLAY in args, f"exit 0 but docker info shows runsc with {args}\n" + _tail(s.out)
    else:
        assert _last_line(s.res) == f"{RESULT} failed", _tail(s.out)


# ---------------------------------------------------------------- scenario 6: normal run


@pytest.fixture(scope="module")
def normal_run():
    """daemon.json with mode 0640 and group adm, runsc unregistered, one real run."""
    s = SimpleNamespace()
    with _runner_scope() as sc:
        cfg = _unregister_runsc(sc)
        cfg["max-concurrent-uploads"] = 4
        _write_daemon(_dump(cfg))
        _sudo("chown", "root:adm", DAEMON_JSON)
        _sudo("chmod", "640", DAEMON_JSON)
        s.before = _stat(DAEMON_JSON)
        s.res = _bootstrap(sc.good_env)
        s.after_stat = _stat(DAEMON_JSON)
        s.cfg = _base_cfg(_daemon_bytes())
        s.runtimes = _runtimes()
        s.hello = _run(
            ["docker", "run", "--rm", "--pull=never", "--runtime=runsc", "hello-world"], timeout=600
        )
    yield from _scope_result(s, sc)


@pytest.mark.docker
def test_normal_run_docker_info_shows_runsc_with_overlay2_root_memory(normal_run):
    s = normal_run
    out = _out(s.res)
    assert s.res.returncode == 0, _tail(out)
    assert _last_line(s.res) == f"{RESULT} changed", _tail(out)
    runsc = s.runtimes.get("runsc")
    assert runsc, f"docker info lists no runsc: {s.runtimes}\n" + _tail(out)
    assert OVERLAY in (runsc.get("runtimeArgs") or []), f"{runsc}\n" + _tail(out)
    assert s.hello.returncode == 0, s.hello.stderr + _tail(out)


@pytest.mark.docker
def test_daemon_json_keeps_its_original_mode_and_owner(normal_run):
    assert normal_run.res.returncode == 0, _tail(_out(normal_run.res))
    assert normal_run.before == "640 root:adm", normal_run.before
    assert normal_run.after_stat == normal_run.before, \
        f"{normal_run.after_stat}\n" + _tail(_out(normal_run.res))


@pytest.mark.docker
def test_normal_run_keeps_the_other_daemon_json_keys(normal_run):
    assert normal_run.res.returncode == 0, _tail(_out(normal_run.res))
    assert normal_run.cfg.get("max-concurrent-uploads") == 4, _tail(_out(normal_run.res))


# ---------------------------------------------------------------- scenario 7: in file, not loaded


@pytest.fixture(scope="module")
def in_file_not_loaded():
    """daemon.json holds everything the bootstrap registers (runsc and runsc-suid, D185) but
    docker has not loaded it. (a) the file also differs from the
    running config in another key; (b) runsc is the only difference."""
    s = SimpleNamespace()
    with _runner_scope() as sc:
        cfg = _unregister_runsc(sc)
        cfg["runtimes"] = {
            "runsc": {"path": sc.runsc_path, "runtimeArgs": [OVERLAY]},
            "runsc-suid": {"path": sc.runsc_path, "runtimeArgs": [OVERLAY, SUID_ARG]},
        }

        s.cfg_a = {**cfg, "max-concurrent-downloads": 7}
        s.bytes_a = _dump(s.cfg_a)
        _write_daemon(s.bytes_a)
        s.res_a = _bootstrap(sc.good_env)
        s.after_a = _daemon_bytes()
        s.loaded_a = "runsc" in _runtimes()

        _unregister_runsc(sc)
        s.bytes_b = _dump(cfg)
        _write_daemon(s.bytes_b)
        s.res_b = _bootstrap(sc.good_env)
        s.after_b = _daemon_bytes()
        s.runtimes_b = _runtimes()
    yield from _scope_result(s, sc)


@pytest.mark.docker
def test_runsc_in_file_but_not_loaded_fails_when_something_else_differs(in_file_not_loaded):
    s = in_file_not_loaded
    out = _out(s.res_a)
    assert s.res_a.returncode != 0, "a reload would have applied unrelated changes: " + out
    assert _last_line(s.res_a) == f"{RESULT} failed", _tail(out)
    assert s.after_a == s.bytes_a, "daemon.json was modified\n" + _tail(out)
    assert not s.loaded_a, \
        "docker was reloaded although the file differs in other keys\n" + _tail(out)


@pytest.mark.docker
def test_runsc_in_file_but_not_loaded_is_reloaded_when_nothing_else_differs(in_file_not_loaded):
    s = in_file_not_loaded
    out = _out(s.res_b)
    assert s.res_b.returncode == 0, _tail(out)
    assert s.after_b == s.bytes_b, \
        "daemon.json must not be rewritten, only reloaded\n" + _tail(out)
    runsc = s.runtimes_b.get("runsc")
    assert runsc, f"docker info lists no runsc after the reload: {s.runtimes_b}\n" + _tail(out)
    assert OVERLAY in (runsc.get("runtimeArgs") or []), f"{runsc}\n" + _tail(out)
    suid = s.runtimes_b.get("runsc-suid")
    assert suid, f"docker info lists no runsc-suid after the reload: {s.runtimes_b}\n" + _tail(out)
    assert SUID_ARG in (suid.get("runtimeArgs") or []), f"{suid}\n" + _tail(out)
    assert OVERLAY in (suid.get("runtimeArgs") or []), f"{suid}\n" + _tail(out)
