"""Acceptance tests for issue #7, Docker part: DockerRuntime under runsc (CI `docker` job).

Drives `app.runtime.DockerRuntime` (D24) against a real Docker Engine: start from
`lf-lab-term` with the spec built by `container_spec`, check the flags with `docker inspect`,
check the student's shell and the size caps from inside, remove, and list. The image is
$LF_LAB_TERM_IMAGE if set, else built from labs/images/term. The lab network
($LF_LAB_NETWORK, default lf-labs) is created if missing and removed again only if this
module created it. Only containers named `lab-<uuid>` that this module started, and
`lf-exam-*` ones it created, are ever touched.

Choices beyond D24: `DockerRuntime()` takes no arguments (it reads LF_DOCKER_HOST); its
methods may be sync or async; list items unpack as (name, running, session id). There is no
Postgres in the docker job, so the janitor's sweep itself is covered in
test_issue_7_session_lifecycle.py; here the runtime must report what the sweep needs
(running flag, session id, stopped containers) and must remove idempotently.
"""
import asyncio
import inspect
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
IMAGE_DIR = REPO_ROOT / "labs" / "images" / "term"
TTYD_PORT = 7681
MB = 1024 * 1024

LAB_YAML = """id: dock
title:
  en: Docker lab
  he: מעבדת דוקר
description:
  en: Fake lab.
  he: מעבדה מזויפת.
image: term
time_limit_min: 15
status: published
module: 1
lesson: 5
tasks:
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

LAB_SUDO_YAML = LAB_YAML.replace("id: dock", "id: docksudo").replace(
    "image: term", "image: term\nsudo: true"
)


def docker(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


@pytest.fixture(scope="module")
def loop():
    lp = asyncio.new_event_loop()
    yield lp
    lp.close()


@pytest.fixture(scope="module")
def call(loop):
    def run(fn, *args):
        r = fn(*args)
        if inspect.isawaitable(r):
            r = loop.run_until_complete(r)
        return r

    return run


@pytest.fixture(scope="module")
def network():
    name = os.environ.get("LF_LAB_NETWORK", "lf-labs")
    existed = docker("network", "inspect", name, check=False).returncode == 0
    if not existed:
        docker("network", "create", name)
    yield name
    if not existed:
        docker("network", "rm", name, check=False)


@pytest.fixture(scope="module")
def image() -> str:
    env = os.environ.get("LF_LAB_TERM_IMAGE")
    if env:
        return env
    assert (IMAGE_DIR / "Dockerfile").is_file(), "labs/images/term/Dockerfile is missing"
    tag = "lf-lab-term:exam"
    docker("build", "-t", tag, str(IMAGE_DIR), timeout=1500)
    return tag


@pytest.fixture(scope="module")
def runtime(network, image, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("LF_LAB_TERM_IMAGE", image)
    mp.setenv("LF_LAB_NETWORK", network)
    mp.delenv("LF_DOCKER_HOST", raising=False)
    from app.runtime import DockerRuntime

    yield DockerRuntime()
    mp.undo()


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    from app.content import load_labs

    root = tmp_path_factory.mktemp("content")
    d = root / "test" / "dock"
    d.mkdir(parents=True)
    (d / "lab.yaml").write_text(LAB_YAML, encoding="utf-8")
    res = load_labs(root)
    assert "dock" in res.labs, res.errors
    return res.labs["dock"]


@pytest.fixture(scope="module")
def sudo_lab(tmp_path_factory):
    from app.content import load_labs

    root = tmp_path_factory.mktemp("content-sudo")
    d = root / "test" / "docksudo"
    d.mkdir(parents=True)
    (d / "lab.yaml").write_text(LAB_SUDO_YAML, encoding="utf-8")
    res = load_labs(root)
    assert "docksudo" in res.labs, res.errors
    return res.labs["docksudo"]


class Started:
    def __init__(self, name: str, session: str, spec: dict):
        self.name, self.session, self.spec = name, session, spec

    def inspect(self) -> dict:
        return json.loads(docker("inspect", self.name).stdout)[0]

    def exec(self, user: str, script: str, timeout: int = 90) -> subprocess.CompletedProcess:
        return docker("exec", "-u", user, self.name, "sh", "-c", script, check=False,
                      timeout=timeout)

    def ip(self, network: str) -> str:
        ip = self.inspect()["NetworkSettings"]["Networks"][network]["IPAddress"]
        assert ip, "container has no address on the lab network"
        return ip


def _start(runtime, lab, call, request) -> Started:
    from app.runtime import container_spec

    session = str(uuid.uuid4())
    spec = container_spec(lab, session)
    name = f"lab-{session}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    got = call(runtime.start, spec)
    assert str(got) == name
    return Started(name, session, spec)


def _ready(c: Started, wait: float = 60.0) -> None:
    """The entrypoint has copied /etc/skel into the empty tmpfs home."""
    deadline = time.time() + wait
    while time.time() < deadline:
        if c.exec("root", "test -f /home/student/.bashrc").returncode == 0:
            return
        time.sleep(0.5)
    raise AssertionError("/home/student/.bashrc never appeared (skel was not copied)")


@pytest.fixture(scope="module")
def sudo_box(runtime, sudo_lab, call, request) -> Started:
    c = _start(runtime, sudo_lab, call, request)
    _ready(c)
    deadline = time.time() + 30
    while time.time() < deadline:  # the entrypoint writes the sudoers file
        if c.exec("root", "test -f /etc/sudoers.d/student").returncode == 0:
            break
        time.sleep(0.5)
    return c


@pytest.fixture(scope="module")
def box(runtime, lab, call, request) -> Started:
    c = _start(runtime, lab, call, request)
    _ready(c)
    return c


def _field(item, key: str, index: int):
    if hasattr(item, key):
        return getattr(item, key)
    try:
        return item[key]
    except (KeyError, TypeError, IndexError):
        return item[index]


def _listed(runtime, call, name: str):
    for item in call(runtime.list_lab_containers):
        if _field(item, "name", 0) == name:
            return item
    return None


def _tmpfs_total_kb(c: Started, path: str) -> int:
    out = c.exec("root", f"df -Pk {path} | tail -n 1").stdout.split()
    assert len(out) >= 2, out
    return int(out[1])


# ---------------------------------------------------------------- run flags (docker inspect)


def test_container_is_running_under_runsc_on_the_lab_network(box, network):
    info = box.inspect()
    assert info["State"]["Running"] is True
    assert info["HostConfig"]["Runtime"] == "runsc"
    assert network in info["NetworkSettings"]["Networks"]
    assert info["Name"].lstrip("/") == box.name


def test_container_has_the_resource_limits(box):
    host = box.inspect()["HostConfig"]
    assert host["Memory"] in (256 * MB, 256 * 10**6)
    assert host["MemorySwap"] == host["Memory"]
    assert host["NanoCpus"] == 500_000_000
    assert host["PidsLimit"] == 256


def test_container_drops_caps_and_sets_no_new_privileges(box):
    host = box.inspect()["HostConfig"]
    assert [c.upper() for c in host["CapDrop"]] == ["ALL"]
    assert sorted(c.upper().removeprefix("CAP_") for c in host["CapAdd"]) == ["SETGID", "SETUID"]
    assert any(str(o).startswith("no-new-privileges") for o in host["SecurityOpt"] or [])


def test_container_log_driver_is_none(box):
    assert box.inspect()["HostConfig"]["LogConfig"]["Type"] == "none"


def test_container_has_no_volumes_or_mounts(box):
    info = box.inspect()
    kinds = {m["Type"] for m in info["Mounts"] or []}
    assert not kinds & {"bind", "volume"}, info["Mounts"]
    assert not info["HostConfig"].get("Binds")
    assert not info["Config"].get("Volumes")


def test_container_has_labels_and_base_path_env(box):
    cfg = box.inspect()["Config"]
    assert cfg["Labels"]["lf"] == "1"
    assert cfg["Labels"]["lf.session"] == box.session
    assert f"LF_BASE_PATH=/s/{box.session}/term/" in cfg["Env"]


def test_container_tmpfs_for_log_dir_and_home_are_declared(box):
    tmpfs = box.inspect()["HostConfig"]["Tmpfs"]
    assert "mode=0700" in tmpfs["/var/log/lf"] or "mode=700" in tmpfs["/var/log/lf"]
    assert "size=" in tmpfs["/var/log/lf"] and "size=" in tmpfs["/home/student"]


# ---------------------------------------------------------------- inside the container


def test_ttyd_answers_under_the_session_base_path(box, network):
    url = f"http://{box.ip(network)}:{TTYD_PORT}/s/{box.session}/term/"
    deadline = time.time() + 30
    last = "no attempt"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                assert r.status == 200
                return
        except urllib.error.HTTPError as e:
            raise AssertionError(f"{url} answered {e.code}") from e
        except Exception as e:  # noqa: BLE001 - connection refused while ttyd starts
            last = repr(e)
        time.sleep(0.5)
    raise AssertionError(f"{url} never answered: {last}")


def test_student_shell_works_and_home_has_skel_files(box):
    res = box.exec("root", "su - student -c 'id -un; ls -a ~'")
    assert res.returncode == 0, res.stderr
    lines = res.stdout.split()
    assert lines[0] == "student", res.stdout
    assert ".bashrc" in lines, res.stdout


def test_home_belongs_to_the_student_and_is_writable(box):
    res = box.exec("student", "echo hi > ~/hello.txt && cat ~/hello.txt && stat -c %U ~")
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == ["hi", "student"]


def test_home_size_is_capped_and_a_200mb_write_fails(box):
    res = box.exec("student", "dd if=/dev/zero of=/home/student/big bs=1M count=200 2>&1")
    assert res.returncode != 0, "writing 200 MB into the home must fail"
    size = int(box.exec("student", "stat -c %s /home/student/big").stdout.strip() or 0)
    assert size < 200 * MB
    assert _tmpfs_total_kb(box, "/home/student") * 1024 <= 160 * MB
    assert box.inspect()["State"]["Running"] is True  # the container survives the full disk
    box.exec("student", "rm -f /home/student/big")


def test_log_dir_is_a_small_root_only_tmpfs(box):
    assert box.exec("root", "stat -c %a /var/log/lf").stdout.strip() == "700"
    assert _tmpfs_total_kb(box, "/var/log/lf") * 1024 <= 64 * MB
    assert box.exec("student", "ls /var/log/lf").returncode != 0


# ---------------------------------------------------------------- runtime API


def test_list_lab_containers_reports_name_running_and_session(runtime, call, box):
    item = _listed(runtime, call, box.name)
    assert item is not None, "a started lab container is not listed"
    assert _field(item, "running", 1) is True
    assert str(_field(item, "session", 2)) == box.session


def test_list_lab_containers_ignores_containers_that_are_not_lab(runtime, call, image, request):
    name = f"lf-exam-{uuid.uuid4().hex[:10]}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    docker("create", "--name", name, "--label", "lf=1", image, "sleep", "60")
    names = [_field(i, "name", 0) for i in call(runtime.list_lab_containers)]
    assert name not in names
    assert all(str(n).startswith("lab-") for n in names), names


def test_a_stopped_lab_container_is_listed_as_not_running(runtime, lab, call, request):
    c = _start(runtime, lab, call, request)
    docker("stop", "-t", "1", c.name)
    item = _listed(runtime, call, c.name)
    assert item is not None, "a stopped lab container must still be listed (the sweep removes it)"
    assert _field(item, "running", 1) is False
    call(runtime.remove, c.name)
    assert _listed(runtime, call, c.name) is None


def test_remove_leaves_no_container_and_no_volume(runtime, lab, call, request):
    before = set(docker("volume", "ls", "-q").stdout.split())
    c = _start(runtime, lab, call, request)
    _ready(c)
    call(runtime.remove, c.name)
    assert docker("inspect", c.name, check=False).returncode != 0
    assert docker("ps", "-a", "-q", "--filter", f"name={c.name}").stdout.strip() == ""
    assert set(docker("volume", "ls", "-q").stdout.split()) == before
    assert _listed(runtime, call, c.name) is None


def test_remove_of_a_missing_container_does_not_raise(runtime, call):
    call(runtime.remove, f"lab-{uuid.uuid4()}")


def test_sweep_inputs_orphan_is_visible_and_removable(runtime, lab, call, request):
    """A lab-* container whose session is unknown to the manager is listed and removable."""
    c = _start(runtime, lab, call, request)
    live_sessions: set[str] = set()  # the manager knows of no live session
    orphans = [
        str(_field(i, "name", 0))
        for i in call(runtime.list_lab_containers)
        if str(_field(i, "session", 2)) not in live_sessions
    ]
    assert c.name in orphans
    for name in orphans:
        if name == c.name:  # never touch a container this test did not start
            call(runtime.remove, name)
    assert docker("inspect", c.name, check=False).returncode != 0


# ---------------------------------------------------------------- review round 1: disk, exec, sudo


def test_a_100mb_write_into_tmp_fails_and_the_container_survives(box):
    res = box.exec("student", "dd if=/dev/zero of=/tmp/big bs=1M count=100 2>&1")
    assert res.returncode != 0, "writing 100 MB into /tmp must fail"
    assert box.inspect()["State"]["Running"] is True
    box.exec("student", "rm -f /tmp/big")


def test_student_can_run_a_script_from_home(box):
    res = box.exec("student", "printf 'echo hi\\n' > ~/a.sh; chmod +x ~/a.sh; ~/a.sh")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "hi"


def test_student_can_run_a_script_from_tmp(box):
    res = box.exec("student", "printf 'echo hi\\n' > /tmp/a.sh; chmod +x /tmp/a.sh; /tmp/a.sh")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "hi"


# gVisor does not honour setuid-root binaries (evidence on #7, D26), so `sudo` cannot work
# in a runsc lab; a working sudo for `sudo: true` labs is a follow-up issue. What #7 still
# guarantees is the sudoers entry, written only for sudo labs.
def test_sudo_lab_gets_a_sudoers_entry_for_student(sudo_box):
    res = sudo_box.exec("root", "stat -c %a /etc/sudoers.d/student && cat /etc/sudoers.d/student")
    assert res.returncode == 0, res.stderr
    mode, *rest = res.stdout.split("\n", 1)
    assert mode.strip() == "440"
    assert "student" in "".join(rest) and "NOPASSWD" in "".join(rest)


def test_normal_lab_has_no_sudoers_entry(box):
    res = box.exec("root", "test -e /etc/sudoers.d/student")
    assert res.returncode != 0


def test_normal_lab_student_has_no_sudo(box):
    res = box.exec("root", "su - student -c 'sudo -n true'")
    assert res.returncode != 0


def test_list_lab_containers_ignores_a_lab_named_container_without_the_lf_label(
    runtime, call, image, request
):
    name = f"lab-{uuid.uuid4()}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    # lf=0 plus a session label: the name and `lf.session` match a lab container, so only the
    # `lf=1` filter can keep it out of the listing.
    docker(
        "run", "-d", "--name", name, "--label", "lf=0", "--label", f"lf.session={uuid.uuid4()}",
        "--entrypoint", "sleep", image, "60",
    )
    names = [_field(i, "name", 0) for i in call(runtime.list_lab_containers)]
    assert name not in names, "a container without lf=1 is not ours and must never be swept"
