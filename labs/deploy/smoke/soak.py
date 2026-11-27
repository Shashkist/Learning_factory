"""Nightly soak (issue #656, decisions D182, D186): 30 sessions in a row through the public URL.

One session on `smoke-tasks` gets no heartbeat until the janitor ends it (`heartbeat`), one on the
internal lab `smoke-soak` (1 minute limit) runs into its limit (`time_limit`), the other 28 are
ended by the student (`student`). Afterwards no `lab-*` container and no orphan lab volume is left
and the server's available memory and disk are not lower than before (within a tolerance).

    SMOKE_ID=... SMOKE_PASSWORD=... [SSH_CONFIG=path] python soak.py [BASE_URL]

The server is only read (over the smoke ssh config); every action goes through the public API as
the smoke student. Prints one summary line; one line per failure on stderr; exit 0 or 1. Never
prints the password, the cookie or the student id.
"""
import os
import subprocess
import sys
import time
from collections.abc import Callable

from lab_api import Fail, end_session, login, request, start

DEFAULT_BASE = "https://labs.shashkist.com"
LAB, SHORT_LAB = "smoke-tasks", "smoke-soak"
MANAGER = "docker exec lf-manager python -m app.cli"


def parse_free_available(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("Mem:"):
            return int(line.split()[6])
    raise ValueError("no Mem: row")


def parse_df_available(text: str) -> int:
    return int(text.strip().splitlines()[1].split()[3])


def run_soak(
    api, host, *, sessions: int = 30, clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep, wait_s: float = 420.0, poll_s: float = 10.0,
    tolerance: int = 50_000_000, out: Callable[[str], None] = print,
    err: Callable[[str], None] | None = None,
) -> int:
    def fail(line: str) -> None:
        failures.append(line)
        (err or (lambda s: print(s, file=sys.stderr)))(line)

    def wait_for(done: Callable[[], bool]) -> bool:
        deadline = clock() + wait_s
        while not done():
            if clock() >= deadline:
                return False
            sleep(poll_s)
        return True

    failures: list[str] = []
    counts: dict[str, int] = {}
    mem0, disk0 = host.memory_available(), host.disk_available()
    for i in range(sessions):
        expected, lab = "student", LAB
        if i == 0:
            expected = "heartbeat"
        elif i == 1:
            expected, lab = "time_limit", SHORT_LAB
        n = f"session {i + 1} ({lab})"
        try:
            sid = api.start(lab)
        except Exception as exc:  # noqa: BLE001
            fail(f"{n}: start failed ({type(exc).__name__})")
            continue
        try:
            if expected == "student":
                api.heartbeat(sid)
                api.end(sid)
            elif not wait_for(lambda s=sid: api.end_reason(s) is not None):
                fail(f"{n}: not ended with {expected} within {wait_s:g} s")
            reason = api.end_reason(sid)
        except Exception as exc:  # noqa: BLE001
            fail(f"{n}: {type(exc).__name__}")
            reason = None
        if reason is None:  # a session must never stay live: the next start would be refused
            try:
                api.end(sid)
            except Exception:  # noqa: BLE001
                pass
        elif reason == expected:
            counts[reason] = counts.get(reason, 0) + 1
        else:
            fail(f"{n}: ended with {reason}, expected {expected}")
    wait_for(lambda: not host.lab_containers() and not host.lab_volumes())
    for name in host.lab_containers():
        fail(f"lab container left: {name}")
    for name in host.lab_volumes():
        fail(f"orphan lab volume left: {name}")
    mem_d, disk_d = host.memory_available() - mem0, host.disk_available() - disk0
    if mem_d < -tolerance:
        fail(f"available memory dropped by {-mem_d} bytes")
    if disk_d < -tolerance:
        fail(f"available disk dropped by {-disk_d} bytes")
    kinds = ("student", "heartbeat", "time_limit")
    ends = "".join(f" {k}={counts[k]}" for k in kinds if k in counts)
    out(f"soak summary: sessions={sessions}{ends} memory_delta={mem_d} disk_delta={disk_d}")
    return 1 if failures else 0


class PublicApi:
    """The public API as the smoke student, plus the end reason read from the manager's database."""

    def __init__(self, base: str, cookie: str, host: "SshHost") -> None:
        self.base, self.cookie, self.host = base, cookie, host

    def start(self, lab_id: str) -> str:
        return start(self.base, self.cookie, lab_id)

    def heartbeat(self, sid: str) -> None:
        status, _, _ = request(self.base, "POST", f"/api/sessions/{sid}/heartbeat", self.cookie)
        if status != 204:
            raise Fail(f"heartbeat failed (http {status})")

    def end(self, sid: str) -> None:
        reason = end_session(self.base, sid, self.cookie)
        if reason is not None:
            raise Fail(reason)

    def end_reason(self, sid: str) -> str | None:
        return self.host.sh(f"{MANAGER} session-end {sid}").strip() or None


class SshHost:
    """Read-only reads over the smoke ssh config."""

    def __init__(self, config: str) -> None:
        self.config = config

    def sh(self, command: str) -> str:
        res = subprocess.run(
            ["ssh", "-F", self.config, "smoke", command],
            capture_output=True, text=True, timeout=60, check=True,
        )
        return res.stdout

    def memory_available(self) -> int:
        return parse_free_available(self.sh("free -b"))

    def disk_available(self) -> int:
        return parse_df_available(self.sh("df -B1 -P /"))

    def lab_containers(self) -> list[str]:
        out = self.sh("docker ps -a --filter name=lab- --format '{{.Names}}'")
        return [n for n in out.split() if n.startswith("lab-")]

    def lab_volumes(self) -> list[str]:
        live = set(self.sh(f"{MANAGER} live-sessions").split())
        names = self.sh("docker volume ls -q --filter name=lab-").split()
        found = [n for n in names if n.startswith(("lab-", "lf-lab-"))]
        return [n for n in found if n.rsplit("lab-", 1)[1] not in live]


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    student, password = os.environ.get("SMOKE_ID"), os.environ.get("SMOKE_PASSWORD")
    tmp = os.environ.get("RUNNER_TEMP", ".")
    config = os.environ.get("SSH_CONFIG") or os.path.join(tmp, "ssh_config")
    if not student or not password:
        print("soak failed: SMOKE_ID and SMOKE_PASSWORD are required", file=sys.stderr)
        return 1
    host = SshHost(config)
    try:
        api = PublicApi(base, login(base, student, password), host)
        return run_soak(api, host)
    except (Fail, subprocess.SubprocessError, ValueError, OSError) as exc:
        print(f"soak failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
