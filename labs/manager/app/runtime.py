"""Lab containers: the Engine API create body (D24, D20) and a Docker runtime over the socket."""
import json
import os
import time
from typing import Any, Protocol

import httpx

from app.content import Lab

MB = 1024 * 1024
DEFAULT_DOCKER_HOST = "unix:///var/run/docker.sock"
_TIMEOUT = 60.0


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
            "LogConfig": {"Type": "none"},
            "Tmpfs": {
                "/var/log/lf": "size=16m,mode=0700",
                "/home/student": "size=128m,mode=0755,uid=1001,gid=1001,exec",
                "/tmp": "size=64m,mode=1777,exec",
                "/var/tmp": "size=32m,mode=1777",
                "/run/lock": "size=1m,mode=1777",
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


class DockerRuntime:
    """Docker Engine API over the unix socket named by LF_DOCKER_HOST."""

    def __init__(self) -> None:
        host = os.environ.get("LF_DOCKER_HOST") or DEFAULT_DOCKER_HOST
        self._socket = host.removeprefix("unix://")

    def _client(self) -> httpx.AsyncClient:
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
            running = item.get("State") == "running"
            found.append(ContainerInfo(name, running, labels["lf.session"], labels))
        return found
