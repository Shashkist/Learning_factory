"""Acceptance tests for issue #652 (D26, D179, slice 2 of #47): sudo labs run on `runsc-suid`.

Spec: section 5 (the student shell) and section 6. Slice 1 (#651) registered a second gVisor
runtime `runsc-suid` (runsc plus `--allow-suid`); here the manager uses it for `sudo: true` labs
only. Every other lab stays on `runsc` with `no-new-privileges`, so `student` cannot become root.

Interface the implementation must follow (the exam pins it):

  * `app.runtime.container_spec(lab, session_id)` keeps its signature and stays pure (no Docker
    call). `spec["HostConfig"]["Runtime"]` is `os.environ.get("LF_SUDO_RUNTIME") or "runsc-suid"`
    for a lab with `sudo: true`, and `"runsc"` for every other lab. Nothing else in the body
    changes: same top-level keys, same HostConfig keys and values as before this issue.
  * The environment variable is `LF_SUDO_RUNTIME`; its default is `runsc-suid`.
  * The degraded fallback lives in `DockerRuntime.start(spec)`: before it posts
    `/containers/create` it reads the loaded runtimes (the `Runtimes` mapping of the Engine API
    `GET /info`, the same data as `docker info`). If the spec's `HostConfig.Runtime` is not
    `runsc` and is not a key of `Runtimes`, the create body is sent with `HostConfig.Runtime`
    set to `runsc` and nothing else changed, and the manager logs exactly ONE line on the
    `lab-manager` logger (level INFO or higher) naming the missing runtime and `runsc`. Start
    does not fail. When the runtime is loaded, or the lab is not a sudo lab, there is no such
    line and the body is sent as given. The body never carries `runc`, nor an empty Runtime.
  * The exam reaches the Engine API through `LF_DOCKER_HOST=tcp://host:port` (the existing test
    seam of `DockerRuntime`), with a fake daemon that answers `GET .../info`,
    `POST .../containers/create` and `POST .../containers/<name>/start`. A fresh
    `DockerRuntime()` is used per start, so a cache of the runtimes list inside one instance is
    allowed.

Unit cases run without Docker. Live cases are marked `docker` and run in the CI `docker` job,
where #651 registered `runsc-suid`. Student data is fake.
"""
import asyncio
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lab_image
import pytest

LAB_YAML = """id: {lab_id}
title:
  en: Lab
  he: מעבדה
description:
  en: Fake lab.
  he: מעבדה מזויפת.
image: term
time_limit_min: 15
status: published
module: 1
lesson: 5
{sudo_line}tasks:
  - id: q1
    kind: quiz
    question:
      en: Pick one.
      he: בחר אחד.
    options:
      - en: a
        he: א
      - en: b
        he: ב
    correct: 0
"""

SUDO_LINES = {"suid": "sudo: true\n", "plain": "sudo: false\n", "omitted": ""}
GVISOR = {"runsc", "runsc-suid"}


def _lab(tmp_path, kind: str):
    from app.content import load_labs

    root = tmp_path / "content"
    d = root / "test" / kind
    d.mkdir(parents=True)
    (d / "lab.yaml").write_text(
        LAB_YAML.format(lab_id=kind, sudo_line=SUDO_LINES[kind]), encoding="utf-8"
    )
    res = load_labs(root)
    assert kind in res.labs, res.errors
    return res.labs[kind]


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("LF_SUDO_RUNTIME", "LF_LAB_NETWORK", "LF_LAB_TERM_IMAGE", "LF_LAB_CODE_IMAGE"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _spec(tmp_path, kind: str):
    from app.runtime import container_spec

    return container_spec(_lab(tmp_path, kind), str(uuid.uuid4()))


# ---------------------------------------------------------------- AC1: the runtime choice


def test_ac1_a_sudo_lab_gets_runsc_suid_by_default(tmp_path, clean_env):
    assert _spec(tmp_path, "suid")["HostConfig"]["Runtime"] == "runsc-suid"


@pytest.mark.parametrize("kind", ["plain", "omitted"])
def test_ac1_every_other_lab_stays_on_runsc(tmp_path, clean_env, kind):
    assert _spec(tmp_path, kind)["HostConfig"]["Runtime"] == "runsc"


def test_ac1_lf_sudo_runtime_overrides_the_runtime_of_sudo_labs_only(tmp_path, clean_env):
    clean_env.setenv("LF_SUDO_RUNTIME", "runsc-suid-test")
    assert _spec(tmp_path, "suid")["HostConfig"]["Runtime"] == "runsc-suid-test"
    assert _spec(tmp_path, "plain")["HostConfig"]["Runtime"] == "runsc"
    assert _spec(tmp_path, "omitted")["HostConfig"]["Runtime"] == "runsc"


def test_ac1_nothing_else_in_the_create_body_changes(tmp_path, clean_env):
    suid = _spec(tmp_path, "suid")
    plain = _spec(tmp_path, "plain")
    assert set(suid) == set(plain) == {"name", "Image", "Env", "Labels", "HostConfig"}
    expected_keys = {
        "Runtime", "NetworkMode", "Memory", "MemorySwap", "NanoCpus", "PidsLimit", "CapDrop",
        "CapAdd", "SecurityOpt", "ShmSize", "LogConfig", "Tmpfs",
    }
    for spec in (suid, plain):
        assert set(spec["HostConfig"]) == expected_keys
    # the sudo grant of before this issue is unchanged
    s, p = suid["HostConfig"], plain["HostConfig"]
    assert "LF_SUDO=1" in suid["Env"] and "LF_SUDO=1" not in plain["Env"]
    assert s["CapAdd"] == ["SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE", "FOWNER"]
    assert p["CapAdd"] == ["SETUID", "SETGID"]
    assert s["SecurityOpt"] == []
    assert p["SecurityOpt"] == ["no-new-privileges"]
    for key in ("NetworkMode", "Memory", "MemorySwap", "NanoCpus", "PidsLimit", "CapDrop",
                "ShmSize", "LogConfig", "Tmpfs"):
        assert s[key] == p[key], key
    assert s["CapDrop"] == ["ALL"]
    assert s["NetworkMode"] == "lf-labs"
    assert suid["Image"] == plain["Image"]


def test_ac1_the_runtime_is_never_runc(tmp_path, clean_env):
    for kind in SUDO_LINES:
        assert _spec(tmp_path, kind)["HostConfig"]["Runtime"] != "runc"


# ---------------------------------------------------------------- AC2: the degraded fallback


class FakeDocker:
    """A daemon that lists `runtimes`, records every create body and accepts create and start."""

    def __init__(self, runtimes: list[str]):
        self.runtimes = runtimes
        self.created: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the test output quiet
                pass

            def _send(self, code: int, body: dict | None = None):
                raw = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path.endswith("/info"):
                    self._send(200, {"Runtimes": {name: {"path": name} for name in outer.runtimes}})
                else:
                    self._send(404, {"message": "not found"})

            def do_POST(self):
                path = self.path.split("?")[0]
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if path.endswith("/containers/create"):
                    outer.created.append(json.loads(body or b"{}"))
                    self._send(201, {"Id": "abc123"})
                elif path.endswith("/start"):
                    self._send(204)
                else:
                    self._send(404, {"message": "not found"})

            def do_DELETE(self):
                self._send(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _start(monkeypatch, tmp_path, caplog, kind: str, runtimes: list[str]):
    """Start a lab of `kind` on a fake daemon; return (spec given, body created, log records)."""
    from app.runtime import DockerRuntime

    spec = _spec(tmp_path, kind)
    given = json.loads(json.dumps(spec))
    with FakeDocker(runtimes) as daemon:
        monkeypatch.setenv("LF_DOCKER_HOST", daemon.address)
        with caplog.at_level(logging.DEBUG, logger="lab-manager"):
            caplog.clear()
            name = asyncio.run(DockerRuntime().start(spec))
        records = [
            r for r in caplog.records if r.name == "lab-manager" and r.levelno >= logging.INFO
        ]
        assert len(daemon.created) == 1, daemon.created
        assert name == given["name"]
        return given, daemon.created[0], records


def _same_but_runtime(given: dict, body: dict):
    want = json.loads(json.dumps(given))
    want.pop("name")  # the name travels as a query parameter
    want["HostConfig"].pop("Runtime")
    got = json.loads(json.dumps(body))
    got["HostConfig"].pop("Runtime")
    assert got == want, "the fallback must change the runtime and nothing else"


def test_ac2_a_sudo_lab_falls_back_to_runsc_with_one_log_line(tmp_path, caplog, clean_env):
    given, body, records = _start(clean_env, tmp_path, caplog, "suid", ["runc", "runsc"])
    assert given["HostConfig"]["Runtime"] == "runsc-suid"
    assert body["HostConfig"]["Runtime"] == "runsc"
    _same_but_runtime(given, body)
    assert len(records) == 1, [r.getMessage() for r in records]
    msg = records[0].getMessage()
    assert "runsc-suid" in msg and "runsc" in msg, msg


def test_ac2_the_fallback_follows_lf_sudo_runtime(tmp_path, caplog, clean_env):
    clean_env.setenv("LF_SUDO_RUNTIME", "runsc-suid-test")
    given, body, records = _start(clean_env, tmp_path, caplog, "suid", ["runc", "runsc"])
    assert body["HostConfig"]["Runtime"] == "runsc"
    _same_but_runtime(given, body)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert "runsc-suid-test" in records[0].getMessage()


def test_ac2_never_runc_even_when_no_gvisor_runtime_is_listed(tmp_path, caplog, clean_env):
    _, body, _ = _start(clean_env, tmp_path, caplog, "suid", ["runc"])
    assert body["HostConfig"]["Runtime"] == "runsc"


def test_ac2_a_loaded_sudo_runtime_is_used_with_no_log_line(tmp_path, caplog, clean_env):
    given, body, records = _start(
        clean_env, tmp_path, caplog, "suid", ["runc", "runsc", "runsc-suid"]
    )
    assert body["HostConfig"]["Runtime"] == "runsc-suid"
    _same_but_runtime(given, body)
    assert records == [], [r.getMessage() for r in records]


def test_ac2_a_loaded_override_runtime_is_used_with_no_log_line(tmp_path, caplog, clean_env):
    clean_env.setenv("LF_SUDO_RUNTIME", "runsc-suid-test")
    _, body, records = _start(
        clean_env, tmp_path, caplog, "suid", ["runc", "runsc", "runsc-suid-test"]
    )
    assert body["HostConfig"]["Runtime"] == "runsc-suid-test"
    assert records == [], [r.getMessage() for r in records]


@pytest.mark.parametrize("kind", ["plain", "omitted"])
def test_ac2_a_normal_lab_stays_on_runsc_with_no_log_line(tmp_path, caplog, clean_env, kind):
    # the sudo runtime is missing, which is no business of a normal lab
    given, body, records = _start(clean_env, tmp_path, caplog, kind, ["runc", "runsc"])
    assert body["HostConfig"]["Runtime"] == "runsc"
    _same_but_runtime(given, body)
    assert records == [], [r.getMessage() for r in records]


# ---------------------------------------------------------------- live: a real gVisor container


def _docker(*args: str, timeout: int = 300, check: bool = True):
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stdout}{res.stderr}"
    return res


@pytest.fixture(scope="module")
def image() -> str:
    return lab_image.ensure_lab_image()


@pytest.fixture(scope="module")
def network():
    name = os.environ.get("LF_LAB_NETWORK", "lf-labs")
    existed = _docker("network", "inspect", name, check=False).returncode == 0
    if not existed:
        _docker("network", "create", name)
    yield name
    if not existed:
        _docker("network", "rm", name, check=False)


@pytest.fixture(scope="module")
def started(network, image, tmp_path_factory):
    """Start a lab of a kind through DockerRuntime.start(container_spec(...)), as production does;
    containers are removed at the end of the module."""
    from app.runtime import DockerRuntime, container_spec

    mp = pytest.MonkeyPatch()
    mp.setenv("LF_LAB_TERM_IMAGE", image)
    mp.setenv("LF_LAB_NETWORK", network)
    mp.delenv("LF_DOCKER_HOST", raising=False)
    mp.delenv("LF_SUDO_RUNTIME", raising=False)
    names: list[str] = []
    loop = asyncio.new_event_loop()
    runtime = DockerRuntime()

    def start(kind: str) -> str:
        spec = container_spec(_lab(tmp_path_factory.mktemp(kind), kind), str(uuid.uuid4()))
        name = str(loop.run_until_complete(runtime.start(spec)))
        names.append(name)
        loop.run_until_complete(runtime.wait_ready(name, 90))
        deadline = time.time() + 60
        while time.time() < deadline:  # the entrypoint copies the skel into the empty tmpfs home
            probe = ["exec", name, "test", "-f", "/home/student/.bashrc"]
            if _docker(*probe, check=False).returncode == 0:
                return name
            time.sleep(0.5)
        raise AssertionError("/home/student/.bashrc never appeared")

    yield start
    for name in names:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    loop.close()
    mp.undo()


@pytest.fixture(scope="module")
def sudo_box(started):
    return started("suid")


@pytest.fixture(scope="module")
def normal_box(started):
    return started("plain")


def _as_student(box: str, script: str):
    return _docker(
        "exec", "-u", "student", "-w", "/home/student", box, "sh", "-c", script, check=False
    )


def _runtime_of(box: str) -> str:
    return _docker("inspect", "-f", "{{.HostConfig.Runtime}}", box).stdout.strip()


def _runs_under_gvisor(box: str):
    host_kernel = _docker("version", "-f", "{{.Server.KernelVersion}}").stdout.strip()
    inside = _docker("exec", box, "uname", "-r").stdout.strip()
    assert inside != host_kernel, f"the container sees the host kernel {host_kernel}: not gVisor"


@pytest.mark.docker
def test_ac3_sudo_true_exits_0_for_student_in_a_sudo_lab(sudo_box):
    res = _as_student(sudo_box, "sudo -n true")
    assert res.returncode == 0, f"{res.stdout}{res.stderr}"


@pytest.mark.docker
def test_ac3_sudo_chown_root_works_for_student_in_a_sudo_lab(sudo_box):
    res = _as_student(
        sudo_box,
        "set -e; printf 'echo hi\\n' > ~/a.sh; sudo -n chown root ~/a.sh; stat -c %U ~/a.sh",
    )
    assert res.returncode == 0, f"{res.stdout}{res.stderr}"
    assert res.stdout.strip() == "root", res.stdout


@pytest.mark.docker
def test_ac4_sudo_id_fails_for_student_in_a_normal_lab(normal_box):
    res = _as_student(normal_box, "sudo -n id")
    assert res.returncode != 0, f"student can sudo in a normal lab: {res.stdout}"
    assert "uid=0" not in res.stdout, res.stdout


@pytest.mark.docker
def test_ac4_no_setuid_binary_raises_the_euid_of_student_in_a_normal_lab(normal_box):
    # D26 recipe: a setuid-root copy of perl (made by root, who owns it, so no CAP_CHOWN is
    # needed), run by uid 1001 with real, effective and saved ids 1001; it must stay 1001.
    made = _docker(
        "exec", "-u", "root", normal_box, "sh", "-c",
        'cp "$(command -v perl)" /tmp/suid-perl && chmod 4755 /tmp/suid-perl',
        check=False,
    )
    assert made.returncode == 0, f"{made.stdout}{made.stderr}"
    res = _docker(
        "exec", "-u", "root", normal_box, "sh", "-c",
        "setpriv --reuid=1001 --regid=1001 --clear-groups /tmp/suid-perl -e 'print $>'",
        check=False,
    )
    assert res.returncode == 0, f"{res.stdout}{res.stderr}"
    assert res.stdout.strip() == "1001", f"a setuid binary raised the euid to {res.stdout!r}"


@pytest.mark.docker
def test_ac4_the_image_setuid_binaries_leave_student_unprivileged_in_a_normal_lab(normal_box):
    res = _as_student(normal_box, "su -c id 2>&1 </dev/null; sudo -n id -u 2>&1; id -u")
    assert "uid=0" not in res.stdout, res.stdout
    assert res.stdout.strip().splitlines()[-1] == "1001", res.stdout


@pytest.mark.docker
def test_ac5_the_sudo_lab_runs_on_runsc_suid_under_gvisor_never_runc(sudo_box):
    runtime = _runtime_of(sudo_box)
    assert runtime in GVISOR and runtime != "runc", runtime
    assert runtime == "runsc-suid"
    _runs_under_gvisor(sudo_box)


@pytest.mark.docker
def test_ac5_the_normal_lab_runs_on_runsc_under_gvisor_never_runc(normal_box):
    runtime = _runtime_of(normal_box)
    assert runtime in GVISOR and runtime != "runc", runtime
    assert runtime == "runsc"
    _runs_under_gvisor(normal_box)
