"""Acceptance tests for issue #142: bootstrap scenarios left over from #54.

Interface under test: `labs/deploy/bootstrap/bootstrap.sh`, run as root with `sudo env ...`
(helpers of the #3 and #50 exams), `BOOTSTRAP RESULT:` last line.

Scenarios (docker ones run in the CI `docker` job, on the runner with passwordless sudo):
- lock: the lock file `/run/lf-bootstrap.lock` is held by `flock` (the way the script takes it),
  a normal run exits 1 with `BOOTSTRAP RESULT: failed`, `--rules-only` still succeeds;
- no `/etc/docker` at all: the create path (directory, temp file, final mode 0644) and the
  rollback without a backup (docker drops runsc, the file is removed);
- an existing `/etc/docker` (empty, no daemon.json): the rollback keeps the directory;
- D130 (1): a rollback removes an `/etc/docker` this run created (rmdir, only when empty),
  never one that existed before;
- another docker network holds part of 172.31.240.0/24: the run stops in the preflight, says
  `nothing was changed`, leaves daemon.json and swap alone;
- D130 (2): a failing preflight prints no "before" report.

The runner's `/etc/docker` is moved aside for the "no /etc/docker" scenarios and put back by a
finalizer (before the #50 scope restores daemon.json, bytes and mode).
"""
import contextlib
import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "lf_issue50_helpers", _HERE / "test_issue_50_bootstrap_safety.py"
)
assert _spec and _spec.loader
h50 = importlib.util.module_from_spec(_spec)
sys.modules["lf_issue50_helpers"] = h50
_spec.loader.exec_module(h50)
h3 = h50.h3

SCRIPT: Path = h50.SCRIPT
RESULT: str = h50.RESULT
DAEMON_JSON: str = h50.DAEMON_JSON
NET: str = h3.NET
LOCK = "/run/lf-bootstrap.lock"
DOCKER_DIR = "/etc/docker"
ASIDE_DIR = "/etc/docker.lf-exam-142-aside"
OVERLAP_NET = "lf-exam-142-overlap"
OVERLAP_SUBNET = "172.31.240.0/25"
HOLD_SECONDS = "117"  # an unusual number: the cleanup finds the holder by its command line

_run = h50._run
_sudo = h50._sudo
_docker = h50._docker
_out = h50._out
_tail = h50._tail
_stat = h50._stat
_bootstrap = h50._bootstrap
_failing_env = h50._failing_env
_runner_scope = h50._runner_scope
_scope_result = h50._scope_result
_unregister_runsc = h50._unregister_runsc
_wait_runsc = h50._wait_runsc
_last_line = h50._last_line


# ---------------------------------------------------------------- static: script


def _code() -> str:
    return "\n".join(h3._script_code())


def test_script_takes_the_lock_before_anything_and_fails_with_a_result_line():
    code = _code()
    m = re.search(r"exec 9>(/run/lf-bootstrap\.lock)", code)
    assert m, "the script does not open /run/lf-bootstrap.lock on fd 9"
    flock = re.search(r"flock -n 9\s*\|\|[^\n]*", code)
    assert flock, "no non-blocking flock on fd 9"
    assert f"{RESULT} failed" in flock.group(0) and "exit 1" in flock.group(0), flock.group(0)
    assert m.start() < code.index("DRY_RUN="), "the lock is not taken before the rest of the script"


def test_script_does_not_take_the_lock_for_rules_only():
    code = _code()
    head = code[: code.index("flock -n 9")]
    assert re.search(r"\[\s*\"\$\{1:-\}\"\s*!=\s*\"?--rules-only\"?\s*\]", head), \
        "the lock is not skipped for --rules-only"


def test_script_creates_a_missing_etc_docker_and_a_0644_temp_file_for_a_new_daemon_json():
    code = _code()
    assert re.search(r"mkdir -p -m 0755 /etc/docker", code)
    assert "mktemp /etc/docker/daemon.json.lf-new." in code
    assert re.search(r"chmod 0644 \"\$DAEMON_TMP\"", code), \
        "a new daemon.json (no file to copy the mode from) must get 0644"


def test_script_runs_the_subnet_preflight_before_the_before_report():
    code = _code()
    pre = re.search(r"^preflight_subnet$", code, re.M)
    rep = re.search(r'^report "before"$', code, re.M)
    assert pre and rep, "driver calls not found"
    assert pre.start() < rep.start(), "the before report would print ahead of a failing preflight"


def test_rollback_removes_an_etc_docker_it_created_with_rmdir_only():
    """D130: `rmdir` (fails on a non-empty directory), never a recursive remove of /etc/docker."""
    code = _code()
    start = code.index("rollback_runtime() {")
    body = code[start: code.index("\n}\n", start)]
    assert re.search(r"\brmdir\b[^\n]*/etc/docker|\brmdir\b[^\n]*DOCKER_DIR", body), \
        "rollback_runtime has no rmdir of the /etc/docker it created"
    assert not re.search(r"\brm\s+-[a-zA-Z]*r[^\n]*/etc/docker\b(?!/)", code), \
        "a recursive remove of /etc/docker"


# ---------------------------------------------------------------- docker helpers


def _exists(path: str) -> bool:
    return _run(["sudo", "-n", "test", "-e", path]).returncode == 0


def _dir_aside(sc, keep_empty_dir: bool) -> None:
    """Move the runner's /etc/docker away (leave an empty one when asked); a finalizer puts it
    back. runsc must already be unregistered (the caller does that first)."""
    h3._precondition(not _exists(ASIDE_DIR), f"{ASIDE_DIR} is left over from an earlier run")
    _sudo("mv", DOCKER_DIR, ASIDE_DIR)

    def _back() -> None:
        _sudo("rm", "-rf", DOCKER_DIR, check=False)
        _sudo("mv", ASIDE_DIR, DOCKER_DIR)

    sc.undo.append(_back)
    if keep_empty_dir:
        _sudo("mkdir", "-m", "0755", DOCKER_DIR)


def _daemon_mentions_runsc() -> bool:
    try:
        return "runsc" in Path(DAEMON_JSON).read_text()
    except OSError:
        return False


def _after_state(s) -> None:
    s.dir_exists = _exists(DOCKER_DIR)
    s.dir_stat = _stat(DOCKER_DIR)
    s.daemon_stat = _stat(DAEMON_JSON)
    s.daemon = h3._daemon_bytes()
    s.temps = h3._temp_files()
    s.backups = h3._backups()


def _rollback_scenario(sc, dir_existed: bool) -> SimpleNamespace:
    """No daemon.json (and no /etc/docker, or an empty one); the baseline fails once the new
    daemon.json is in place, so the runtime step rolls back with no backup to restore."""
    s = SimpleNamespace(dir_existed=dir_existed)
    _unregister_runsc(sc)
    _dir_aside(sc, keep_empty_dir=dir_existed)
    s.precondition_no_file = not _exists(DAEMON_JSON)
    s.precondition_dir = _exists(DOCKER_DIR) == dir_existed
    trip = h50._TripListener(_daemon_mentions_runsc)
    try:
        s.res = _bootstrap(_failing_env(trip.port))
    finally:
        trip.finish()
    s.out = _out(s.res)
    s.tripped = trip.closed.is_set()
    s.runsc_gone = _wait_runsc(False)
    h3._wait_daemon_state_stable()
    _after_state(s)
    return s


def _precondition_rollback(s) -> None:
    assert s.precondition_no_file and s.precondition_dir, \
        "exam precondition: the runner could not be put in the no-daemon.json state"
    assert s.tripped, "exam precondition: the baseline trip fired after daemon.json was " \
        "written\n" + _tail(s.out)


# ---------------------------------------------------------------- scenario: lock


@contextlib.contextmanager
def _lock_held():
    """Hold /run/lf-bootstrap.lock with flock(1): a second open file description, exactly what
    a running bootstrap holds."""
    proc = subprocess.Popen(
        ["sudo", "-n", "flock", LOCK, "sleep", HOLD_SECONDS],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        held = False
        while time.time() < deadline and not held:
            held = _run(["sudo", "-n", "flock", "-n", LOCK, "true"]).returncode != 0
            if not held:
                time.sleep(0.2)
        assert held, "exam precondition: could not take the bootstrap lock"
        yield
    finally:
        proc.terminate()
        _run(["sudo", "-n", "pkill", "-f", f"flock {LOCK} sleep {HOLD_SECONDS}"])
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)


@pytest.fixture(scope="module")
def lock_held():
    s = SimpleNamespace()
    with _runner_scope() as sc:
        with _lock_held():
            s.second = _bootstrap({"DRY_RUN": "1", **sc.good_env})
            s.second_real = _bootstrap(sc.good_env)
            s.rules_only = _run(
                ["sudo", "-n", "env", "-u", "DRY_RUN", "bash", str(SCRIPT), "--rules-only"]
            )
            s.rules = h3._lf_rules()
    yield from _scope_result(s, sc)


@pytest.mark.docker
def test_second_concurrent_run_exits_1_with_result_failed(lock_held):
    for res in (lock_held.second, lock_held.second_real):
        assert res.returncode == 1, _tail(_out(res))
        assert _last_line(res) == f"{RESULT} failed", _tail(_out(res))
        assert "another bootstrap is running" in res.stdout, _tail(_out(res))


@pytest.mark.docker
def test_run_refused_by_the_lock_changes_nothing(lock_held):
    assert lock_held.second_real.returncode == 1
    out = lock_held.second_real.stdout
    assert "== baseline" not in out and "== step" not in out, \
        "the run went on after losing the lock\n" + _tail(_out(lock_held.second_real))


@pytest.mark.docker
def test_rules_only_succeeds_while_the_lock_is_held(lock_held):
    res = lock_held.rules_only
    assert res.returncode == 0, _tail(_out(res))
    assert "another bootstrap is running" not in _out(res), _tail(_out(res))
    assert lock_held.rules, "--rules-only applied no lf-labs rules\n" + _tail(_out(res))


# ---------------------------------------------------------------- scenario: no /etc/docker, create


@pytest.fixture(scope="module")
def create_path():
    """No /etc/docker, runsc unregistered: the first apply of a fresh host."""
    s = SimpleNamespace()
    with _runner_scope() as sc:
        _unregister_runsc(sc)
        _dir_aside(sc, keep_empty_dir=False)
        s.precondition = not _exists(DOCKER_DIR)
        s.res = _bootstrap(sc.good_env)
        s.runtimes = h3._runtimes()
        _after_state(s)
        s.hello = _run(["docker", "run", "--rm", "--pull=never", "--runtime=runsc",
                        "hello-world"], timeout=600)
    yield from _scope_result(s, sc)


@pytest.mark.docker
def test_without_etc_docker_the_run_succeeds_and_creates_it(create_path):
    s = create_path
    assert s.precondition, "exam precondition: /etc/docker was not moved aside"
    assert s.res.returncode == 0, _tail(_out(s.res))
    assert _last_line(s.res) == f"{RESULT} changed", _tail(_out(s.res))
    assert s.dir_exists, "/etc/docker was not created\n" + _tail(_out(s.res))
    assert s.dir_stat is not None and s.dir_stat.startswith("755 root:"), \
        f"{s.dir_stat}\n" + _tail(_out(s.res))


@pytest.mark.docker
def test_without_etc_docker_the_new_daemon_json_is_0644_and_registers_runsc(create_path):
    s = create_path
    assert s.res.returncode == 0, _tail(_out(s.res))
    assert s.daemon_stat is not None and s.daemon_stat.startswith("644 root:"), \
        f"{s.daemon_stat}\n" + _tail(_out(s.res))
    assert s.daemon and b"runsc" in s.daemon, _tail(_out(s.res))
    assert "runsc" in s.runtimes, _tail(_out(s.res))
    assert s.hello.returncode == 0, s.hello.stderr + _tail(_out(s.res))


@pytest.mark.docker
def test_without_etc_docker_no_temp_file_or_backup_is_left_behind(create_path):
    s = create_path
    assert s.res.returncode == 0, _tail(_out(s.res))
    assert s.temps == [], f"{s.temps}\n" + _tail(_out(s.res))
    assert s.backups == [], "there was no daemon.json to back up\n" + _tail(_out(s.res))


# ---------------------------------------------------------------- scenario: rollback, no backup


@pytest.fixture(scope="module")
def rollback_created_dir():
    s = SimpleNamespace()
    with _runner_scope() as sc:
        s.__dict__.update(_rollback_scenario(sc, dir_existed=False).__dict__)
    yield from _scope_result(s, sc)


@pytest.fixture(scope="module")
def rollback_existing_dir():
    s = SimpleNamespace()
    with _runner_scope() as sc:
        s.__dict__.update(_rollback_scenario(sc, dir_existed=True).__dict__)
    yield from _scope_result(s, sc)


@pytest.mark.docker
def test_rollback_with_no_backup_fails_the_run_and_removes_the_new_daemon_json(
    rollback_created_dir,
):
    s = rollback_created_dir
    _precondition_rollback(s)
    assert s.res.returncode != 0, _tail(s.out)
    assert "baseline differs after step 'runtime'" in s.out, _tail(s.out)
    assert _last_line(s.res) == f"{RESULT} failed", _tail(s.out)
    assert s.daemon is None, "the daemon.json written by the failed run is still there\n" \
        + _tail(s.out)


@pytest.mark.docker
def test_rollback_with_no_backup_makes_docker_drop_runsc_and_leaves_no_temp_files(
    rollback_created_dir,
):
    """The staged {"runtimes": {}} reload is what makes docker forget runsc: removing the file
    alone would leave it loaded."""
    s = rollback_created_dir
    _precondition_rollback(s)
    assert s.runsc_gone, "docker still lists runsc after the rollback\n" + _tail(s.out)
    assert s.temps == [], f"{s.temps}\n" + _tail(s.out)
    assert s.backups == [], f"{s.backups}\n" + _tail(s.out)


@pytest.mark.docker
def test_rollback_removes_an_etc_docker_the_run_created(rollback_created_dir):
    s = rollback_created_dir
    _precondition_rollback(s)
    assert not s.dir_exists, \
        "/etc/docker, created by the failed run, is still there\n" + _tail(s.out)


@pytest.mark.docker
def test_rollback_keeps_an_etc_docker_that_existed_before_the_run(rollback_existing_dir):
    s = rollback_existing_dir
    _precondition_rollback(s)
    assert s.res.returncode != 0, _tail(s.out)
    assert s.dir_exists, "an /etc/docker that existed before the run was removed\n" + _tail(s.out)
    assert s.daemon is None, _tail(s.out)
    assert s.runsc_gone, _tail(s.out)


# ---------------------------------------------------------------- scenario: overlapping network


@pytest.fixture(scope="module")
def overlap():
    s = SimpleNamespace()
    with _runner_scope() as sc:
        _unregister_runsc(sc)
        res = _docker("network", "create", "--subnet", OVERLAP_SUBNET, OVERLAP_NET, check=False)
        sc.undo.append(lambda: _docker("network", "rm", OVERLAP_NET, check=False))
        s.precondition = res.returncode == 0
        s.precondition_text = res.stderr
        s.daemon_before = h3._daemon_bytes()
        s.swap_before = h3._swap()
        s.swapfile_before = _exists(h3.SWAPFILE)
        s.res = _bootstrap(sc.good_env)
        s.out = _out(s.res)
        s.daemon_after = h3._daemon_bytes()
        s.swap_after = h3._swap()
        s.swapfile_after = _exists(h3.SWAPFILE)
        s.networks = h3._networks()
        s.backups = h3._backups()
        s.temps = h3._temp_files()
        s.runsc_loaded = "runsc" in h3._runtimes()
    yield from _scope_result(s, sc)


def _precondition_overlap(s) -> None:
    assert s.precondition, f"exam precondition: could not create {OVERLAP_NET}: " \
        f"{s.precondition_text}"


@pytest.mark.docker
def test_overlapping_docker_network_fails_the_run_and_says_nothing_was_changed(overlap):
    s = overlap
    _precondition_overlap(s)
    assert s.res.returncode != 0, _tail(s.out)
    assert "nothing was changed" in s.out, _tail(s.out)
    assert _last_line(s.res) == f"{RESULT} failed", _tail(s.out)
    assert OVERLAP_NET in s.out and "overlap" in s.out, \
        "the message does not name the clashing network\n" + _tail(s.out)


@pytest.mark.docker
def test_overlapping_docker_network_leaves_daemon_json_and_swap_unchanged(overlap):
    s = overlap
    _precondition_overlap(s)
    assert s.res.returncode != 0, _tail(s.out)
    assert s.daemon_after == s.daemon_before, "daemon.json changed\n" + _tail(s.out)
    assert s.backups == [] and s.temps == [], f"{s.backups} {s.temps}\n" + _tail(s.out)
    assert s.swap_after == s.swap_before and s.swapfile_after == s.swapfile_before, \
        "swap changed\n" + _tail(s.out)
    assert not s.runsc_loaded, "docker was reloaded with runsc although nothing may change"
    assert NET not in s.networks, "lf-labs was created\n" + _tail(s.out)


@pytest.mark.docker
def test_failing_preflight_prints_no_before_report(overlap):
    """D130: the preflight runs ahead of the report; a refused run shows only its reason."""
    s = overlap
    _precondition_overlap(s)
    assert s.res.returncode != 0, _tail(s.out)
    assert not re.search(r"^== before$", s.out, re.M), \
        "the before report was printed ahead of a failing preflight\n" + _tail(s.out)
    assert "-- free memory" not in s.out, _tail(s.out)
    assert "nothing was changed" in s.out, _tail(s.out)
    assert _last_line(s.res) == f"{RESULT} failed", _tail(s.out)
