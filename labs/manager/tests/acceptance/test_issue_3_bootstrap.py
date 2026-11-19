"""Acceptance tests for issue #3: server bootstrap (runsc, swap, lf-labs, DOCKER-USER).

Interface under test (D25 plus the details fixed by this exam):
- Script: labs/deploy/bootstrap/bootstrap.sh, run as root (the tests use `sudo env ...`).
- `DRY_RUN=1` prints every command it would run (one command per line, containing the real
  command text, e.g. `docker network create ...`, `iptables ...`) and changes nothing.
- Baseline: `LF_BASELINE_URLS` (space separated, expect 2xx/3xx) and `LF_BASELINE_TCP`
  (space separated host:port), plus every container whose name does not start with lf-/lab-.
  A failing baseline makes the script exit non-zero (any non-zero code), mention "baseline" in
  its output, and leave the host as it was (nothing applied, or the step just done rolled back).
- Report: the LAST non-empty line of stdout is one of
    `BOOTSTRAP RESULT: dry run`     (DRY_RUN=1)
    `BOOTSTRAP RESULT: changed`     (something was applied)
    `BOOTSTRAP RESULT: no changes`  (everything was already in place)
  Exit code 0 on success, non-zero on any failure.
- Network `lf-labs`: bridge `br-lf-labs`, enable_icc=false, subnet 172.31.240.0/24, manager
  address 172.31.240.2 (accepted to labs on tcp/7681 ahead of the drops).
- Every DOCKER-USER rule the script adds matches `-i br-lf-labs` and has comment `lf-labs`.

Static tests run everywhere. Docker tests run in the CI `docker` job: an ubuntu runner with
passwordless sudo, Docker and runsc already registered. They run the real script on the runner,
once as a dry run, once with a failing baseline, twice for real.
"""
import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
import uuid
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import lab_image
import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SCRIPT = REPO_ROOT / "labs" / "deploy" / "bootstrap" / "bootstrap.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "bootstrap.yml"
IMAGE_DIR = REPO_ROOT / "labs" / "images" / "term"
DAEMON_JSON = "/etc/docker/daemon.json"
NET = "lf-labs"
BRIDGE = "br-lf-labs"
SUBNET = "172.31.240.0/24"
MANAGER_IP = "172.31.240.2"
PORT = 7681
SENTINEL = "sentinel-bootstrap-test"
RESULT = "BOOTSTRAP RESULT:"
CHAIN = "LF-LABS-INPUT"
UNIT = "lf-labs-iptables.service"
TAG_RE = r'--comment "?lf-labs"?(\s|$)'


# ---------------------------------------------------------------- static: script


def _script_code() -> list[str]:
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    return [ln for ln in lines if not ln.strip().startswith("#")]


def test_script_exists_and_supports_dry_run():
    code = "\n".join(_script_code())
    assert "DRY_RUN" in code
    assert "LF_BASELINE_URLS" in code and "LF_BASELINE_TCP" in code


def test_script_never_restarts_docker():
    code = "\n".join(_script_code())
    assert not re.search(r"systemctl\s+restart\s+docker", code)
    assert not re.search(r"service\s+docker\s+restart", code)
    assert re.search(r"systemctl\s+reload\s+docker", code), "docker must be reloaded, not restarted"


def test_script_only_touches_docker_user_chain():
    code = "\n".join(_script_code())
    assert "DOCKER-USER" in code
    assert "iptables-persistent" not in code
    assert not re.search(r"iptables\s+(-t\s+\w+\s+)?-F(\s|$)", code), "never flush a chain"
    assert not re.search(r"iptables\s+(-t\s+\w+\s+)?(-P|--policy)\s", code), "never change a policy"
    assert not re.search(r"iptables\s+-t\s+nat", code)
    assert "lf-labs-iptables.service" in code
    assert "/swapfile-lf" in code


# ---------------------------------------------------------------- static: workflow


def _workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(wf: dict) -> dict:
    trig = wf.get("on", wf.get(True))
    if isinstance(trig, str):
        trig = {trig: None}
    elif isinstance(trig, list):
        trig = {t: None for t in trig}
    assert isinstance(trig, dict), "workflow has no triggers"
    return trig


def test_workflow_exists():
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"


def test_workflow_is_never_scheduled_and_has_only_the_two_triggers():
    wf = _workflow()
    trig = _triggers(wf)
    assert "schedule" not in trig
    assert set(trig) == {"workflow_dispatch", "push"}, set(trig)
    assert "cron" not in WORKFLOW.read_text(encoding="utf-8")


def test_workflow_push_trigger_is_limited_to_bootstrap_paths():
    push = _triggers(_workflow())["push"]
    assert isinstance(push, dict) and push.get("paths"), "push trigger needs a paths filter"
    assert "labs/deploy/bootstrap/**" in push["paths"]
    assert all(
        p.startswith("labs/deploy/bootstrap/") or p == ".github/workflows/bootstrap.yml"
        for p in push["paths"]
    ), push["paths"]


def test_workflow_uses_production_environment():
    jobs = (_workflow().get("jobs") or {}).values()
    envs = [j.get("environment") for j in jobs if isinstance(j, dict)]
    names = [e.get("name") if isinstance(e, dict) else e for e in envs]
    assert "production" in names, names


def test_workflow_defaults_to_dry_run():
    wf = _workflow()
    text = WORKFLOW.read_text(encoding="utf-8")
    inputs = (_triggers(wf)["workflow_dispatch"] or {}).get("inputs") or {}
    assert "apply" in inputs, "workflow_dispatch needs an `apply` input"
    assert str(inputs["apply"].get("default", "false")).lower() == "false"
    assert "DRY_RUN" in text
    assert "[bootstrap apply]" in text, "pushes apply only when the commit message says so"
    assert "bootstrap.sh" in text


def test_workflow_applies_only_on_a_whole_line_bootstrap_apply():
    """A commit that merely mentions the marker must not apply (D25 round 1).

    Rejected: a bare `contains(github.event.head_commit.message, '[bootstrap apply]')`.
    Accepted: an anchored whole-line match in the workflow text, i.e. `grep -x` / `grep -Fx`
    (combined flags such as `-qFx` too) on the marker, or a regex anchored with ^ and $
    around the escaped marker.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    bare = re.search(
        r"contains\(\s*github\.event\.head_commit\.message\s*,"
        r"\s*['\"]\[bootstrap apply\]['\"]\s*\)",
        text,
    )
    assert not bare, "bare contains() would match the marker anywhere in the message"
    grep_x = re.search(r"grep\s+(?:-\w+\s+)*-\w*x\w*\s+.*bootstrap apply", text)
    anchored = re.search(r"\^\s*\\?\[bootstrap apply\\?\]\s*\$", text)
    assert grep_x or anchored, "no whole-line (grep -x or ^...$) match of [bootstrap apply]"


# ---------------------------------------------------------------- docker helpers


def _run(cmd: list[str], timeout: int = 900, check: bool = False) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"{' '.join(cmd)} failed: {res.stdout}\n{res.stderr}"
    return res


def _docker(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    return _run(["docker", *args], timeout=timeout, check=check)


def _sudo(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["sudo", "-n", *args], check=check)


def _iptables() -> list[str]:
    out = _sudo("iptables", "-S", "DOCKER-USER").stdout
    return [ln.strip() for ln in out.splitlines() if ln.startswith("-A DOCKER-USER")]


def _chain_rules(chain: str, table_args: tuple[str, ...] = ()) -> list[str] | None:
    res = _run(["sudo", "-n", "iptables", *table_args, "-S", chain])
    if res.returncode != 0:
        return None
    return [ln.strip() for ln in res.stdout.splitlines() if ln.startswith(f"-A {chain} ")]


def _input_rules() -> list[str]:
    return _chain_rules("INPUT") or []


def _reset_runner() -> None:
    """Return the runner to the pre-bootstrap state (runsc in daemon.json stays)."""
    for chain in ("DOCKER-USER", "INPUT"):
        for rule in _chain_rules(chain) or []:
            if re.search(TAG_RE, rule):
                _run(["sudo", "-n", "iptables", "-D", chain, *shlex.split(rule)[2:]])
    if _chain_rules(CHAIN) is not None:
        _run(["sudo", "-n", "iptables", "-F", CHAIN])
        _run(["sudo", "-n", "iptables", "-X", CHAIN])
    res = _docker("network", "inspect", NET, check=False)
    if res.returncode == 0:
        for c in json.loads(res.stdout)[0].get("Containers", {}).values():
            _docker("rm", "-f", "-v", c["Name"], check=False)
        _docker("network", "rm", NET, check=False)
    _sudo("systemctl", "disable", "--now", UNIT, check=False)
    _sudo("rm", "-f", f"/etc/systemd/system/{UNIT}", f"/lib/systemd/system/{UNIT}", check=False)
    _sudo("systemctl", "daemon-reload", check=False)


def _daemon_bytes() -> bytes | None:
    res = _run(["sudo", "-n", "cat", DAEMON_JSON])
    return res.stdout.encode() if res.returncode == 0 else None


def _networks() -> list[str]:
    return sorted(_docker("network", "ls", "--format", "{{.Name}}").stdout.split())


def _swap() -> list[str]:
    out = _run(["swapon", "--show", "--noheadings"]).stdout
    return sorted(ln.strip() for ln in out.splitlines() if ln.strip())


BACKUP_GLOB = f"{DAEMON_JSON}.lf-bak.*"
SWAPFILE = "/swapfile-lf"


def _write_daemon(data: bytes | None) -> None:
    if data is None:
        _sudo("rm", "-f", DAEMON_JSON, check=False)
    else:
        subprocess.run(["sudo", "-n", "tee", DAEMON_JSON], input=data.decode(), text=True,
                       capture_output=True, check=True)


def _runtimes() -> dict:
    out = _docker("info", "--format", "{{json .Runtimes}}", check=False).stdout
    try:
        return json.loads(out) or {}
    except ValueError:
        return {}


def _reload_docker_and_wait(runsc: bool | None, wait: float = 40.0) -> None:
    """Reload (never restart) docker; optionally wait until runsc is listed / not listed."""
    _sudo("systemctl", "reload", "docker")
    deadline = time.time() + wait
    while time.time() < deadline:
        if runsc is None or ("runsc" in _runtimes()) == runsc:
            return
        time.sleep(1)


def _backups() -> list[str]:
    res = _run(["sudo", "-n", "sh", "-c", f"ls {BACKUP_GLOB} 2>/dev/null"])
    return res.stdout.split()


def _remove_backups() -> None:
    _run(["sudo", "-n", "sh", "-c", f"rm -f {BACKUP_GLOB}"])


TEMP_GLOBS = (f"{DAEMON_JSON}.lf-new.*", f"{DAEMON_JSON}.lf-restore.*", f"{DAEMON_JSON}.lf-stage.*")


def _temp_files() -> list[str]:
    pat = " ".join(TEMP_GLOBS)
    return _run(["sudo", "-n", "sh", "-c", f"ls {pat} 2>/dev/null"]).stdout.split()


def _state_text(label: str = "") -> str:
    """daemon.json, its backups, stray temp files and the runtimes docker lists, for a message."""
    data = _daemon_bytes()
    shown = "(no file)" if data is None else data.decode(errors="replace")
    return (f"--- daemon.json state {label}\n{shown}\n"
            f"--- backups: {_backups()}\n--- temp files: {_temp_files()}\n"
            f"--- docker runtimes: {sorted(_runtimes())}\n")


def _tail(res, lines: int = 60) -> str:
    out = (res.stdout or "") + (res.stderr or "")
    return "\n".join(out.splitlines()[-lines:])


def _wait_daemon_state_stable(wait: float = 30.0, still: float = 3.0) -> bool:
    """Wait (bounded) until daemon.json, the backups, the temp files and the runtimes docker lists
    stop changing for `still` seconds: a `systemctl reload docker` returns before dockerd has
    applied the file, and an earlier scenario may still be settling."""
    def _t(cmd):
        return _run(cmd, timeout=10).stdout

    def sample():
        try:
            info = _run(["docker", "info", "--format", "{{json .Runtimes}}"], timeout=10).stdout
            return (
                _t(["sudo", "-n", "cat", DAEMON_JSON]),
                _t(["sudo", "-n", "sh", "-c", f"ls {BACKUP_GLOB} 2>/dev/null"]),
                _t(["sudo", "-n", "sh", "-c", f"ls {' '.join(TEMP_GLOBS)} 2>/dev/null"]),
                "runsc" in info,
            )
        except subprocess.TimeoutExpired:
            return None  # not a stable sample

    deadline = time.time() + wait
    last, since = sample(), time.time()
    while time.time() < deadline:
        time.sleep(0.5)
        cur = sample()
        if cur is None or cur != last:
            last, since = cur, time.time()
        elif time.time() - since >= still:
            return True
    return False


def _snapshot_daemon_state() -> SimpleNamespace:
    """The runner's daemon.json (bytes, mode, owner), its backups and whether docker lists runsc."""
    _wait_daemon_state_stable()
    original = _daemon_bytes()
    st = None
    if original is not None:
        st = _run(["sudo", "-n", "stat", "-c", "%a %U:%G", DAEMON_JSON]).stdout.strip() or None
    return SimpleNamespace(original=original, stat=st, backups=_backups(),
                           temps=_temp_files(), had_runsc="runsc" in _runtimes())


def _precondition(ok: bool, what: str) -> None:
    """Fail as an exam problem (not a product failure), with the state of the runner."""
    if not ok:
        pytest.fail(f"exam precondition: {what}\n" + _state_text("at the failed precondition"),
                    pytrace=False)


def _check_clean_start(snap: SimpleNamespace, module: str) -> None:
    _precondition(not snap.backups and not snap.temps,
                  f"{module} found daemon.json backups or temp files left by an earlier "
                  f"scenario: {sorted(snap.backups)} {snap.temps}")


def _restore_daemon_state(snap: SimpleNamespace, reload: bool | None = None,
                          restart: bool = False) -> str:
    """Put daemon.json (bytes, mode, owner) back first, remove every backup and temp file this
    exam caused, then reload (or restart) docker and wait for a stable state. Returns the problem
    text ("" when fine) instead of failing: the caller does all its other cleanup, then fails
    last, so a leftover never skips a cleanup step or hides a product failure."""
    _remove_backups()
    _run(["sudo", "-n", "sh", "-c", "rm -f " + " ".join(TEMP_GLOBS)])
    changed = _daemon_bytes() != snap.original
    if changed:
        _write_daemon(snap.original)
    if snap.original is not None and snap.stat:
        mode, owner = snap.stat.split()
        _sudo("chmod", mode, DAEMON_JSON, check=False)
        _sudo("chown", owner, DAEMON_JSON, check=False)
    if restart:
        _sudo("systemctl", "restart", "docker", check=False)
        deadline = time.time() + 60.0
        while time.time() < deadline:
            if _run(["docker", "info", "--format", "{{.ServerVersion}}"]).returncode == 0:
                break
            time.sleep(1)
    elif changed if reload is None else reload:
        _sudo("systemctl", "reload", "docker", check=False)
    deadline = time.time() + 40.0
    while time.time() < deadline and ("runsc" in _runtimes()) != snap.had_runsc:
        time.sleep(1)
    problems = []
    if not _wait_daemon_state_stable():
        problems.append("daemon.json state did not settle within the bound")
    if ("runsc" in _runtimes()) != snap.had_runsc:
        problems.append(f"docker's runsc listing differs from the start (had runsc: "
                        f"{snap.had_runsc})")
    if not problems:
        return ""
    return "teardown: " + "; ".join(problems) + "\n" + _state_text("after the restore")


class _OneShotListener:
    """A TCP listener that accepts one connection, then shuts down and closes at once. shutdown()
    ends the listening state, so the kernel completes no handshake from the backlog afterwards."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.closed = threading.Event()
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self) -> None:
        try:
            c, _ = self.sock.accept()
        except OSError:
            return
        c.close()
        self._shut()
        self.closed.set()

    def _shut(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def finish(self) -> None:
        self._shut()  # wakes accept() when nothing ever connected
        self._t.join(timeout=5)


def _swap_names() -> list[str]:
    out = _run(["swapon", "--show=NAME", "--noheadings"]).stdout
    return sorted(ln.strip() for ln in out.splitlines() if ln.strip())


def _lf_rules() -> list[str]:
    out: list[str] = []
    for chain in ("DOCKER-USER", "INPUT"):
        out += [r for r in _chain_rules(chain) or [] if re.search(TAG_RE, r)]
    return out


def _foreign_start_times() -> dict[str, str]:
    names = _docker("ps", "-a", "--format", "{{.Names}}").stdout.split()
    out = {}
    for n in names:
        if n.startswith(("lf-", "lab-")):
            continue
        fmt = "{{.State.Running}} {{.State.StartedAt}}"
        out[n] = _docker("inspect", "-f", fmt, n).stdout.strip()
    return out


def _snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        daemon=_daemon_bytes(),
        rules=_iptables(),
        input_rules=_input_rules(),
        chain=_chain_rules(CHAIN),
        networks=_networks(),
        swap=_swap(),
        foreign=_foreign_start_times(),
    )


def _bootstrap(env: dict[str, str], unset: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    cmd = ["sudo", "-n", "env"]
    for k in unset:
        cmd += ["-u", k]
    cmd += [f"{k}={v}" for k, v in env.items()] + ["bash", str(SCRIPT)]
    return _run(cmd, timeout=1500)


def _last_line(res: subprocess.CompletedProcess) -> str:
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def _serve(host: str) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, 0), _Ok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def image() -> str:
    return lab_image.ensure_lab_image()


@pytest.fixture(scope="module")
def run():
    """Seed daemon.json, start a sentinel; run: dry, failing baseline, real, real again."""
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    baseline = _serve("127.0.0.1")
    bport = baseline.server_address[1]
    good_env = {
        "LF_BASELINE_URLS": f"http://127.0.0.1:{bport}/",
        "LF_BASELINE_TCP": f"127.0.0.1:{bport}",
        "LF_SWAP_MIN_FREE_GB": "1",  # the runner's disk is small
    }

    snap = _snapshot_daemon_state()
    _check_clean_start(snap, "test_issue_3_bootstrap")
    original = snap.original
    try:
        cfg = json.loads(original) if original and original.strip() else {}
    except ValueError:
        cfg = {}
    seeded_cfg = dict(cfg)
    seeded_cfg["max-concurrent-uploads"] = 4  # reloadable key the script must leave alone
    # The runner has runsc registered; remove it so the runtime step really runs.
    runtimes = dict(seeded_cfg.get("runtimes") or {})
    runtimes.pop("runsc", None)
    # Keep the key even when empty: dockerd reloads runtimes only when "runtimes" is
    # present in the file, so dropping the key would leave runsc registered in memory.
    seeded_cfg["runtimes"] = runtimes
    seeded = json.dumps(seeded_cfg, indent=2) + "\n"
    _run(["sudo", "-n", "mkdir", "-p", "/etc/docker"], check=True)
    orig_swaps = _swap_names()
    s = SimpleNamespace(good_env=good_env, seeded=json.loads(seeded), orig_swaps=orig_swaps)
    try:
        _write_daemon(seeded.encode())
        _reload_docker_and_wait(runsc=False)
        _precondition("runsc" not in _runtimes(), "could not unregister runsc before the exam")
        _run(["sudo", "-n", "swapoff", "-a"])
        _remove_backups()
        _sudo("rm", "-f", SWAPFILE, check=False)

        _reset_runner()
        _precondition(_wait_daemon_state_stable(), "daemon.json state did not settle")
        _precondition(_daemon_bytes() == seeded.encode(),
                      "daemon.json is not the seeded file when the scenarios start")
        _precondition(not _backups(), "daemon.json backups exist when the scenarios start")
        _precondition("runsc" not in _runtimes(), "docker lists runsc when the scenarios start")
        _docker("rm", "-f", SENTINEL, check=False)
        _docker("run", "-d", "--name", SENTINEL, "alpine:3.20", "sleep", "3600")
        s.before = _snapshot()
        s.dry = _bootstrap({**good_env, "DRY_RUN": "1"})
        s.after_dry = _snapshot()
        s.bad = _bootstrap({"LF_BASELINE_URLS": "http://127.0.0.1:9/", "LF_BASELINE_TCP": "",
                            "LF_SWAP_MIN_FREE_GB": "1"}, unset=("DRY_RUN",))
        s.after_bad = _snapshot()

        # Rollback after a step: the baseline passes once, at the start of the run (the only TCP
        # check before any change), then the listener is shut down and closed right after that
        # first connection, so every later baseline check fails: no timing window, the check
        # after the runtime step is the one that fails and rolls the runtime step back.
        trip = _OneShotListener()
        s.rb = _bootstrap({"LF_BASELINE_URLS": "", "LF_BASELINE_TCP": f"127.0.0.1:{trip.port}",
                           "LF_SWAP_MIN_FREE_GB": "1"}, unset=("DRY_RUN",))
        trip.finish()
        s.rb_closed = trip.closed.is_set()
        s.after_rb_runtimes = _runtimes()  # read at once: the rollback itself waits for runsc
        time.sleep(1)
        _wait_daemon_state_stable()
        s.state_after_rb = _state_text("after the rollback run")
        s.after_rb = _snapshot()
        s.after_rb_swapfile = os.path.exists(SWAPFILE)
        s.after_rb_lf_rules = _lf_rules()
        s.after_rb_nets = _networks()
        _remove_backups()
        # run1 must not depend on how the rollback scenario ended: seeded file, no runsc.
        _write_daemon(seeded.encode())
        _reload_docker_and_wait(runsc=False)
        _precondition(_wait_daemon_state_stable(), "daemon.json state did not settle")
        _precondition(_daemon_bytes() == seeded.encode(),
                      "daemon.json is not the seeded file before the real run")
        _precondition("runsc" not in _runtimes(), "docker lists runsc before the real run")
        _precondition(not _backups(), "daemon.json backups exist before the real run")

        s.run_env = {**good_env, "LF_SWAP_MIN_FREE_GB": "1"}
        s.run1 = _bootstrap(s.run_env, unset=("DRY_RUN",))
        s.after1 = _snapshot()
        s.backups1 = _backups()
        s.state_after1 = _state_text("after the first real run")
        s.backup_bytes = [_run(["sudo", "-n", "cat", b]).stdout.encode() for b in s.backups1]
        s.swap1 = _run(["swapon", "--show=NAME,SIZE", "--noheadings", "--bytes"]).stdout
        s.swapfile_mode = _run(["sudo", "-n", "stat", "-c", "%a", SWAPFILE]).stdout.strip()
        s.fstab = _run(["sudo", "-n", "cat", "/etc/fstab"]).stdout
        s.run2 = _bootstrap(s.run_env, unset=("DRY_RUN",))
        s.after2 = _snapshot()
        yield s
    finally:
        _docker("rm", "-f", "-v", SENTINEL, check=False)
        _reset_runner()
        restore_problem = _restore_daemon_state(snap, reload=True)
        _sudo("swapoff", SWAPFILE, check=False)
        _sudo("rm", "-f", SWAPFILE, check=False)
        _sudo("sed", "-i", f"\\|^{SWAPFILE} |d", "/etc/fstab", check=False)
        for name in orig_swaps:
            _sudo("swapon", name, check=False)
        baseline.shutdown()
        _precondition(not restore_problem, restore_problem)  # last: all cleanup is done


# ---------------------------------------------------------------- docker: dry run


@pytest.mark.docker
def test_dry_run_exits_zero_and_reports_dry_run(run):
    assert run.dry.returncode == 0, run.dry.stdout + run.dry.stderr
    assert _last_line(run.dry) == f"{RESULT} dry run"


@pytest.mark.docker
def test_dry_run_lists_planned_commands(run):
    out = run.dry.stdout
    assert "docker network create" in out
    assert NET in out and BRIDGE in out
    assert "iptables" in out and "DOCKER-USER" in out


@pytest.mark.docker
def test_dry_run_changes_nothing(run):
    b, a = run.before, run.after_dry
    assert a.daemon == b.daemon, "daemon.json changed by a dry run"
    assert a.rules == b.rules, "DOCKER-USER changed by a dry run"
    assert a.input_rules == b.input_rules, "INPUT changed by a dry run"
    assert a.chain is None, "a dry run created the LF-LABS-INPUT chain"
    assert a.networks == b.networks, "networks changed by a dry run"
    assert a.swap == b.swap, "swap changed by a dry run"
    assert a.foreign == b.foreign


# ---------------------------------------------------------------- docker: baseline failure


@pytest.mark.docker
def test_failing_baseline_fails_the_run_and_changes_nothing(run):
    assert run.bad.returncode != 0, "a failing baseline must fail the run"
    assert "baseline" in (run.bad.stdout + run.bad.stderr).lower()
    assert f"{RESULT} changed" not in run.bad.stdout
    b, a = run.after_dry, run.after_bad
    assert a.daemon == b.daemon, "daemon.json changed although the baseline failed"
    assert a.rules == b.rules, "DOCKER-USER changed although the baseline failed"
    assert a.input_rules == b.input_rules, "INPUT changed although the baseline failed"
    assert a.chain is None, "chain created although the baseline failed"
    assert a.networks == b.networks, "network created although the baseline failed"
    assert a.swap == b.swap
    assert a.foreign == b.foreign


# ---------------------------------------------------------------- docker: real run


@pytest.mark.docker
def test_real_run_succeeds_and_reports_changed(run):
    assert run.run1.returncode == 0, run.run1.stdout + run.run1.stderr
    assert _last_line(run.run1) == f"{RESULT} changed"


@pytest.mark.docker
def test_runsc_is_registered_and_runs_containers(run):
    assert run.run1.returncode == 0
    info = _docker("info", "--format", "{{json .Runtimes}}").stdout
    assert "runsc" in json.loads(info)
    res = _docker("run", "--rm", "--runtime=runsc", "hello-world", check=False)
    assert res.returncode == 0, res.stderr
    assert "Hello from Docker" in res.stdout


@pytest.mark.docker
def test_network_lf_labs_has_bridge_icc_off_and_subnet(run):
    assert run.run1.returncode == 0
    data = json.loads(_docker("network", "inspect", NET).stdout)[0]
    opts = data.get("Options") or {}
    assert opts.get("com.docker.network.bridge.name") == BRIDGE
    assert opts.get("com.docker.network.bridge.enable_icc") == "false"
    subnets = [c.get("Subnet") for c in (data.get("IPAM") or {}).get("Config") or []]
    assert subnets == [SUBNET], subnets
    assert not data.get("Internal"), "lf-labs must not be internal (allowlist proxy needs egress)"


@pytest.mark.docker
def test_added_iptables_rules_are_all_scoped_to_the_bridge_and_tagged(run):
    assert run.run1.returncode == 0
    added = list((Counter(run.after1.rules) - Counter(run.before.rules)).elements())
    assert added, "the script added no DOCKER-USER rule"
    for rule in added:
        assert re.search(r"-i br-lf-labs(\s|$)", rule), f"rule not scoped to br-lf-labs: {rule}"
        assert re.search(TAG_RE, rule), f"rule not tagged lf-labs: {rule}"


@pytest.mark.docker
def test_input_rules_added_are_scoped_and_tagged_and_chain_exists(run):
    assert run.run1.returncode == 0
    added = list((Counter(run.after1.input_rules) - Counter(run.before.input_rules)).elements())
    assert any(f"-j {CHAIN}" in r for r in added), f"no INPUT jump to {CHAIN}: {added}"
    for rule in added:
        assert re.search(r"-i br-lf-labs(\s|$)", rule), f"INPUT rule not scoped: {rule}"
        assert re.search(TAG_RE, rule), f"INPUT rule not tagged lf-labs: {rule}"
    assert run.after1.chain is not None, f"chain {CHAIN} does not exist after a real run"
    assert run.after1.chain, f"chain {CHAIN} is empty"
    for rule in run.after1.chain:
        assert re.search(r"-i br-lf-labs(\s|$)", rule), f"chain rule not scoped: {rule}"
        assert re.search(TAG_RE, rule), f"chain rule not tagged lf-labs: {rule}"
    assert any("ESTABLISHED" in r and "-j ACCEPT" in r for r in run.after1.chain)
    assert any(re.search(r"-j (DROP|REJECT)", r) for r in run.after1.chain)


@pytest.mark.docker
def test_no_preexisting_input_rule_was_changed(run):
    assert run.run1.returncode == 0
    after = iter(run.after1.input_rules)
    for rule in run.before.input_rules:
        assert any(rule == r for r in after), f"pre-existing INPUT rule lost or reordered: {rule}"


@pytest.mark.docker
def test_no_preexisting_docker_user_rule_was_changed(run):
    assert run.run1.returncode == 0
    after = iter(run.after1.rules)
    for rule in run.before.rules:
        assert any(rule == r for r in after), f"pre-existing rule lost or reordered: {rule}"


@pytest.mark.docker
def test_manager_accept_comes_before_drops_and_established_is_allowed(run):
    assert run.run1.returncode == 0
    ours = [r for r in run.after1.rules if re.search(TAG_RE, r)]
    accept = [i for i, r in enumerate(ours)
              if MANAGER_IP in r and "--dport 7681" in r and "-j ACCEPT" in r]
    drops = [i for i, r in enumerate(ours) if re.search(r"-j (DROP|REJECT)", r)]
    assert accept, f"no manager->lab tcp/7681 accept rule in {ours}"
    assert drops, "no drop rule tagged lf-labs"
    assert min(accept) < min(drops), "the manager accept must come before the first drop"
    assert any("ESTABLISHED" in r and "-j ACCEPT" in r for r in ours), "no established accept"


@pytest.mark.docker
def test_daemon_json_other_keys_are_untouched(run):
    assert run.run1.returncode == 0
    after = json.loads(run.after1.daemon)
    assert "runsc" in (after.get("runtimes") or {})
    assert after.get("max-concurrent-uploads") == 4, "pre-seeded key lost or changed"

    def strip(cfg: dict) -> dict:
        cfg = json.loads(json.dumps(cfg))
        (cfg.get("runtimes") or {}).pop("runsc", None)
        if "runtimes" in cfg and not cfg["runtimes"]:
            del cfg["runtimes"]
        return cfg

    assert json.dumps(strip(after), sort_keys=True) == json.dumps(strip(run.seeded), sort_keys=True)


@pytest.mark.docker
def test_swap_step_keeps_existing_swap(run):
    assert run.run1.returncode == 0
    gone = set(run.before.swap) - set(run.after1.swap)
    assert not gone, f"existing swap was removed: {gone}"


@pytest.mark.docker
def test_foreign_containers_are_not_restarted(run):
    assert run.run1.returncode == 0
    assert SENTINEL in run.before.foreign
    assert run.after1.foreign == run.before.foreign, "a non lf-/lab- container changed state"
    assert run.after2.foreign == run.before.foreign
    fmt = "{{.State.Running}} {{.State.StartedAt}}"
    started = _docker("inspect", "-f", fmt, SENTINEL).stdout
    assert started.strip() == run.before.foreign[SENTINEL]


# ---------------------------------------------------------------- docker: idempotence


@pytest.mark.docker
def test_second_run_reports_no_changes(run):
    assert run.run2.returncode == 0, run.run2.stdout + run.run2.stderr
    assert _last_line(run.run2) == f"{RESULT} no changes"


@pytest.mark.docker
def test_second_run_leaves_rules_network_and_daemon_json_identical(run):
    a, b = run.after1, run.after2
    assert b.rules == a.rules, "second run changed DOCKER-USER"
    assert b.input_rules == a.input_rules, "second run changed INPUT"
    assert b.chain == a.chain, "second run changed LF-LABS-INPUT"
    assert b.daemon == a.daemon, "second run rewrote daemon.json"
    assert b.networks == a.networks
    assert b.swap == a.swap


# ---------------------------------------------------------------- docker: isolation


def _lab(image: str, request, *extra: str) -> str:
    name = f"lf-exam-{uuid.uuid4().hex[:10]}"
    request.addfinalizer(lambda: _run(["docker", "rm", "-f", "-v", name], timeout=120))
    _docker(
        "run", "-d", "--name", name, "--runtime=runsc", "--log-driver", "none",
        "--cap-drop", "ALL", "--cap-add", "SETUID", "--cap-add", "SETGID",
        "--security-opt", "no-new-privileges", "-e", "LF_BASE_PATH=/s/x/term", *extra, image,
    )
    return name


def _ip(name: str, network: str) -> str:
    fmt = f'{{{{(index .NetworkSettings.Networks "{network}").IPAddress}}}}'
    out = _docker("inspect", "-f", fmt, name).stdout.strip()
    assert out, f"{name} has no IP on {network}"
    return out


def _curl(name: str, url: str, secs: int = 4) -> int:
    """Return curl's exit code from inside container `name` (0 = got an HTTP answer)."""
    return _docker("exec", name, "curl", "-sS", "-m", str(secs), "-o", "/dev/null", url,
                   check=False, timeout=60).returncode


def _wait_curl_ok(name: str, url: str, wait: float = 40.0) -> None:
    deadline = time.time() + wait
    while time.time() < deadline:
        if _curl(name, url) == 0:
            return
        time.sleep(1)
    raise AssertionError(f"{url} never answered from {name}")


@pytest.fixture
def labs(run, image, request):
    assert run.run1.returncode == 0, "bootstrap did not succeed"
    a = _lab(image, request, "--network", NET)
    b = _lab(image, request, "--network", NET)
    mgr = f"lf-exam-mgr-{uuid.uuid4().hex[:8]}"
    request.addfinalizer(lambda: _run(["docker", "rm", "-f", "-v", mgr], timeout=120))
    _docker("run", "-d", "--name", mgr, "--network", NET, "--ip", MANAGER_IP,
            "--entrypoint", "sleep", image, "3600")
    ip_a, ip_b = _ip(a, NET), _ip(b, NET)
    assert _ip(mgr, NET) == MANAGER_IP
    # Readiness is proven through the manager path, so the negative checks are not vacuous.
    _wait_curl_ok(mgr, f"http://{ip_a}:{PORT}/s/x/term/")
    _wait_curl_ok(mgr, f"http://{ip_b}:{PORT}/s/x/term/")
    return SimpleNamespace(a=a, b=b, mgr=mgr, ip_a=ip_a, ip_b=ip_b, image=image)


@pytest.mark.docker
def test_manager_address_reaches_lab_port_7681(labs):
    assert _curl(labs.mgr, f"http://{labs.ip_a}:{PORT}/s/x/term/") == 0


@pytest.mark.docker
def test_one_lab_cannot_open_7681_on_another(labs):
    assert _curl(labs.b, f"http://{labs.ip_a}:{PORT}/s/x/term/") != 0
    assert _curl(labs.a, f"http://{labs.ip_b}:{PORT}/s/x/term/") != 0


@pytest.mark.docker
def test_lab_cannot_reach_a_container_on_another_bridge(labs, image, request):
    other = f"lf-exam-other-{uuid.uuid4().hex[:8]}"
    request.addfinalizer(lambda: _run(["docker", "rm", "-f", "-v", other], timeout=120))
    _docker("run", "-d", "--name", other, "--runtime=runsc", "--log-driver", "none",
            "-e", "LF_BASE_PATH=/s/x/term", image)
    ip = _ip(other, "bridge")
    local = f"http://127.0.0.1:{PORT}/s/x/term/"
    deadline = time.time() + 40
    while time.time() < deadline and _curl(other, local) != 0:
        time.sleep(1)
    assert _curl(other, local) == 0, "control listener not up"
    assert _curl(labs.a, f"http://{ip}:{PORT}/s/x/term/") != 0


@pytest.mark.docker
def test_lab_cannot_reach_host_services(labs):
    data = json.loads(_docker("network", "inspect", NET).stdout)[0]
    gw = data["IPAM"]["Config"][0]["Gateway"]
    route = _run(["ip", "-4", "route", "get", "1.1.1.1"]).stdout
    m = re.search(r"src (\S+)", route)
    targets = [gw] + ([m.group(1)] if m else [])
    srv = _serve("0.0.0.0")  # noqa: S104 - listening on all host addresses is the point
    try:
        port = srv.server_address[1]
        for ip in targets:
            assert _curl(labs.a, f"http://{ip}:{port}/") != 0, f"lab reached host service on {ip}"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- docker: runtime step


def _canon(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True)


@pytest.mark.docker
def test_runtime_step_really_ran_runsc_was_unregistered_before(run):
    before = json.loads(run.before.daemon)
    assert "runsc" not in (before.get("runtimes") or {})
    assert run.run1.returncode == 0, run.run1.stdout + run.run1.stderr
    assert _last_line(run.run1) == f"{RESULT} changed"


@pytest.mark.docker
def test_runsc_registered_with_overlay2_root_memory(run):
    assert run.run1.returncode == 0
    cfg = json.loads(run.after1.daemon)
    entry = (cfg.get("runtimes") or {}).get("runsc")
    assert entry, "runtimes.runsc missing after the real run"
    assert "--overlay2=root:memory" in (entry.get("runtimeArgs") or []), entry
    assert "runsc" in _runtimes()


@pytest.mark.docker
def test_runtime_step_changes_no_other_daemon_json_key(run):
    assert run.run1.returncode == 0
    before = json.loads(run.before.daemon)
    after = json.loads(run.after1.daemon)
    for cfg in (before, after):  # same normalization on both sides
        (cfg.get("runtimes") or {}).pop("runsc", None)
        if not cfg.get("runtimes"):
            cfg.pop("runtimes", None)
    assert _canon(after) == _canon(before), "keys other than runtimes.runsc changed"
    assert after.get("max-concurrent-uploads") == 4


@pytest.mark.docker
def test_runtime_step_leaves_a_backup_equal_to_the_pre_run_file(run):
    assert run.run1.returncode == 0
    assert run.backups1, (f"no backup matching {BACKUP_GLOB} after the real run\n"
                          f"{run.state_after_rb}{run.state_after1}"
                          f"--- rollback run output\n{_tail(run.rb)}\n"
                          f"--- real run output\n{_tail(run.run1)}")
    assert run.before.daemon in run.backup_bytes, "no backup equals the pre-run daemon.json"


@pytest.mark.docker
def test_runsc_container_runs_after_the_runtime_step(run):
    assert run.run1.returncode == 0
    res = _docker("run", "--rm", "--runtime=runsc", "hello-world", check=False)
    assert res.returncode == 0, res.stderr
    assert "Hello from Docker" in res.stdout


# ---------------------------------------------------------------- docker: swap step


@pytest.mark.docker
def test_swap_step_creates_a_4gb_600_swapfile_and_fstab_line(run):
    assert run.run1.returncode == 0, run.run1.stdout + run.run1.stderr
    assert run.before.swap == [], "exam precondition: swap was off before run 1"
    rows = [ln.split() for ln in run.swap1.splitlines() if ln.strip()]
    mine = [r for r in rows if r[0] == SWAPFILE]
    assert mine, f"{SWAPFILE} is not an active swap: {run.swap1}"
    size_gb = int(mine[0][1]) / 2**30
    assert 3.5 <= size_gb <= 4.5, f"swapfile is {size_gb:.2f} GB, expected about 4"
    assert run.swapfile_mode == "600", run.swapfile_mode
    fstab_line = re.search(rf"^{re.escape(SWAPFILE)}\s", run.fstab, re.M)
    assert fstab_line, "no fstab line for the swapfile"


@pytest.mark.docker
def test_second_run_does_not_touch_swap_and_reports_no_changes(run):
    assert run.run2.returncode == 0, run.run2.stdout + run.run2.stderr
    assert _last_line(run.run2) == f"{RESULT} no changes"
    assert run.after2.swap == run.after1.swap


# ---------------------------------------------------------------- docker: rollback after a step


@pytest.mark.docker
def test_baseline_failing_after_the_first_step_rolls_everything_back(run):
    assert run.rb_closed, "exam precondition: the listener took the first connection and closed"
    _precondition("== step runtime: applying" in run.rb.stdout + run.rb.stderr,
                  "the runtime step did not run in the rollback scenario\n" + _tail(run.rb))
    assert run.rb.returncode != 0, run.rb.stdout + run.rb.stderr
    assert "baseline" in (run.rb.stdout + run.rb.stderr).lower()
    assert f"{RESULT} changed" not in run.rb.stdout
    assert "baseline differs after step 'runtime'" in run.rb.stdout + run.rb.stderr, (
        "the baseline check that failed is not the one after the runtime step\n" + _tail(run.rb))
    assert run.after_rb.daemon == run.before.daemon, (
        "daemon.json not restored byte for byte\n"
        f"--- seeded (pre-run)\n{(run.before.daemon or b'').decode(errors='replace')}\n"
        f"{run.state_after_rb}--- rollback run output\n{_tail(run.rb)}")
    assert "runsc" not in run.after_rb_runtimes, "docker still lists runsc (not reloaded back)"
    assert NET not in run.after_rb_nets, "lf-labs network left behind"
    assert not run.after_rb_lf_rules, f"rules tagged lf-labs left behind: {run.after_rb_lf_rules}"
    assert not run.after_rb_swapfile, f"{SWAPFILE} left behind"
    assert run.after_rb.swap == run.before.swap
    assert run.after_rb.foreign == run.before.foreign, "a foreign container was restarted"


# ---------------------------------------------------------------- docker: network checks


@pytest.mark.docker
def test_lf_labs_network_has_ipv6_disabled(run):
    assert run.run1.returncode == 0
    data = json.loads(_docker("network", "inspect", NET).stdout)[0]
    assert data.get("EnableIPv6") is False, data.get("EnableIPv6")


@pytest.mark.docker
def test_lab_has_no_internet(labs):
    url = "http://1.1.1.1"
    res = _docker("exec", labs.a, "curl", "-sS", "-m", "5", "-o", "/dev/null", url,
                  check=False, timeout=60)
    assert res.returncode != 0, "a lab on lf-labs reached the public internet"
    ctl = _run(["docker", "run", "--rm", "--entrypoint", "curl", labs.image,
                "-sS", "-m", "10", "-o", "/dev/null", url], timeout=120)
    if ctl.returncode != 0:
        pytest.fail("contrast failed: a container on the default bridge cannot reach "
                    f"{url} either, so the no-internet check would be vacuous: {ctl.stderr}")
