"""Acceptance tests for issue #627: IPv6 guard hardening (follow-up of #44, D20, D167, spec 6).

Interface under test: `labs/deploy/bootstrap/bootstrap.sh` and what it leaves on the host, plus
what lab containers can reach over IPv6 on `br-lf-labs`. The #44 exam stays as it is; this file
adds what its review (PR #626) found missing.

Seams the exam expects from the implementation (everything else is behaviour):
- `PROC_ROOT` (environment, default `/proc`): the script reads `$PROC_ROOT/net/if_inet6` ("the
  kernel has IPv6" means that file exists) and `$PROC_ROOT/sys/net/bridge/bridge-nf-call-ip6tables`
  (must read 1 while the IPv6 forwarding guard is in place) and nothing else under /proc for this.
- `ip6tables` and `iptables` are found through `PATH`; the exam puts fakes first. The IPv6 guard
  never calls `sysctl` to write the bridge-nf value (read-only check).
- The code before the line `if [ "${1:-}" = "--rules-only" ]; then` only defines things (no side
  effect with `$1` = `--rules-only`), and `rules_missing` and `apply_rules` keep their names: the
  exam drives "the iptables step" as `rules_missing; apply_rules` with `CURRENT=iptables` (what
  `need_iptables` / `apply_iptables` do, minus the systemd unit and /usr/local/sbin copy that
  need root). The bridge-nf check and the fail-closed check must be reachable from `apply_rules`
  (for example inside `apply_ip6`), so the boot path and the normal run both have them.
- Outcome of "fails and rolls back": non-zero exit, an `ip6tables` line on stderr or stdout and,
  in the `CURRENT=iptables` flow, no tagged IPv4 rule left from this run.

Sandbox cases run everywhere (they use fake `iptables`/`ip6tables` that keep their rules in a
state directory). Docker cases are marked `docker`, reuse the fixtures of the #3 and #44 exams and
run in CI's docker job with gVisor. All data is fake.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "lf_issue44_helpers_627", _HERE / "test_issue_44_ipv6_isolation.py"
)
assert _spec and _spec.loader
h44 = importlib.util.module_from_spec(_spec)
sys.modules["lf_issue44_helpers_627"] = h44
_spec.loader.exec_module(h44)
h3 = h44.h3

SCRIPT: Path = h3.SCRIPT
BRIDGE: str = h3.BRIDGE
NET: str = h3.NET
PORT: int = h3.PORT
RESULT: str = h3.RESULT
TAG_RE: str = h3.TAG_RE
BLOCKED = h44.BLOCKED
_run = h3._run
_docker = h3._docker
_last_line = h3._last_line
_bootstrap = h3._bootstrap

# Fixtures of the #3 and #44 exams, reused as they are.
run = h3.run
image = h3.image
v6_pre = h44.v6_pre

BR_NF = "bridge-nf-call-ip6tables"
BASH = shutil.which("bash")
START_RULES_ONLY = 'if [ "${1:-}" = "--rules-only" ]; then'


# ---------------------------------------------------------------- sandbox


def _posix(p: Path) -> str:
    return p.as_posix()


_FAKE_TABLES = r"""#!/bin/sh
# Fake iptables/ip6tables for the exam: rules live in $FAKE_STATE/<tool>.rules as "chain args".
tool=$(basename "$0")
st="$FAKE_STATE"
printf '%s %s\n' "$tool" "$*" >> "$st/calls.log"
if [ -f "$st/$tool.broken" ]; then echo "$tool: unusable (fake)" >&2; exit 3; fi
if [ "$1" = "-w" ]; then shift 2; fi
cmd=$1; shift
rules="$st/$tool.rules"; chains="$st/$tool.chains"
touch "$rules" "$chains"
case "$cmd" in
  -n)
    shift
    if [ -f "$st/$tool.late" ] && [ "$1" = "DOCKER-USER" ]; then
      # dockerd creates this chain 2 seconds after it was first asked about
      now=$(date +%s)
      [ -f "$st/$tool.first" ] || echo "$now" > "$st/$tool.first"
      first=$(cat "$st/$tool.first")
      [ $((now - first)) -ge 2 ] || exit 1
    fi
    grep -qx -e "$1" "$chains" ;;
  -C) grep -Fxq -e "$*" "$rules" ;;
  -I|-A)
    chain=$1; shift
    case "$1" in ''|*[!0-9]*) ;; *) shift ;; esac
    printf '%s %s\n' "$chain" "$*" >> "$rules" ;;
  -D)
    awk -v l="$*" '!d && $0==l {d=1; next} {print}' "$rules" > "$rules.tmp"
    mv "$rules.tmp" "$rules" ;;
  -N) printf '%s\n' "$1" >> "$chains" ;;
  -X) grep -vx -e "$1" "$chains" > "$chains.tmp"; mv "$chains.tmp" "$chains" ;;
  -S) sed 's/^/-A /' "$rules" ;;
  *) exit 0 ;;
esac
"""

_FAKE_SYSCTL = """#!/bin/sh
echo "sysctl $*" >> "$FAKE_STATE/calls.log"
exit 0
"""

_PLAIN_TOOLS = (
    "bash sh env date readlink dirname basename cat grep sed awk tr cut head tail sort uniq wc rm "
    "mv cp mkdir touch sleep seq cmp tee id timeout stat expr ls ln chmod install mktemp"
).split()


def _real_tools(names: list[str]) -> dict[str, str]:
    assert BASH, "bash is needed for the sandbox cases"
    snippet = 'for t in "$@"; do p=$(command -v "$t") && printf "%s=%s\\n" "$t" "$p"; done'
    out = subprocess.run([BASH, "-c", snippet, "x", *names], capture_output=True, text=True)
    return dict(ln.split("=", 1) for ln in out.stdout.splitlines() if "=" in ln)


def _write_exec(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").encode())
    path.chmod(0o755)


_BASE_BIN: dict[str, Path] = {}


def _base_bin(root_factory) -> Path:
    """One directory of tool shims and fakes (no ip6tables) shared by all sandbox cases."""
    if "dir" not in _BASE_BIN:
        d = root_factory.mktemp("lf627-base-bin")
        for name, real in _real_tools(_PLAIN_TOOLS).items():
            _write_exec(d / name, "#!/bin/sh\nexec '" + real + "' \"$@\"\n")
        for name in ("docker", "systemctl"):
            _write_exec(d / name, "#!/bin/sh\nexit 0\n")
        _write_exec(d / "sysctl", _FAKE_SYSCTL)
        _write_exec(d / "iptables", _FAKE_TABLES)
        _BASE_BIN["dir"] = d
    return _BASE_BIN["dir"]


class Sandbox:
    """A PATH with fakes, a fake /proc and a state directory; runs bootstrap.sh pieces in it."""

    def __init__(self, root: Path, base: Path):
        self.base = base
        self.bin = root / "bin"
        self.state = root / "state"
        self.proc = root / "proc"
        for d in (self.bin, self.state, self.proc / "net", self.proc / "sys/net/bridge"):
            d.mkdir(parents=True, exist_ok=True)
        (self.state / "calls.log").write_bytes(b"")
        self.chains("iptables", "INPUT FORWARD OUTPUT DOCKER-USER")
        self.configure()

    def chains(self, tool: str, names: str) -> None:
        (self.state / f"{tool}.chains").write_bytes(("\n".join(names.split()) + "\n").encode())

    def add_chain(self, tool: str, name: str) -> None:
        with open(self.state / f"{tool}.chains", "a", newline="\n") as f:
            f.write(name + "\n")

    def configure(self, *, inet6: bool = True, bridge_nf: str | None = "1",
                  ip6tables: str = "ok", ip6_docker_user: str = "early") -> "Sandbox":
        """ip6tables: ok | missing | broken. bridge_nf None: the proc file does not exist.
        ip6_docker_user: early (there from the start) | late (appears 2 s after the first look)."""
        iff = self.proc / "net/if_inet6"
        if inet6:
            iff.write_bytes(b"00000000000000000000000000000001 01 80 10 80       lo\n")
        elif iff.exists():
            iff.unlink()
        nf = self.proc / "sys/net/bridge" / BR_NF
        if bridge_nf is None:
            nf.unlink(missing_ok=True)
        else:
            nf.write_bytes((bridge_nf + "\n").encode())
        (self.bin / "ip6tables").unlink(missing_ok=True)
        (self.state / "ip6tables.broken").unlink(missing_ok=True)
        if ip6tables != "missing":
            _write_exec(self.bin / "ip6tables", _FAKE_TABLES)
        if ip6tables == "broken":
            (self.state / "ip6tables.broken").write_bytes(b"")
        for stale in ("ip6tables.late", "ip6tables.first"):
            (self.state / stale).unlink(missing_ok=True)
        if ip6_docker_user == "late":
            (self.state / "ip6tables.late").write_bytes(b"")
        self.chains("ip6tables", "INPUT FORWARD OUTPUT DOCKER-USER")
        return self

    def env(self) -> dict[str, str]:
        e = dict(os.environ)
        path = os.pathsep.join([_posix(self.bin), _posix(self.base)])
        e.update(PATH=path, PROC_ROOT=_posix(self.proc),
                 FAKE_STATE=_posix(self.state), LF_BASELINE_URLS="", LF_BASELINE_TCP="")
        for k in ("DRY_RUN",):
            e.pop(k, None)
        return e

    def _prelude(self) -> Path:
        text = SCRIPT.read_text(encoding="utf-8")
        assert START_RULES_ONLY in text, "bootstrap.sh has no --rules-only branch"
        path = self.state / "prelude.sh"
        path.write_bytes(text.split(START_RULES_ONLY, 1)[0].replace("\r\n", "\n").encode())
        return path

    def rules_only(self, timeout: int = 300) -> subprocess.CompletedProcess:
        assert BASH
        return subprocess.run([BASH, _posix(SCRIPT), "--rules-only"], env=self.env(),
                              capture_output=True, text=True, timeout=timeout)

    def step(self, timeout: int = 300) -> subprocess.CompletedProcess:
        """The iptables step of a normal run: `rules_missing; apply_rules` with CURRENT=iptables.

        need_iptables asks rules_missing and, whatever it says (the boot copy or the unit may be
        what is missing), apply_iptables then calls apply_rules: both see the availability test.
        """
        assert BASH
        harness = self.state / "harness.sh"
        harness.write_bytes(
            (
                "set -Eeuo pipefail\n"
                "set -- --rules-only\n"
                f'source "{_posix(self._prelude())}"\n'
                "CURRENT=iptables\n"
                "rules_missing || true\n"
                "apply_rules\n"
                "echo 'HARNESS: step finished'\n"
            ).encode()
        )
        return subprocess.run([BASH, _posix(harness)], env=self.env(), capture_output=True,
                              text=True, timeout=timeout)

    def rules(self, tool: str) -> list[str]:
        p = self.state / f"{tool}.rules"
        return [ln for ln in p.read_text().splitlines() if ln] if p.exists() else []

    def calls(self) -> list[str]:
        return (self.state / "calls.log").read_text().splitlines()

    def tagged6(self) -> list[str]:
        return [r for r in self.rules("ip6tables") if re.search(TAG_RE, r)]


@pytest.fixture
def make_sb(tmp_path, tmp_path_factory):
    if not BASH:
        pytest.fail("precondition: bash is not available")
    base = _base_bin(tmp_path_factory)

    def make(name: str = "sb") -> Sandbox:
        return Sandbox(tmp_path / name, base)

    return make


@pytest.fixture
def sb(make_sb) -> Sandbox:
    return make_sb()


def _out(res: subprocess.CompletedProcess) -> str:
    return res.stdout + res.stderr


def _chains_of(rules: list[str]) -> set[str]:
    return {r.split()[0] for r in rules}


def _forward_chains(sb: Sandbox) -> set[str]:
    return {c for c in _chains_of(sb.tagged6()) if c != "INPUT"}


# ---------------------------------------------------------------- static: the script


def test_bridge_nf_call_ip6tables_is_checked_and_never_written():
    code = h44._code()
    assert re.search(rf"sys/net/bridge/{BR_NF}", code), f"the script never looks at {BR_NF}"
    for ln in code.splitlines():
        if BR_NF in ln:
            assert not re.search(r">\s*\S*" + BR_NF, ln), f"writes the bridge-nf value: {ln}"
            assert not re.search(r"\btee\b", ln), f"writes the bridge-nf value: {ln}"
    assert not re.search(r"sysctl\s+[^\n]*(-w|--write)\b[^\n]*bridge", code)
    assert not re.search(r"sysctl\s+[^\n]*bridge-nf[^\n]*=", code), "sets a bridge-nf sysctl"
    assert "modprobe br_netfilter" not in code, "the check is read-only: no module loading"


def test_no_ipv6_is_decided_from_proc_net_if_inet6():
    code = h44._code()
    assert re.search(r"net/if_inet6", code), "the kernel's IPv6 presence is not read from if_inet6"


def test_availability_is_not_tested_with_command_v_on_the_wrapper_function():
    code = h44._code()
    assert not re.search(r"command\s+-v\s+ip6tables", code), (
        "`command -v ip6tables` always succeeds: ip6tables() is a function in this script"
    )


def test_ip6_input_rule_text_is_a_tagged_bridge_drop_not_a_list_call():
    """The INPUT drop is matched by its rule text (`INPUT -i br-lf-labs ... -j DROP`)."""
    code = h44._code()
    ok = [
        ln for ln in code.splitlines()
        if re.search(r"\bINPUT\s+-i\s+br-lf-labs\b", ln)
        and re.search(r"--comment\s+lf-labs\b", ln)
        and re.search(r"-j\s+DROP\b", ln)
        and not re.search(r"\s-L\b", ln)
    ]
    assert ok, "no IPv6 INPUT rule text `INPUT -i br-lf-labs -m comment --comment lf-labs -j DROP`"


# ---------------------------------------------------------------- sandbox: bridge-nf-call-ip6tables


@pytest.mark.parametrize("value", ["0", None], ids=["value-0", "file-absent"])
def test_step_fails_with_a_clear_line_when_bridge_nf_call_ip6tables_is_not_1(sb, value):
    sb.configure(bridge_nf=value)
    res = sb.step()
    assert res.returncode != 0, "the step succeeded while lab-to-lab IPv6 would bypass the guard"
    assert BR_NF in _out(res), f"no clear line about {BR_NF}:\n{_out(res)}"
    assert "HARNESS: step finished" not in res.stdout


def test_step_with_bridge_nf_call_ip6tables_1_succeeds_and_reads_only(sb):
    sb.configure(bridge_nf="1")
    nf = sb.proc / "sys/net/bridge" / BR_NF
    before = (nf.read_text(), nf.stat().st_mtime_ns)
    time.sleep(0.05)
    res = sb.step()
    assert res.returncode == 0, _out(res)
    assert (nf.read_text(), nf.stat().st_mtime_ns) == before, "the value file was written"
    assert not [c for c in sb.calls() if c.startswith("sysctl ")], "the guard called sysctl"
    assert sb.tagged6(), "no IPv6 guard was put in place"


def test_bridge_nf_call_ip6tables_value_is_left_alone_when_it_is_0(sb):
    sb.configure(bridge_nf="0")
    nf = sb.proc / "sys/net/bridge" / BR_NF
    sb.step()
    assert nf.read_text().strip() == "0", "the script changed the host's bridge-nf value"


def test_no_ipv6_kernel_does_not_need_bridge_nf_call_ip6tables(sb):
    sb.configure(inet6=False, bridge_nf=None, ip6tables="missing")
    res = sb.step()
    assert res.returncode == 0, f"a host without IPv6 must not fail on bridge-nf:\n{_out(res)}"
    assert "HARNESS: step finished" in res.stdout


# ---------------------------------------------------------------- sandbox: fail closed


@pytest.mark.parametrize("state", ["missing", "broken"])
def test_kernel_ipv6_with_unusable_ip6tables_fails_the_step_instead_of_skipping(sb, state):
    sb.configure(inet6=True, ip6tables=state)
    res = sb.step()
    assert res.returncode != 0, f"guard skipped with {state} ip6tables on an IPv6 kernel"
    assert "ip6tables" in _out(res), f"no clear line about ip6tables:\n{_out(res)}"
    assert "HARNESS: step finished" not in res.stdout


@pytest.mark.parametrize("state", ["missing", "broken"])
def test_failed_ipv6_guard_rolls_back_what_the_run_added(sb, state):
    sb.configure(inet6=True, ip6tables=state)
    res = sb.step()
    assert res.returncode != 0
    left = [r for r in sb.rules("iptables") if re.search(TAG_RE, r)]
    assert not left, f"the failed step left tagged IPv4 rules behind: {left}"
    assert not sb.tagged6()


@pytest.mark.parametrize("state", ["missing", "broken"])
def test_boot_path_rules_only_also_fails_closed(sb, state):
    sb.configure(inet6=True, ip6tables=state)
    res = sb.rules_only()
    assert res.returncode != 0, f"--rules-only skipped the guard with {state} ip6tables"


def test_kernel_without_ipv6_skips_the_guard_and_says_so_once(sb):
    sb.configure(inet6=False, ip6tables="missing")
    assert sb.step().returncode == 0  # IPv4 rules now in place: the next step only re-checks
    res = sb.step()
    assert res.returncode == 0, _out(res)
    said = [
        ln for ln in _out(res).splitlines()
        if re.search(r"ipv6|ip6tables", ln, re.I) and not ln.startswith("+ ")
    ]
    assert len(said) == 1, f"the no-IPv6 line should appear once per run, got {said}"
    assert not [c for c in sb.calls() if c.startswith("ip6tables")]


def test_kernel_without_ipv6_skips_the_guard_in_rules_only_too(sb):
    sb.configure(inet6=False, ip6tables="missing")
    res = sb.rules_only()
    assert res.returncode == 0, _out(res)


def test_a_working_ip6tables_is_found_through_path_and_used(sb):
    """The fixed availability test still says yes when ip6tables is really there."""
    sb.configure(inet6=True, ip6tables="ok")
    assert sb.step().returncode == 0
    assert any(c.startswith("ip6tables") for c in sb.calls())


# ---------------------------------------------------------------- sandbox: one chain


def test_ip6_rules_are_scoped_tagged_and_cover_input_and_forwarding(sb):
    res = sb.rules_only()
    assert res.returncode == 0, _out(res)
    rules = sb.tagged6()
    assert any(r.startswith("INPUT ") and r.endswith("-j DROP") for r in rules), rules
    assert any(not r.startswith("INPUT ") and r.endswith("-j DROP") for r in rules), rules
    for r in rules:
        assert re.search(r"-i br-lf-labs(\s|$)", r), f"not scoped to the bridge: {r}"
    assert sorted(_chains_of(rules) - {"INPUT"}) in (["DOCKER-USER"], ["FORWARD"])
    assert len(rules) == 2


def test_rules_only_and_the_normal_step_put_the_forwarding_guard_in_the_same_chain(make_sb):
    a = make_sb("a")
    b = make_sb("b")
    assert a.rules_only().returncode == 0
    assert b.step().returncode == 0
    assert _forward_chains(a) == _forward_chains(b) != set(), (
        f"rules-only: {_forward_chains(a)}, normal step: {_forward_chains(b)}"
    )
    assert sorted(a.tagged6()) == sorted(b.tagged6())


def test_rules_only_with_a_late_docker_user_chain_matches_an_early_one(make_sb):
    """Boot race: dockerd creates the ip6tables DOCKER-USER chain just after the unit starts."""
    early = make_sb("early")
    assert early.rules_only().returncode == 0
    late = make_sb("late").configure(ip6_docker_user="late")
    res = late.rules_only()
    assert res.returncode == 0, _out(res)
    assert _forward_chains(late) == _forward_chains(early), (
        f"early chain {_forward_chains(early)}, late chain {_forward_chains(late)}"
    )


def test_repeated_runs_never_leave_a_tagged_copy_in_a_second_chain(make_sb):
    late = make_sb("late").configure(ip6_docker_user="late")
    assert late.rules_only().returncode == 0
    time.sleep(2.5)  # by now the chain exists for everybody
    first = sorted(late.tagged6())
    assert late.rules_only().returncode == 0
    assert late.step().returncode == 0
    assert late.rules_only().returncode == 0
    assert sorted(late.tagged6()) == first, "a repeated run changed or added IPv6 rules"
    assert len(_forward_chains(late)) == 1, f"tagged copies in {_forward_chains(late)}"
    assert len(late.tagged6()) == 2


def test_a_second_normal_step_adds_nothing(sb):
    assert sb.step().returncode == 0
    n = len(sb.calls())
    first = sorted(sb.tagged6())
    assert sb.step().returncode == 0
    assert sorted(sb.tagged6()) == first
    new = sb.calls()[n:]
    assert not [c for c in new if re.search(r"ip6tables\s+(-w\s+\d+\s+)?-[IAD]\b", c)]


# ---------------------------------------------------------------- docker helpers


def _set_sysctl(key: str, value: str) -> str | None:
    old = _run(["sudo", "-n", "sysctl", "-n", key])
    res = _run(["sudo", "-n", "sysctl", "-w", f"{key}={value}"])
    assert res.returncode == 0, f"cannot set {key}={value}: {res.stderr}"
    return old.stdout.strip() if old.returncode == 0 else None


@pytest.fixture
def guarded(v6_pre, run):
    """After the #3 runs: the IPv6 guard is in place (a real run, with bridge-nf on)."""
    assert run.run1.returncode == 0, "bootstrap did not succeed"
    _run(["sudo", "-n", "modprobe", "br_netfilter"])
    key = f"net.bridge.{BR_NF}"
    old = _set_sysctl(key, "1")
    h44._v6_clean()
    res = _bootstrap(run.run_env, unset=("DRY_RUN",))
    assert res.returncode == 0, res.stdout + res.stderr
    assert h44._tagged_v6_rules(h44._host_v6_state()), "no tagged IPv6 rule after a real run"
    yield run
    if old is not None:
        _run(["sudo", "-n", "sysctl", "-w", f"{key}={old}"])


PORT6 = 7682  # the exam's own IPv6 listener: the lab image's ttyd (port 7681) is IPv4-only (D170)


def _serve6(lab: str, port: int = PORT6) -> None:
    """Start a second ttyd with -6 (listens on [::]) in `lab`, and prove it answers on [::1]."""
    _docker("exec", "-d", lab, "ttyd", "-6", "-p", str(port), "sleep", "3600")
    _listens6(lab, port)


def _listens6(lab: str, port: int = PORT6, wait: float = 40.0) -> None:
    """Proof that `lab` has a listener on IPv6: curl to [::1] gets an HTTP answer (exit 0)."""
    deadline = time.time() + wait
    rc = -1
    while time.time() < deadline:
        rc = _docker("exec", lab, "curl", "-g", "-6", "-sS", "-m", "4", "-o", "/dev/null",
                     f"http://[::1]:{port}/", check=False, timeout=60).returncode
        if rc == 0:
            return
        time.sleep(1)
    raise AssertionError(f"{lab} does not listen on IPv6 [::1]:{port} (curl exit {rc})")


def _record_outcome(record_property, links: list) -> str:
    outcome = "blocked" if links else "no IPv6"
    record_property("ipv6_outcome", outcome)
    print(f"IPv6 outcome: {outcome}")
    return outcome


def _lab_fixture_pair(image, request):
    a = h3._lab(image, request, "--network", NET)
    b = h3._lab(image, request, "--network", NET)
    mgr = f"lf-exam-mgr6-{uuid.uuid4().hex[:8]}"
    request.addfinalizer(lambda: _run(["docker", "rm", "-f", "-v", mgr], timeout=120))
    _docker("run", "-d", "--name", mgr, "--network", NET, "--ip", h3.MANAGER_IP,
            "--entrypoint", "sleep", image, "3600")
    for ip in (h3._ip(a, NET), h3._ip(b, NET)):
        h3._wait_curl_ok(mgr, f"http://{ip}:{PORT}/s/x/term/")
    return a, b


# ---------------------------------------------------------------- docker: bootstrap behaviour


@pytest.mark.docker
def test_bootstrap_fails_when_bridge_nf_call_ip6tables_is_off_and_never_writes_it(guarded):
    run = guarded
    key = f"net.bridge.{BR_NF}"
    old = _set_sysctl(key, "0")
    try:
        h44._v6_clean()
        res = _bootstrap(run.run_env, unset=("DRY_RUN",))
        assert res.returncode != 0, "the step passed with bridge-nf-call-ip6tables=0"
        assert BR_NF in res.stdout + res.stderr, "no clear line about bridge-nf-call-ip6tables"
        assert _last_line(res) == f"{RESULT} failed"
        now = _run(["sudo", "-n", "sysctl", "-n", key]).stdout.strip()
        assert now == "0", "the script changed the host's bridge-nf value"
        assert not h44._tagged_v6_rules(h44._host_v6_state()), "the failed step was not rolled back"
        _set_sysctl(key, "1")
        res = _bootstrap(run.run_env, unset=("DRY_RUN",))
        assert res.returncode == 0, res.stdout + res.stderr
        assert h44._tagged_v6_rules(h44._host_v6_state())
    finally:
        if old is not None:
            _run(["sudo", "-n", "sysctl", "-w", f"{key}={old}"])


@pytest.mark.docker
def test_bootstrap_fails_and_rolls_back_when_ip6tables_is_missing_on_an_ipv6_kernel(
    guarded, tmp_path
):
    run = guarded
    assert Path("/proc/net/if_inet6").exists(), "precondition: the runner kernel has no IPv6"
    farm = tmp_path / "bin"
    farm.mkdir()
    seen: set[str] = set()
    for src in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"):
        d = Path(src)
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.name.startswith("ip6tables") or f.name in seen:
                continue
            seen.add(f.name)
            (farm / f.name).symlink_to(f)
    assert not (farm / "ip6tables").exists()
    h44._v6_clean()
    env = {**run.run_env, "PATH": str(farm)}
    res = _bootstrap(env, unset=("DRY_RUN",))
    assert res.returncode != 0, "the guard was skipped without ip6tables on an IPv6 kernel"
    assert _last_line(res) == f"{RESULT} failed"
    assert "ip6tables" in res.stdout + res.stderr
    assert not h44._tagged_v6_rules(h44._host_v6_state())


@pytest.mark.docker
def test_rules_only_and_a_normal_run_leave_the_forwarding_guard_in_one_chain(guarded):
    run = guarded
    base = h44._tagged_v6_rules(h44._host_v6_state())
    assert base
    chains = {ln.split()[1] for ln in base if ln.split()[1] != "INPUT"}
    assert len(chains) == 1, f"tagged forwarding rules in {chains}"
    # boot path over a complete guard: nothing new
    res = _run(["sudo", "-n", "bash", str(SCRIPT), "--rules-only"])
    assert res.returncode == 0, res.stdout + res.stderr
    assert h44._tagged_v6_rules(h44._host_v6_state()) == base
    # boot path from scratch, then a normal run: same chain, no second copy
    h44._v6_clean()
    res = _run(["sudo", "-n", "bash", str(SCRIPT), "--rules-only"])
    assert res.returncode == 0, res.stdout + res.stderr
    boot = h44._tagged_v6_rules(h44._host_v6_state())
    assert sorted(boot) == sorted(base), "rules-only used another chain than the normal run"
    res = _bootstrap(run.run_env, unset=("DRY_RUN",))
    assert res.returncode == 0, res.stdout + res.stderr
    assert sorted(h44._tagged_v6_rules(h44._host_v6_state())) == sorted(base)


# ---------------------------------------------------------------- docker: isolation over IPv6


@pytest.mark.docker
def test_cross_bridge_probe_targets_a_real_ipv6_listener(guarded, image, request, record_property):
    a = h3._lab(image, request, "--network", NET)
    _docker("exec", a, "true")
    other = h3._lab(image, request)  # default bridge; the lab ttyd is IPv4-only: add our own
    _serve6(other)
    nets = h3.json.loads(_docker("inspect", other).stdout)[0]["NetworkSettings"]["Networks"]
    mac = nets["bridge"]["MacAddress"]
    assert mac, "no MAC on the default bridge container"
    h44._assert_probe_works(a, "eth0")
    targets = {addr for _, addr in h44._link_local(other)} | {h44._eui64(mac)}
    _record_outcome(record_property, h44._link_local(a))
    for addr in sorted(targets):
        rc = h44._probe(a, addr, "eth0", PORT6)
        assert rc in BLOCKED, f"lab reached the listener on [{addr}] of another bridge (exit {rc})"


@pytest.mark.docker
def test_lab_to_lab_target_listens_on_ipv6_and_the_outcome_is_recorded(
    guarded, image, request, record_property
):
    a, b = _lab_fixture_pair(image, request)
    a_ll, b_ll = h44._link_local(a), h44._link_local(b)
    outcome = _record_outcome(record_property, a_ll)
    h44._assert_probe_works(b, "eth0")
    _serve6(a)  # lab ttyd is IPv4-only: lab A gets its own IPv6 listener
    targets = {addr for _, addr in a_ll} | {h44._eui64(h44._lab_mac(a))}
    for addr in sorted(targets):
        for scope in sorted({"eth0", *(i for i, _ in b_ll)}):
            rc = h44._probe(b, addr, scope, PORT6)
            assert rc in BLOCKED, (
                f"outcome {outcome}: lab B reached [{addr}%{scope}]:{PORT6} on lab A (exit {rc})"
            )


# ---------------------------------------------------------------- docker: the drops really block


def _helper(image: str, request, nets_ready: list[str]) -> str:
    """A plain (runc) container on lf-labs with IPv6 forced on (`_serve6` adds a listener)."""
    name = f"lf-exam-v6h-{uuid.uuid4().hex[:8]}"
    request.addfinalizer(lambda: _run(["docker", "rm", "-f", "-v", name], timeout=120))
    _docker("run", "-d", "--name", name, "--network", NET, "--log-driver", "none",
            "--cap-drop", "ALL", "--cap-add", "SETUID", "--cap-add", "SETGID",
            "--security-opt", "no-new-privileges", "-e", "LF_BASE_PATH=/s/x/term",
            "--sysctl", "net.ipv6.conf.all.disable_ipv6=0",
            "--sysctl", "net.ipv6.conf.default.disable_ipv6=0", image)
    pid = _docker("inspect", "-f", "{{.State.Pid}}", name).stdout.strip()
    res = _run(["sudo", "-n", "nsenter", "-t", pid, "-n", "sysctl", "-w",
                "net.ipv6.conf.eth0.disable_ipv6=0"])
    assert res.returncode == 0, f"cannot force IPv6 on in {name}: {res.stderr}"
    deadline = time.time() + 20
    while time.time() < deadline:
        out = _docker("exec", name, "ip", "-6", "-o", "addr", "show", "scope", "link",
                      check=False).stdout
        if "fe80:" in out and "tentative" not in out:
            return name
        time.sleep(1)
    raise AssertionError(f"{name} got no usable link-local address with IPv6 forced on")


class _GuardOff:
    """Context: the tagged ip6tables drops are removed, and always put back."""

    def __enter__(self):
        self.rules = h44._tagged_v6_rules(h44._host_v6_state())
        assert self.rules, "precondition: no tagged IPv6 rule to take out"
        for ln in self.rules:
            _run(["sudo", "-n", "ip6tables", "-D", *ln.split()[1:]])
        return self

    def __exit__(self, *exc):
        for ln in reversed(self.rules):
            parts = ln.split()[1:]
            _run(["sudo", "-n", "ip6tables", "-I", parts[0], "1", *parts[1:]])


def _wait_reach(src: str, addr: str, scope: str, port: int, wait: float = 15.0) -> int:
    rc = -1
    deadline = time.time() + wait
    while time.time() < deadline:
        rc = h44._probe(src, addr, scope, port)
        if rc == 0:
            return 0
        time.sleep(1)
    return rc


@pytest.mark.docker
def test_helper_pair_with_ipv6_forced_on_is_blocked_by_the_forwarding_drop(guarded, image, request):
    a = _helper(image, request, [])
    b = _helper(image, request, [])
    _serve6(a)
    (_, a_ll), *_ = h44._link_local(a)
    h44._assert_probe_works(b, "eth0")
    rc = h44._probe(b, a_ll, "eth0", PORT6)
    assert rc in BLOCKED, f"with the guard in place B reached A over IPv6 (exit {rc})"
    with _GuardOff():
        rc = _wait_reach(b, a_ll, "eth0", PORT6)
        assert rc == 0, (
            f"control failed: without the drops B cannot reach A either (exit {rc}), so a blocked "
            "probe proves nothing (is net.bridge.bridge-nf-call-ip6tables 1?)"
        )
    rc = h44._probe(b, a_ll, "eth0", PORT6)
    assert rc in BLOCKED, f"after the guard came back B reached A over IPv6 (exit {rc})"


@pytest.mark.docker
def test_helper_with_ipv6_forced_on_is_blocked_by_the_input_drop(guarded, image, request):
    key = f"net.ipv6.conf.{BRIDGE}.disable_ipv6"
    old = _set_sysctl(key, "0")
    request.addfinalizer(lambda: old is not None and _set_sysctl(key, old))
    helper = _helper(image, request, [])
    deadline = time.time() + 20
    bridge_ll: list[str] = []
    while time.time() < deadline and not bridge_ll:
        out = _run(["ip", "-6", "-o", "addr", "show", "dev", BRIDGE, "scope", "link"]).stdout
        if "tentative" not in out:
            bridge_ll = re.findall(r"inet6\s+(fe80:[0-9a-f:]+)/\d+", out)
        time.sleep(1)
    assert bridge_ll, f"{BRIDGE} has no usable link-local address with IPv6 enabled on it"
    srv = h44._V6Server(("::", 0), h44._Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        h44._assert_probe_works(helper, "eth0")
        control = _run(["curl", "-g", "-6", "-sS", "-m", "5", "-o", "/dev/null",
                        h44._url("::1", None, port)])
        assert control.returncode == 0, f"host listener not reachable from the host: {control}"
        rc = h44._probe(helper, bridge_ll[0], "eth0", port)
        assert rc in BLOCKED, f"with the guard in place the helper reached the host (exit {rc})"
        with _GuardOff():
            rc = _wait_reach(helper, bridge_ll[0], "eth0", port)
            assert rc == 0, (
                f"control failed: without the drops the helper cannot reach the host (exit {rc})"
            )
        rc = h44._probe(helper, bridge_ll[0], "eth0", port)
        assert rc in BLOCKED, f"after the guard came back the helper reached the host (exit {rc})"
    finally:
        srv.shutdown()
        srv.server_close()
