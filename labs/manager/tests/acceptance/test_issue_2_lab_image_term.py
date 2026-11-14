"""Acceptance tests for issue #2: lab image lf-lab-term (Ubuntu + ttyd) built and published.

Static tests run everywhere. Docker tests run in the CI `docker` job (gVisor runsc registered).
The image under test is $LF_LAB_TERM_IMAGE if set, else built from labs/images/term.
"""
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
IMAGE_DIR = REPO_ROOT / "labs" / "images" / "term"
DOCKERFILE = IMAGE_DIR / "Dockerfile"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TTYD_PORT = 7681
TOOLS = [
    "nano", "vim", "less", "tree", "curl", "dnsutils", "iputils-ping", "traceroute",
    "iproute2", "net-tools", "procps", "file", "man-db",
]


# ---------------------------------------------------------------- static: Dockerfile


def _dockerfile() -> str:
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_exists():
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"


def test_dockerfile_is_based_on_ubuntu_2404():
    froms = re.findall(r"^\s*FROM\s+(\S+)", _dockerfile(), flags=re.I | re.M)
    assert froms, "no FROM line"
    assert re.fullmatch(r"ubuntu:24\.04(@sha256:[0-9a-f]{64})?", froms[0]), froms[0]


def test_dockerfile_pins_a_concrete_ttyd_version():
    text = _dockerfile()
    assert "ttyd" in text.lower()
    assert not re.search(r"releases/latest", text, flags=re.I), "ttyd must not use releases/latest"
    assert not re.search(r"TTYD\w*\s*[= ]\s*\"?latest", text, flags=re.I), "ttyd must not be latest"
    pinned = re.search(r"ttyd\S*[=\s:/_-]+v?\d+\.\d+\.\d+", text, flags=re.I) or re.search(
        r"(ARG|ENV)\s+\w*TTYD\w*[= ]\s*\"?v?\d+\.\d+\.\d+", text, flags=re.I
    )
    assert pinned, "ttyd version is not pinned to a concrete x.y.z version"


@pytest.mark.parametrize("tool", TOOLS)
def test_dockerfile_installs_tool(tool):
    text = _dockerfile()
    assert re.search(rf"(?<![\w-]){re.escape(tool)}(?![\w-])", text), f"{tool} not installed"


def test_dockerfile_creates_student_user_and_log_dir():
    text = _dockerfile()
    assert "student" in text
    assert "/home/student" in text or "useradd" in text or "adduser" in text
    assert "/var/log/lf" in text


# ---------------------------------------------------------------- static: CI publishing


def _workflows() -> dict[str, dict]:
    yaml = pytest.importorskip("yaml")
    assert WORKFLOWS.is_dir(), f"{WORKFLOWS} is missing"
    out = {}
    for f in sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml"))):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            out[f.name] = data
    return out


def _term_job() -> tuple[str, dict, dict, str]:
    """Return (file, workflow, job, job text) for the job that builds labs/images/term."""
    for fname, wf in _workflows().items():
        for job in (wf.get("jobs") or {}).values():
            if "images/term" in str(job):
                return fname, wf, job, str(job)
    pytest.fail("no workflow job builds labs/images/term")


def test_ci_has_job_that_builds_term_image():
    _, _, _, text = _term_job()
    assert "build" in text.lower()
    assert "lf-lab-term" in text


def test_ci_builds_image_on_pull_requests():
    fname, wf, job, _ = _term_job()
    trig = wf.get("on", wf.get(True))
    if isinstance(trig, str):
        trig = {trig: None}
    elif isinstance(trig, list):
        trig = {t: None for t in trig}
    assert "pull_request" in (trig or {}), f"{fname} does not run on pull_request"
    cond = str(job.get("if", ""))
    assert "pull_request" not in cond or "!=" in cond or "||" in cond, (
        f"build job is restricted away from PRs: {cond}"
    )


def test_ci_pushes_image_only_on_main():
    _, _, _, text = _term_job()
    assert re.search(r"push", text, flags=re.I), "no push step found"
    assert "refs/heads/main" in text, "push is not conditioned on github.ref == refs/heads/main"
    assert "github.ref" in text


def test_ci_logs_in_to_ghcr_with_github_token():
    _, _, _, text = _term_job()
    assert "ghcr.io" in text
    assert "secrets.GITHUB_TOKEN" in text
    assert "login" in text.lower()


def test_ci_tags_image_with_sha_and_latest():
    _, _, _, text = _term_job()
    assert "ghcr.io/shashkist/lf-lab-term:" in text.lower()
    assert "github.sha" in text
    assert re.search(r"lf-lab-term:latest", text, flags=re.I), "no :latest tag"


def test_ci_job_has_packages_write_permission():
    _, _, job, _ = _term_job()
    perms = job.get("permissions")
    assert isinstance(perms, dict), "term image job must declare its own permissions"
    assert perms.get("packages") == "write"


# ---------------------------------------------------------------- docker helpers


def _docker(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


@pytest.fixture(scope="module")
def image() -> str:
    env = os.environ.get("LF_LAB_TERM_IMAGE")
    if env:
        return env
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing, cannot build the image"
    tag = "lf-lab-term:exam"
    _docker("build", "-t", tag, str(IMAGE_DIR), timeout=1500)
    return tag


def _start(image: str, request, base_path: str) -> str:
    name = f"lf-exam-{uuid.uuid4().hex[:10]}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    _docker(
        "run", "-d", "--name", name, "--runtime=runsc", "--log-driver", "none",
        "-e", f"LF_BASE_PATH={base_path}", image,
    )
    return name


def _ip(name: str) -> str:
    # Docker 28+ no longer fills the top-level NetworkSettings.IPAddress; read the
    # per-network address instead (the container is only on the default bridge).
    fmt = "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"
    ips = _docker("inspect", "-f", fmt, name).stdout.split()
    out = ips[0] if ips else ""
    assert out, "container has no IP on the default bridge"
    return out


def _get(url: str, wait: float = 25.0) -> int:
    deadline = time.time() + wait
    last = "no attempt"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:  # noqa: BLE001 - connection refused while ttyd starts
            last = repr(e)
        time.sleep(0.5)
    raise AssertionError(f"{url} never answered: {last}")


@pytest.fixture(scope="module")
def container(image, request) -> str:
    return _start(image, request, "/s/exam1/term")


def _exec(name: str, user: str, *cmd: str) -> subprocess.CompletedProcess:
    return _docker("exec", "-u", user, name, *cmd, check=False, timeout=60)


# ---------------------------------------------------------------- docker tests


@pytest.mark.docker
def test_ttyd_serves_200_under_base_path(container):
    assert _get(f"http://{_ip(container)}:{TTYD_PORT}/s/exam1/term/") == 200


@pytest.mark.docker
def test_base_path_is_configurable_by_env(image, request):
    name = _start(image, request, "/s/other77/term")
    assert _get(f"http://{_ip(name)}:{TTYD_PORT}/s/other77/term/") == 200


@pytest.mark.docker
def test_student_user_exists_with_home(container):
    res = _exec(container, "root", "getent", "passwd", "student")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().split(":")[5] == "/home/student"
    who = _exec(container, "student", "sh", "-c", "echo $HOME; id -un")
    assert who.stdout.split() == ["/home/student", "student"]


@pytest.mark.docker
def test_student_has_no_sudo(container):
    groups = _exec(container, "root", "id", "-nG", "student").stdout.split()
    assert "sudo" not in groups and "admin" not in groups, groups
    res = _exec(container, "student", "sh", "-c", "command -v sudo >/dev/null 2>&1 && sudo -n true")
    assert res.returncode != 0, "student can sudo without a password"


@pytest.mark.docker
def test_log_directory_is_root_only(container):
    assert _exec(container, "root", "test", "-d", "/var/log/lf").returncode == 0
    assert _exec(container, "student", "ls", "/var/log/lf").returncode != 0
    assert _exec(container, "student", "cat", "/var/log/lf/terminal.log").returncode != 0
    assert _exec(container, "student", "sh", "-c", ": > /var/log/lf/terminal.log").returncode != 0


@pytest.mark.docker
def test_ttyd_command_wraps_shell_in_script_and_su(container):
    script = "for f in /proc/[0-9]*/cmdline; do tr '\\0' ' ' < $f; echo; done"
    cmdlines = _exec(container, "root", "sh", "-c", script).stdout
    cmdlines += _exec(container, "root", "ps", "-eww", "-o", "args=").stdout
    assert "ttyd" in cmdlines, cmdlines
    assert "script -q -f /var/log/lf/terminal.log" in cmdlines, cmdlines
    assert "su - student" in cmdlines, cmdlines
