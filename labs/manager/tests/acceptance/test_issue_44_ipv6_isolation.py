"""Acceptance tests for issue #44: IPv6 isolation of the lab containers on `lf-labs`.

Interface under test: `labs/deploy/bootstrap/bootstrap.sh` and what it leaves on the host, plus
what lab containers (runsc) can reach over IPv6 on `br-lf-labs` (spec 6, D20, D25).

`lf-labs` is created with `--ipv6=false`, but under gVisor the lab may still get a link-local
(fe80::) address, and then lab B could open lab A's ttyd on [::]:7681, or a host service, over
IPv6, around the IPv4 rules. Two outcomes are acceptable and the docker tests pass in both:
- no usable IPv6 at all (no fe80 address on the lab or the bridge), or
- IPv6 exists but `ip6tables` drops tagged `lf-labs` (`-i br-lf-labs`) block the traffic.
The manager decided the bootstrap adds one of the two mechanisms anyway (defense in depth): tagged
`ip6tables` drops, or `net.ipv6.conf.br-lf-labs.disable_ipv6=1` persisted under /etc/sysctl.d/.

Static cases (no Docker) check the script text and run everywhere. Docker cases reuse the fixtures
of the #3 exam (`run`, `image`, `labs`) and run in CI's docker job with gVisor. Reachability is
probed with curl (in the lab image): a probe only counts when curl ran and failed to connect
(exit 7) or timed out (exit 28); a missing tool, a malformed URL or a reachable port can never
pass vacuously. All data is fake.
"""

import importlib.util
import json
import re
import socket
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "lf_issue3_helpers_44", _HERE / "test_issue_3_bootstrap.py"
)
assert _spec and _spec.loader
h3 = importlib.util.module_from_spec(_spec)
sys.modules["lf_issue3_helpers_44"] = h3
_spec.loader.exec_module(h3)

SCRIPT: Path = h3.SCRIPT
BRIDGE: str = h3.BRIDGE
NET: str = h3.NET
PORT: int = h3.PORT
RESULT: str = h3.RESULT
TAG_RE: str = h3.TAG_RE
_run = h3._run
_docker = h3._docker
_last_line = h3._last_line
_bootstrap = h3._bootstrap

# The fixtures of the #3 exam, reused as they are (no second copy of the bootstrap setup).
run = h3.run
image = h3.image
labs = h3.labs

SYSCTL_KEY = f"net.ipv6.conf.{BRIDGE}.disable_ipv6"
BLOCKED = (7, 28)  # curl: could not connect / timed out. Anything else is not a verdict.


# ---------------------------------------------------------------- static: the script


def _code() -> str:
    """Script code without comment lines, with $BRIDGE / $TAG written out."""
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    text = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^BRIDGE=br-lf-labs\s*$", text, re.M), "BRIDGE is not br-lf-labs"
    assert re.search(r"^TAG=lf-labs\s*$", text, re.M), "TAG is not lf-labs"
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    out = "\n".join(lines)
    for var, val in (("BRIDGE", BRIDGE), ("TAG", "lf-labs")):
        out = out.replace("${" + var + "}", val).replace("$" + var, val)
    return out


def _functions(code: str) -> dict[str, str]:
    """name -> body, for `name() {` ... a closing `}` at column 0."""
    funcs: dict[str, str] = {}
    for m in re.finditer(r"^([A-Za-z_]\w*)\(\)\s*\{.*?^\}", code, re.M | re.S):
        funcs[m.group(1)] = m.group(0)
    return funcs


def _uses_ip6tables(code: str) -> bool:
    return bool(re.search(r"\bip6tables\b", code))


def _uses_sysctl(code: str) -> bool:
    return "disable_ipv6" in code


def _need_mechanism(code: str) -> None:
    """No mechanism at all is a failure, never a skip."""
    assert _uses_ip6tables(code) or _uses_sysctl(code), "no IPv6 mechanism in the script"


def _v6_rule_lines(code: str) -> list[str]:
    """Lines that spell out an IPv6 rule: in an ip6tables call, or in a function named *6*."""
    lines = [ln for ln in code.splitlines() if re.search(r"\bip6tables\b", ln) and " -j " in ln]
    for name, body in _functions(code).items():
        if "6" in name:
            lines += [ln for ln in body.splitlines() if re.search(r"\s-j\s", ln)]
    return lines


def _rules_only_code(code: str) -> str:
    """The `--rules-only` branch plus every function reachable from it."""
    m = re.search(r'^if \[ "\$\{1:-\}" = "--rules-only" \]; then\n(.*?)^fi\n', code, re.M | re.S)
    assert m, "no --rules-only branch in the script"
    funcs = _functions(code)
    seen: set[str] = set()
    todo = [m.group(1)]
    while todo:
        text = todo.pop()
        for name, body in funcs.items():
            if name not in seen and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text):
                seen.add(name)
                todo.append(body)
    return m.group(1) + "\n" + "\n".join(funcs[n] for n in sorted(seen))


def test_script_adds_an_ipv6_mechanism_ip6tables_drops_or_bridge_sysctl():
    code = _code()
    assert _uses_ip6tables(code) or _uses_sysctl(code), (
        "bootstrap.sh adds neither tagged ip6tables drops nor "
        f"{SYSCTL_KEY}=1 for the lab bridge"
    )


def test_ip6tables_rules_match_only_the_lab_bridge_and_are_tagged():
    code = _code()
    _need_mechanism(code)
    if not _uses_ip6tables(code):
        pytest.skip("this implementation uses the bridge sysctl, not ip6tables")
    rules = _v6_rule_lines(code)
    assert rules, "ip6tables is used but no rule with a -j target is spelled out"
    for ln in rules:
        assert re.search(r"-i\s+br-lf-labs\b", ln), f"IPv6 rule not scoped to the bridge: {ln}"
        assert re.search(r"--comment\s+\"?lf-labs\"?(\s|$)", ln), f"IPv6 rule not tagged: {ln}"


def test_ip6tables_drops_cover_input_and_forwarding():
    code = _code()
    _need_mechanism(code)
    if not _uses_ip6tables(code):
        pytest.skip("this implementation uses the bridge sysctl, not ip6tables")
    v6 = "\n".join(ln for ln in code.splitlines() if "ip6tables" in ln) + "\n".join(
        body for name, body in _functions(code).items() if "6" in name
    )
    assert "INPUT" in v6, "no ip6tables rule in INPUT (traffic to the host's own addresses)"
    assert "DOCKER-USER" in v6 or "FORWARD" in v6, "no ip6tables rule in DOCKER-USER or FORWARD"
    assert re.search(r"-j\s+(DROP|REJECT)", "\n".join(_v6_rule_lines(code))), (
        "no IPv6 rule drops anything"
    )


def test_ip6tables_is_idempotent_checked_before_adding_and_never_flushes():
    code = _code()
    _need_mechanism(code)
    if not _uses_ip6tables(code):
        pytest.skip("this implementation uses the bridge sysctl, not ip6tables")
    assert re.search(r"ip6tables\b[^\n]*\s-C\b", code), "no `ip6tables -C` check before adding"
    assert not re.search(r"ip6tables\b[^\n]*\s-F(\s|$)", code), "never flush a chain"
    assert not re.search(r"ip6tables\b[^\n]*\s(-P|--policy)\s", code), "never change a policy"
    assert "iptables-persistent" not in code
    for ln in code.splitlines():
        if re.search(r"\bip6tables\s+(-w\s+\d+\s+)?-(I|A|N)\b", ln):
            assert re.search(r"\brun\s", ln) or "command ip6tables" in ln, (
                f"ip6tables insert that bypasses the dry-run `run` helper: {ln.strip()}"
            )


def test_sysctl_targets_only_the_lab_bridge_and_is_persisted_under_sysctl_d():
    code = _code()
    _need_mechanism(code)
    if not _uses_sysctl(code):
        pytest.skip("this implementation uses ip6tables, not the bridge sysctl")
    assert SYSCTL_KEY in code, f"the sysctl is not {SYSCTL_KEY}"
    assert not re.search(r"net\.ipv6\.conf\.(all|default)\.", code), (
        "never change the IPv6 sysctls of all or default interfaces"
    )
    assert re.search(r"/etc/sysctl\.d/[\w.-]+\.conf", code), (
        "the sysctl is not persisted in a file under /etc/sysctl.d/"
    )
    assert not re.search(r"/etc/sysctl\.conf", code), "never edit the owner's /etc/sysctl.conf"


def test_ipv6_protection_is_reapplied_by_the_boot_path_rules_only():
    code = _code()
    reach = _rules_only_code(code)
    ok = (_uses_ip6tables(code) and "ip6tables" in reach) or (
        _uses_sysctl(code) and "disable_ipv6" in reach
    )
    assert ok, "--rules-only (lf-labs-iptables.service at boot) does not re-apply the IPv6 part"


def test_ipv6_step_has_a_rollback():
    code = _code()
    if _uses_ip6tables(code):
        assert re.search(r"ip6tables\b[^\n]*\s-D\b", code), (
            "ip6tables rules are added but nothing removes them on rollback"
        )
    elif _uses_sysctl(code):
        undo = re.search(r"disable_ipv6[^\n]*=\s*0", code) or re.search(
            r"\brm\b[^\n]*sysctl\.d", code
        )
        assert undo, "the sysctl is set and persisted but nothing undoes it on rollback"
    else:
        pytest.fail("no IPv6 mechanism in the script")


def test_script_never_restarts_docker_and_has_an_ipv6_step():
    code = _code()
    assert not re.search(r"systemctl\s+restart\s+(docker|containerd)", code)
    assert not re.search(r"service\s+docker\s+restart", code)
    assert _uses_ip6tables(code) or _uses_sysctl(code), "no IPv6 mechanism in the script"


# ---------------------------------------------------------------- docker helpers


def _host_v6_state() -> SimpleNamespace:
    res = _run(["sudo", "-n", "ip6tables", "-S"])
    rules = res.stdout if res.returncode == 0 else "(ip6tables unavailable)"
    sc = _run(["sudo", "-n", "sysctl", "-n", SYSCTL_KEY])
    value = sc.stdout.strip() if sc.returncode == 0 else None
    files: dict[str, str] = {}
    found = _run(["sudo", "-n", "grep", "-rls", "br-lf-labs", "/etc/sysctl.d"])
    for path in sorted(found.stdout.split()):
        files[path] = _run(["sudo", "-n", "cat", path]).stdout
    return SimpleNamespace(rules=rules, sysctl=value, files=files)


def _tagged_v6_rules(state: SimpleNamespace) -> list[str]:
    return [ln for ln in state.rules.splitlines()
            if ln.startswith("-A ") and re.search(TAG_RE, ln)]


def _v6_clean() -> None:
    """Test harness only: remove IPv6 protection the bootstrap left (never part of the script)."""
    state = _host_v6_state()
    for ln in _tagged_v6_rules(state):
        _run(["sudo", "-n", "ip6tables", "-D", *ln.split()[1:]])
    for path in state.files:
        _run(["sudo", "-n", "rm", "-f", path])
    if state.sysctl is not None:
        _run(["sudo", "-n", "sysctl", "-w", f"{SYSCTL_KEY}=0"])


@pytest.fixture(scope="module")
def v6_pre():
    """Runs before the bootstrap fixture: no IPv6 protection left over from an earlier module."""
    _v6_clean()
    yield
    _v6_clean()


@pytest.fixture(scope="module")
def v6_runs(v6_pre, run):
    """After the #3 runs: a third real run, then drift (protection removed) with a dry run and a
    repair run. Every step is bracketed by a snapshot of ip6tables, the sysctl and its files."""
    assert run.run1.returncode == 0, "bootstrap did not succeed"
    s = SimpleNamespace()
    s.protected = _host_v6_state()
    s.run3 = _bootstrap(run.run_env, unset=("DRY_RUN",))
    s.after3 = _host_v6_state()
    _v6_clean()
    s.drifted = _host_v6_state()
    s.dry = _bootstrap({**run.good_env, "DRY_RUN": "1"})
    s.after_dry = _host_v6_state()
    s.repair = _bootstrap(run.run_env, unset=("DRY_RUN",))
    s.repaired = _host_v6_state()
    return s


def _protected(state: SimpleNamespace) -> tuple[bool, str]:
    """(ok, why not): ip6tables drops for INPUT and forwarding, or the persisted bridge sysctl."""
    rules = _tagged_v6_rules(state)
    for ln in rules:
        if not re.search(r"-i br-lf-labs(\s|$)", ln):
            return False, f"IPv6 rule not scoped to the bridge: {ln}"
    chains = {ln.split()[1] for ln in rules}
    by_rules = "INPUT" in chains and bool(chains & {"DOCKER-USER", "FORWARD"})
    persisted = any(
        re.search(rf"{re.escape(SYSCTL_KEY)}\s*=\s*1\b", text) for text in state.files.values()
    )
    by_sysctl = state.sysctl == "1" and persisted
    if by_rules or by_sysctl:
        return True, ""
    return False, (f"tagged ip6tables chains {sorted(chains)}, {SYSCTL_KEY}={state.sysctl}, "
                   f"sysctl.d files {sorted(state.files)}")


def _link_local(lab: str) -> list[tuple[str, str]]:
    """(interface, fe80 address) pairs inside `lab`; asserts the `ip` tool really ran."""
    res = _docker("exec", lab, "ip", "-o", "link", "show", check=False)
    assert res.returncode == 0 and "eth0" in res.stdout, f"`ip link` did not run in {lab}: {res}"
    res = _docker("exec", lab, "ip", "-6", "-o", "addr", "show", "scope", "link", check=False)
    assert res.returncode == 0, f"`ip -6 addr` did not run in {lab}: {res.stderr}"
    return re.findall(r"^\d+:\s+(\S+)\s+inet6\s+(fe80:[0-9a-f:]+)/\d+", res.stdout, re.M)


def _eui64(mac: str) -> str:
    b = [int(x, 16) for x in mac.split(":")]
    b[0] ^= 2
    return f"fe80::{b[0]:02x}{b[1]:02x}:{b[2]:02x}ff:fe{b[3]:02x}:{b[4]:02x}{b[5]:02x}"


def _lab_mac(lab: str) -> str:
    fmt = f'{{{{(index .NetworkSettings.Networks "{NET}").MacAddress}}}}'
    mac = _docker("inspect", "-f", fmt, lab).stdout.strip()
    assert re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac), f"no MAC for {lab}: {mac!r}"
    return mac


def _url(addr: str, scope: str | None, port: int) -> str:
    host = f"{addr}%25{scope}" if scope else addr
    return f"http://[{host}]:{port}/"


def _probe(lab: str, addr: str, scope: str | None, port: int, secs: int = 4) -> int:
    """curl exit code from inside `lab` towards an IPv6 literal (-g: no URL globbing)."""
    return _docker("exec", lab, "curl", "-g", "-6", "-sS", "-m", str(secs), "-o", "/dev/null",
                   _url(addr, scope, port), check=False, timeout=60).returncode


def _assert_probe_works(lab: str, scope: str) -> None:
    """The probe tool is there and understands scoped IPv6 literals: a closed port is exit 7."""
    for addr, sc in (("::1", None), ("fe80::1", scope)):
        rc = _probe(lab, addr, sc, 1)
        assert rc in BLOCKED, (
            f"control probe of [{addr}]:1 from {lab} gave curl exit {rc}, expected 7 or 28: "
            "the probe would not prove anything (curl missing, URL not understood, ...)"
        )


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------- docker: isolation over IPv6


@pytest.fixture
def lab_pair(v6_pre, labs):
    """Two labs on lf-labs, ready, after the bootstrap (v6_pre first: see its docstring)."""
    return labs


@pytest.mark.docker
def test_lab_b_cannot_open_lab_a_port_7681_over_ipv6_link_local(lab_pair):
    labs = lab_pair
    a_ll = _link_local(labs.a)
    b_ll = _link_local(labs.b)
    _assert_probe_works(labs.b, "eth0")
    targets = {addr for _, addr in a_ll}
    targets.add(_eui64(_lab_mac(labs.a)))  # also the address lab A would have under EUI-64
    if not a_ll:
        # Outcome 1 (no usable IPv6): lab A has no fe80 address at all; stated, not assumed.
        assert a_ll == [] and b_ll == [], "inconsistent IPv6 state between the two labs"
    for addr in sorted(targets):
        for scope in sorted({"eth0", *(i for i, _ in b_ll)}):
            rc = _probe(labs.b, addr, scope, PORT)
            assert rc in BLOCKED, (
                f"lab B reached [{addr}%{scope}]:{PORT} on lab A over IPv6 (curl exit {rc}); "
                f"lab A link-local addresses: {a_ll}"
            )


@pytest.mark.docker
def test_lab_a_cannot_open_lab_b_port_7681_over_ipv6_either_way(lab_pair):
    labs = lab_pair
    _assert_probe_works(labs.a, "eth0")
    targets = {addr for _, addr in _link_local(labs.b)} | {_eui64(_lab_mac(labs.b))}
    for addr in sorted(targets):
        rc = _probe(labs.a, addr, "eth0", PORT)
        assert rc in BLOCKED, f"lab A reached [{addr}%eth0]:{PORT} on lab B over IPv6 (exit {rc})"


@pytest.mark.docker
def test_lab_cannot_reach_the_host_over_ipv6(lab_pair):
    labs = lab_pair
    _assert_probe_works(labs.a, "eth0")
    link = _run(["ip", "-o", "link", "show", BRIDGE])
    assert link.returncode == 0, f"host has no {BRIDGE}: {link.stderr}"
    mac = re.search(r"link/ether\s+((?:[0-9a-f]{2}:){5}[0-9a-f]{2})", link.stdout)
    assert mac, f"no MAC on {BRIDGE}: {link.stdout}"
    bridge_ll = re.findall(
        r"inet6\s+(fe80:[0-9a-f:]+)/\d+",
        _run(["ip", "-6", "-o", "addr", "show", "dev", BRIDGE, "scope", "link"]).stdout,
    )
    glob = re.findall(
        r"inet6\s+([0-9a-f:]+)/\d+",
        _run(["ip", "-6", "-o", "addr", "show", "scope", "global"]).stdout,
    )
    try:
        srv = _V6Server(("::", 0), _Handler)
    except OSError as e:
        pytest.fail(f"precondition: cannot listen on [::] on the runner: {e}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        control = _run(["curl", "-g", "-6", "-sS", "-m", "5", "-o", "/dev/null",
                        _url("::1", None, port)])
        assert control.returncode == 0, f"host IPv6 service not reachable from the host: {control}"
        if not bridge_ll:
            # Outcome 1 (no usable IPv6): the bridge has no fe80 address; stated, not assumed.
            assert bridge_ll == []
        targets = [(a, "eth0") for a in sorted({*bridge_ll, _eui64(mac.group(1))})]
        targets += [(a, None) for a in glob if not a.startswith("fe80")]
        for addr, scope in targets:
            rc = _probe(labs.a, addr, scope, port)
            assert rc in BLOCKED, (
                f"lab reached the host service on [{addr}] over IPv6 (curl exit {rc}); "
                f"bridge link-local: {bridge_ll}, host global: {glob}"
            )
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.docker
def test_lab_cannot_reach_a_container_on_another_bridge_over_ipv6(lab_pair, image, request):
    labs = lab_pair
    other = f"lf-exam-other6-{uuid.uuid4().hex[:8]}"
    request.addfinalizer(lambda: _run(["docker", "rm", "-f", "-v", other], timeout=120))
    _docker("run", "-d", "--name", other, "--runtime=runsc", "--log-driver", "none",
            "--entrypoint", "sleep", image, "3600")
    mac = json.loads(_docker("inspect", other).stdout)[0]["NetworkSettings"]["MacAddress"]
    assert mac, "no MAC on the default bridge container"
    _assert_probe_works(labs.a, "eth0")
    rc = _probe(labs.a, _eui64(mac), "eth0", PORT)
    assert rc in BLOCKED, f"lab reached a default-bridge container over IPv6 (curl exit {rc})"


# ---------------------------------------------------------------- docker: the bootstrap's IPv6 step


@pytest.mark.docker
def test_bootstrap_leaves_an_ipv6_protection_in_place(v6_runs):
    ok, why = _protected(v6_runs.protected)
    assert ok, f"after the real runs there is no IPv6 protection: {why}"


@pytest.mark.docker
def test_ipv6_rules_added_are_scoped_to_the_bridge_tagged_and_persisted(v6_runs):
    state = v6_runs.protected
    for ln in _tagged_v6_rules(state):
        assert re.search(r"-i br-lf-labs(\s|$)", ln), f"not scoped to the bridge: {ln}"
    for ln in state.rules.splitlines():
        if ln.startswith("-A ") and re.search(r"-i br-lf-labs(\s|$)", ln):
            assert re.search(TAG_RE, ln), f"IPv6 rule on the bridge without the lf-labs tag: {ln}"
    if state.sysctl == "1":
        assert any(SYSCTL_KEY in t for t in state.files.values()), (
            f"{SYSCTL_KEY}=1 is live but not persisted under /etc/sysctl.d/"
        )
    assert state.sysctl in (None, "0", "1")


@pytest.mark.docker
def test_third_run_reports_no_changes_and_leaves_ipv6_state_identical(v6_runs):
    assert v6_runs.run3.returncode == 0, v6_runs.run3.stdout + v6_runs.run3.stderr
    assert _last_line(v6_runs.run3) == f"{RESULT} no changes"
    a, b = v6_runs.protected, v6_runs.after3
    assert b.rules == a.rules, "a repeated run changed the ip6tables rules"
    assert b.sysctl == a.sysctl, "a repeated run changed the bridge sysctl"
    assert b.files == a.files, "a repeated run rewrote the sysctl.d files"


@pytest.mark.docker
def test_dry_run_changes_nothing_ipv6_while_the_protection_is_missing(v6_runs):
    assert not _protected(v6_runs.drifted)[0], "precondition: protection still present"
    assert v6_runs.dry.returncode == 0, v6_runs.dry.stdout + v6_runs.dry.stderr
    assert _last_line(v6_runs.dry) == f"{RESULT} dry run"
    a, b = v6_runs.drifted, v6_runs.after_dry
    assert b.rules == a.rules, "the dry run changed the ip6tables rules"
    assert b.sysctl == a.sysctl, "the dry run changed the bridge sysctl"
    assert b.files == a.files, "the dry run wrote sysctl.d files"


@pytest.mark.docker
def test_a_real_run_repairs_missing_ipv6_protection_and_reports_changed(v6_runs):
    assert v6_runs.repair.returncode == 0, v6_runs.repair.stdout + v6_runs.repair.stderr
    assert _last_line(v6_runs.repair) == f"{RESULT} changed"
    ok, why = _protected(v6_runs.repaired)
    assert ok, f"the repair run did not restore the IPv6 protection: {why}"
    assert v6_runs.repaired.rules == v6_runs.protected.rules
    assert v6_runs.repaired.sysctl == v6_runs.protected.sysctl
    assert v6_runs.repaired.files == v6_runs.protected.files
