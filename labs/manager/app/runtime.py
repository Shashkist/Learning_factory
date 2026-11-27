"""Lab containers: the Engine API create body (D24, D20) and a Docker runtime over the socket."""
import asyncio
import contextlib
import json
import logging
import os
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.content import Lab, shebang_argv

log = logging.getLogger("lab-manager")

MB = 1024 * 1024
DEFAULT_DOCKER_HOST = "unix:///var/run/docker.sock"
_TIMEOUT = 60.0
KILL_BOUND_S = 5.0  # longest kill of a timed-out script
_KILLS: set[asyncio.Future[None]] = set()  # kills still running after their caller left
MAX_OUTPUT = 256 * 1024  # the exam reassembles 200 KiB, so not the 64 KiB asked for
TRUNCATED = b"\n[output truncated]\n"


class _StillRunning(Exception):
    """The kill exec ended and the script is still running."""


EXEC_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass
class ExecResult:
    exit_code: int | None
    output: str
    timed_out: bool = False


def memory_limit_mb(lab: Lab) -> int:
    return 768 if lab.image == "code" else 256


def container_name(session_id: Any) -> str:
    return f"lab-{session_id}"


def container_spec(lab: Lab, session_id: str) -> dict[str, Any]:
    """The Engine API create body plus a top-level `name` (the API takes it as a query)."""
    if lab.image == "code":
        image = os.environ.get("LF_LAB_CODE_IMAGE", "ghcr.io/shashkist/lf-lab-code:latest")
    else:
        image = os.environ.get("LF_LAB_TERM_IMAGE", "ghcr.io/shashkist/lf-lab-term:latest")
    memory = memory_limit_mb(lab) * MB
    # uid/gid 1001 is the `student` user of the lab images; a tmpfs home would be root-owned
    # otherwise, and root has no CAP_CHOWN here.
    env = [f"LF_BASE_PATH=/s/{session_id}/term/"] + (["LF_SUDO=1"] if lab.sudo else [])
    caps = ["SETUID", "SETGID"] + (["CHOWN", "DAC_OVERRIDE", "FOWNER"] if lab.sudo else [])
    expires = int(time.time()) + lab.time_limit_min * 60
    return {
        "name": container_name(session_id),
        "Image": image,
        "Env": env,
        "Labels": {"lf": "1", "lf.session": session_id, "lf.expires": str(expires)},
        "HostConfig": {
            "Runtime": (os.environ.get("LF_SUDO_RUNTIME") or "runsc-suid") if lab.sudo else "runsc",
            "NetworkMode": os.environ.get("LF_LAB_NETWORK", "lf-labs"),
            "Memory": memory,
            "MemorySwap": memory,
            "NanoCpus": 500_000_000,
            "PidsLimit": 256,
            "CapDrop": ["ALL"],
            "CapAdd": caps,
            "SecurityOpt": [] if lab.sudo else ["no-new-privileges"],
            "ShmSize": 64 * MB,
            "LogConfig": {"Type": "none"},
            "Tmpfs": {
                "/var/log/lf": "size=16m,mode=0700",
                "/home/student": "size=128m,mode=0755,uid=1001,gid=1001,exec",
                "/tmp": "size=64m,mode=1777,exec",
                "/var/tmp": "size=32m,mode=1777",
                "/run/lock": "size=1m,mode=1777",
                # hides the mail spool `useradd -m` leaves owned by student on the root fs
                "/var/mail": "size=1m,mode=0755",
                # the student's crontab spool is student-owned in the image; cap it (D193)
                "/var/spool/cron/crontabs": "size=1m,mode=0700,uid=1001,gid=1001",
            },
        },
    }


class ContainerInfo(tuple):  # type: ignore[type-arg]
    """(name, running, session id); also `.labels` and `.expires` (the lf.expires epoch or None)."""

    labels: dict[str, str]
    expires: int | None

    def __new__(
        cls, name: str, running: bool, session_id: str, labels: dict[str, str] | None = None
    ) -> "ContainerInfo":
        self = super().__new__(cls, (name, running, session_id))
        self.labels = dict(labels or {})
        raw = self.labels.get("lf.expires", "")
        self.expires = int(raw) if raw.isdigit() else None
        return self


class LabRuntime(Protocol):
    async def start(self, spec: dict[str, Any]) -> str: ...

    async def remove(self, name: str) -> None: ...

    async def list_lab_containers(self) -> list[ContainerInfo]: ...

    async def exec_script(self, name: str, script: str, timeout: float) -> ExecResult: ...

    async def wait_ready(self, name: str, timeout: float) -> None: ...


def exec_command(script: str) -> list[str]:
    """The shebang interpreter plus `-s` (read the program from stdin); `/bin/sh` without one."""
    return [*shebang_argv(script), "-s"]


# Run as root by a second exec after a timeout (#117, #306). The script's exec carries a per-exec
# unique `LF_EXEC_ID` in its environment (the manager's own interpreter, always root). Root can read
# only root processes' environment (it has SETUID/SETGID, not CAP_DAC_OVERRIDE/CAP_KILL), so the
# marker finds the root processes of this exec; student processes are found only as descendants by
# PPid. Orphans (reparented) and children that clear their environment with `env -i` are missed by
# the scan. So the kill first freezes (STOP) and, when re-collecting, kills the student groups
# before the root parents, which keeps the tree from orphaning itself. Another exec is never a
# target (it has another marker). Non-root pids are signalled as their owner (no CAP_KILL) by
# background children; root is stopped at once and killed after at most four short polls of those
# children, so a child the student stops cannot hold the killer; the killer is root and runs under
# `timeout -s KILL` (see `kill_command`), so it ends by its own deadline and never outlives the
# call, except for the latency between the manager's clock and the `timeout` start inside the
# container (the margin in `_kill_bounded`). A stopped child that resumes later signals only the
# pids it was given, as the student (D56). Zombies are not targets.
# Not `timeout -s KILL` around the script (a script's background jobs would live on)
# and not the exec Pid (under gVisor it is a host pid).
KILL_SCRIPT = r"""
id="$1"
collect() {
  roots=$(grep -l -a -z -x "LF_EXEC_ID=$id" /proc/[0-9]*/environ 2>/dev/null |
    sed 's#^/proc/\([0-9]*\)/environ$#\1#' | tr '\n' ' ')
  [ -n "$roots" ] || return 0
  ps -eo pid=,ppid=,ruid=,rgid=,stat= | awk -v roots="$roots" '
    BEGIN { n = split(roots, r, " "); for (i = 1; i <= n; i++) mark[r[i]] = 1 }
    { pid=$1; pp[pid]=$2; uid[pid]=$3; gid[pid]=$4; st[pid]=$5 }
    END {
      do { n = 0
        for (p in pp) if (!(p in mark) && (pp[p] in mark)) { mark[p] = 1; n = 1 }
      } while (n)
      for (p in mark) if ((p in pp) && st[p] !~ /^Z/) {
        k = uid[p] " " gid[p]; by[k] = by[k] " " p
      }
      for (k in by) print k by[k]
    }'
}
send() {
  printf '%s
' "$2" | {
    kids=""; rootpids=""
    while read -r u g pids; do
      [ -n "$pids" ] || continue
      if [ "$u" = 0 ]; then rootpids="$rootpids $pids"
      else
        setpriv --reuid="$u" --regid="$g" --clear-groups kill -"$1" $pids \
          </dev/null >/dev/null 2>&1 &
        kids="$kids $!"
      fi
    done
    # Stopping root orphans nothing: STOP root at once. Before a root KILL, give the student
    # groups' kill children a few rounds (one `ps` each) so the tree does not orphan itself.
    if [ "$1" = KILL ] && [ -n "$kids" ]; then
      i=0
      while [ "$i" -lt 4 ]; do
        ps -o stat= -p "$(echo $kids | tr ' ' ',')" 2>/dev/null | grep -qv '^ *Z' || break
        sleep 0.05; i=$((i + 1))
      done
    fi
    [ -z "$rootpids" ] || kill -"$1" $rootpids 2>/dev/null
  }
}
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  a=$(collect)
  [ -n "$a" ] || exit 0
  send STOP "$a"
  sleep 0.1
  b=$(collect)
  send KILL "$a"
  send KILL "$b"
  sleep 0.2
done
"""


def kill_command(marker: str, deadline_s: float) -> list[str]:
    """The killer's argv: a root `sh` that execs `timeout -s KILL <n> sh -c KILL_SCRIPT` (timeout
    signals its whole group, so the pipeline subshells go too). The command is not itself a
    `timeout ...` wrapper: it stays a second exec that runs a kill program (#117)."""
    wrapper = 'exec timeout -s KILL "$1" /bin/sh -c "$2" sh "$3"'
    return ["/bin/sh", "-c", wrapper, "sh", f"{deadline_s:.1f}", KILL_SCRIPT, marker]


class DockerRuntime:
    """Docker Engine API over the unix socket named by LF_DOCKER_HOST."""

    def __init__(self) -> None:
        host = os.environ.get("LF_DOCKER_HOST") or DEFAULT_DOCKER_HOST
        self._tcp = host.removeprefix("tcp://") if host.startswith("tcp://") else None
        self._socket = "" if self._tcp else host.removeprefix("unix://")

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._tcp is not None:
            host, _, port = self._tcp.rpartition(":")
            return await asyncio.open_connection(host, int(port))
        # not defined on win32, where mypy runs on the laptop
        return await asyncio.open_unix_connection(  # type: ignore[attr-defined,no-any-return,unused-ignore]
            self._socket
        )

    async def _stream_exec(self, exec_id: str, script: str, out: bytearray) -> None:
        """Hijacked exec start: write the script, close the write side, demultiplex the output."""
        reader, writer = await self._open()
        try:
            body = b'{"Detach":false,"Tty":false}'
            writer.write(
                f"POST /exec/{exec_id}/start HTTP/1.1\r\nHost: docker\r\n"
                "Content-Type: application/json\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
                f"Content-Length: {len(body)}\r\n\r\n".encode() + body
            )
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            if head.split(" ", 2)[1] not in ("101", "200"):
                raise RuntimeError(f"docker exec start failed: {head.splitlines()[0][:100]}")
            writer.write(script.encode())
            await writer.drain()
            writer.write_eof()
            truncated = False
            while True:
                try:
                    header = await reader.readexactly(8)
                except asyncio.IncompleteReadError:
                    break
                left = struct.unpack(">xxxxI", header)[0]
                while left:
                    try:
                        chunk = await reader.readexactly(min(left, 65536))
                    except asyncio.IncompleteReadError as exc:
                        chunk, left = exc.partial, 0
                        eof = True
                    else:
                        left -= len(chunk)
                        eof = False
                    room = MAX_OUTPUT - len(out)
                    if len(chunk) > room:  # keep the first MAX_OUTPUT bytes, drain the rest
                        truncated = True
                    out += chunk[: max(room, 0)]
                    if eof:
                        left = -1
                        break
                if left < 0:
                    break
            if truncated:
                out += TRUNCATED
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def exec_script(self, name: str, script: str, timeout: float) -> ExecResult:
        """Run `script` as root in container `name`, streamed on stdin (D45); raises on failures."""
        marker = uuid.uuid4().hex  # names this exec to its killer (#306)
        create = {
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "User": "root",
            "Privileged": False,
            "WorkingDir": "/",
            "Env": [f"PATH={EXEC_PATH}", "HOME=/root", f"LF_EXEC_ID={marker}"],
            "Cmd": exec_command(script),
        }
        out = bytearray()
        ids: list[str] = []
        try:
            async with asyncio.timeout(timeout) as deadline:
                return await self._run_exec(name, script, create, out, ids)
        except Exception as exc:
            timed_out = isinstance(exc, TimeoutError) and deadline.expired()
            if ids:  # returns only once the script has stopped, so the session lock outlives it
                await self._kill_shielded(name, marker, ids[0], timeout)
            if not timed_out:
                raise  # a failure (even a timeout while connecting), not a slow script
            return ExecResult(None, out.decode("utf-8", "replace"), True)
        except asyncio.CancelledError:
            if ids:  # a cancel (shutdown, outer timeout) must not leave the script running
                await self._kill_shielded(name, marker, ids[0], timeout)
            raise

    async def _kill_shielded(self, name: str, marker: str, exec_id: str, timeout: float) -> None:
        """The bounded kill, which a cancel of the caller does not cut."""
        kill = asyncio.ensure_future(self._kill_bounded(name, marker, exec_id, timeout))
        # a second cancel may leave at once while the kill goes on: the loop keeps only a weak
        # reference to a task, so hold it here (the D98 marker keeps the next exec safe)
        _KILLS.add(kill)
        kill.add_done_callback(_KILLS.discard)
        try:
            await asyncio.shield(kill)  # the kill bounds itself by KILL_BOUND_S
        except asyncio.CancelledError:  # a cancel landed during the kill: let it finish, then go on
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(kill)  # a second cancel leaves at once
            raise

    async def _kill_bounded(self, name: str, marker: str, exec_id: str, timeout: float) -> None:
        """The kill of a script that must not outlive its call; never raises, logs one line."""
        loop = asyncio.get_running_loop()
        bound = min(KILL_BOUND_S, max(3.0, timeout * 0.2))
        try:
            async with asyncio.timeout(bound):
                give_up_at = loop.time() + bound - 0.3
                await self._kill(name, marker, exec_id, give_up_at, max(1.0, bound - 1.5))
        except TimeoutError:
            log.error("kill of the script hung")
        except _StillRunning:
            log.error("kill left the script running")
        except Exception as exc:  # the lock is released anyway; class only, no data
            log.error("killing the script failed (%s)", type(exc).__name__)

    async def _kill(
        self, name: str, marker: str, exec_id: str, give_up_at: float, deadline_s: float
    ) -> None:
        body = {
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "User": "root",
            "Privileged": False,
            "Env": [f"PATH={EXEC_PATH}"],
            "Cmd": kill_command(marker, deadline_s),
        }
        async with self._client() as c:
            resp = await c.post(f"/containers/{name}/exec", json=body)
            if resp.status_code != 201:
                raise RuntimeError(f"docker kill exec create failed: {resp.status_code}")
            await self._stream_exec(str(resp.json()["Id"]), "", bytearray())
            loop = asyncio.get_running_loop()
            while True:
                if not (await c.get(f"/exec/{exec_id}/json")).json().get("Running"):
                    return
                if loop.time() >= give_up_at:
                    raise _StillRunning
                await asyncio.sleep(0.1)

    async def _run_exec(
        self, name: str, script: str, create: dict[str, Any], out: bytearray, ids: list[str]
    ) -> ExecResult:
        async with self._client() as c:
            resp = await c.post(f"/containers/{name}/exec", json=create)
            if resp.status_code != 201:
                raise RuntimeError(f"docker exec create failed: {resp.status_code}")
            exec_id = str(resp.json()["Id"])
            ids.append(exec_id)
            await self._stream_exec(exec_id, script, out)
            for _ in range(50):  # the exit code lands just after the stream closes
                info = (await c.get(f"/exec/{exec_id}/json")).json()
                if not info.get("Running") and info.get("ExitCode") is not None:
                    return ExecResult(int(info["ExitCode"]), out.decode("utf-8", "replace"))
                await asyncio.sleep(0.1)
        raise RuntimeError("docker exec did not report an exit code")

    def _client(self) -> httpx.AsyncClient:
        if self._tcp is not None:  # the exam's test seam
            return httpx.AsyncClient(base_url=f"http://{self._tcp}", timeout=_TIMEOUT)
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self._socket),
            base_url="http://docker",
            timeout=_TIMEOUT,
        )

    async def start(self, spec: dict[str, Any]) -> str:
        body = dict(spec)
        name = str(body.pop("name"))
        async with self._client() as c:
            host_config = dict(body.get("HostConfig") or {})
            wanted = host_config.get("Runtime")
            if wanted and wanted != "runsc":
                info = await c.get("/info")
                loaded = info.json().get("Runtimes") or {} if info.status_code == 200 else None
                if loaded is not None and wanted not in loaded:
                    log.info("runtime %s is not loaded; creating the lab with runsc", wanted)
                    body["HostConfig"] = {**host_config, "Runtime": "runsc"}
            resp = await c.post("/containers/create", params={"name": name}, json=body)
            if resp.status_code != 201:
                raise RuntimeError(f"docker create failed: {resp.status_code} {resp.text[:200]}")
            resp = await c.post(f"/containers/{name}/start")
            if resp.status_code not in (204, 304):
                await c.delete(f"/containers/{name}", params={"force": "true", "v": "true"})
                raise RuntimeError(f"docker start failed: {resp.status_code} {resp.text[:200]}")
        return name

    async def _is_running(self, name: str) -> bool:
        try:
            async with self._client() as c:
                resp = await c.get(f"/containers/{name}/json")
        except httpx.HTTPError:
            return True  # the socket blinked; keep waiting, the timeout bounds it
        if resp.status_code == 404:
            return False
        if resp.status_code != 200:
            return True  # cannot tell; the timeout bounds the wait
        return bool((resp.json().get("State") or {}).get("Running"))

    async def wait_ready(self, name: str, timeout: float) -> None:
        """Return once the entrypoint has copied the skel and exec'd ttyd; TimeoutError if never.
        A container that is gone or stopped ends the wait at once (RuntimeError)."""
        async with asyncio.timeout(timeout):
            while True:
                try:
                    res = await self.exec_script(name, "pgrep -x ttyd >/dev/null", 5.0)
                except Exception:  # noqa: BLE001 - the container is still coming up
                    res = None
                if res is not None and res.exit_code == 0:
                    return
                if not await self._is_running(name):
                    raise RuntimeError("lab container is not running")
                await asyncio.sleep(0.2)

    async def remove(self, name: str) -> None:
        async with self._client() as c:
            resp = await c.delete(f"/containers/{name}", params={"force": "true", "v": "true"})
        if resp.status_code not in (204, 404):
            raise RuntimeError(f"docker remove failed: {resp.status_code} {resp.text[:200]}")

    async def list_lab_containers(self) -> list[ContainerInfo]:
        async with self._client() as c:
            filters = json.dumps({"label": ["lf=1"], "name": ["lab-"]})
            resp = await c.get("/containers/json", params={"all": "true", "filters": filters})
        if resp.status_code != 200:
            raise RuntimeError(f"docker list failed: {resp.status_code}")
        found: list[ContainerInfo] = []
        for item in resp.json():
            name = str(item["Names"][0]).lstrip("/")
            labels = item.get("Labels") or {}
            # the name filter matches substrings; only our own labelled containers count
            if not name.startswith("lab-") or not labels.get("lf.session"):
                continue
            # created/restarting/paused are alive for our purposes; only exited/dead are stopped
            running = item.get("State") not in ("exited", "dead")
            found.append(ContainerInfo(name, running, labels["lf.session"], labels))
        return found
