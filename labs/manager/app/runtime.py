"""Lab containers: the Engine API create body (D24, D20) and a Docker runtime over the socket."""
import asyncio
import contextlib
import json
import os
import struct
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.content import Lab

MB = 1024 * 1024
DEFAULT_DOCKER_HOST = "unix:///var/run/docker.sock"
_TIMEOUT = 60.0
MAX_OUTPUT = 256 * 1024  # the exam reassembles 200 KiB, so not the 64 KiB asked for
TRUNCATED = b"\n[output truncated]\n"


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
            "Runtime": "runsc",
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


def exec_command(script: str) -> list[str]:
    """The shebang interpreter plus `-s` (read the program from stdin); `/bin/sh` without one."""
    first = script.split("\n", 1)[0]
    parts = first[2:].split() if first.startswith("#!") else []
    return [*(parts or ["/bin/sh"]), "-s"]


# Run as root by a second exec after a timeout (#117). The script's exec has the argv `<interp> -s`
# (the manager's own, always root); the kill finds it, adds its descendants, freezes the lot so
# nothing can fork or reparent, then SIGKILLs. Not `timeout -s KILL` (it kills only its child, and
# a script's background jobs would live on) and not the exec Pid (under gVisor it is a host pid).
KILL_SCRIPT = r"""
want="$1"
tree() {
  all=""
  for d in /proc/[0-9]*; do
    [ "$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)" = "$want" ] || continue
    [ "$(stat -c %u "$d" 2>/dev/null)" = 0 ] && all="$all ${d#/proc/}"
  done
  while :; do
    before="$all"
    for d in /proc/[0-9]*; do
      p=${d#/proc/}
      case " $all " in *" $p "*) continue;; esac
      pp=$(sed -n 's/^PPid:[[:space:]]*//p' "$d/status" 2>/dev/null)
      [ -n "$pp" ] || continue
      case " $all " in *" $pp "*) all="$all $p";; esac
    done
    [ "$before" = "$all" ] && break
  done
  echo $all
}
for _ in 1 2 3; do
  pids=$(tree)
  [ -n "$pids" ] || exit 0
  kill -STOP $pids 2>/dev/null
  pids=$(tree)
  kill -KILL $pids 2>/dev/null
done
"""


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
        create = {
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "User": "root",
            "Privileged": False,
            "WorkingDir": "/",
            "Env": [f"PATH={EXEC_PATH}", "HOME=/root"],
            "Cmd": exec_command(script),
        }
        out = bytearray()
        ids: list[str] = []
        try:
            async with asyncio.timeout(timeout) as deadline:
                return await self._run_exec(name, script, create, out, ids)
        except TimeoutError:
            if not deadline.expired():
                raise  # a timeout inside the exec (e.g. connecting) is a failure, not a slow script
            if ids:  # returns only once the script has stopped, so the session lock outlives it
                await self._kill(name, exec_command(script), ids[0])
            return ExecResult(None, out.decode("utf-8", "replace"), True)

    async def _kill(self, name: str, argv: list[str], exec_id: str) -> None:
        if argv[0].rsplit("/", 1)[-1] == "env":  # `env bash -s` replaces itself with `bash -s`
            argv = argv[1:]
        body = {
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "User": "root",
            "Privileged": False,
            "Env": [f"PATH={EXEC_PATH}"],
            "Cmd": ["/bin/sh", "-c", KILL_SCRIPT, "sh", " ".join(argv) + " "],
        }
        async with self._client() as c:
            resp = await c.post(f"/containers/{name}/exec", json=body)
            if resp.status_code != 201:
                raise RuntimeError(f"docker kill exec create failed: {resp.status_code}")
            await self._stream_exec(str(resp.json()["Id"]), "", bytearray())
            for _ in range(100):
                if not (await c.get(f"/exec/{exec_id}/json")).json().get("Running"):
                    return
                await asyncio.sleep(0.1)
        raise RuntimeError("a timed-out script is still running after the kill")

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
            resp = await c.post("/containers/create", params={"name": name}, json=body)
            if resp.status_code != 201:
                raise RuntimeError(f"docker create failed: {resp.status_code} {resp.text[:200]}")
            resp = await c.post(f"/containers/{name}/start")
            if resp.status_code not in (204, 304):
                await c.delete(f"/containers/{name}", params={"force": "true", "v": "true"})
                raise RuntimeError(f"docker start failed: {resp.status_code} {resp.text[:200]}")
        return name

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
