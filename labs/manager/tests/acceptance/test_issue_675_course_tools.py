"""Acceptance tests for issue #675: course tools in the lab image lf-lab-term (D191).

Static tests run everywhere and read labs/images/term/Dockerfile and entrypoint.sh.
Docker tests run in the CI `docker` job and use the shared image build (lab_image).
"""
import re
import subprocess
import uuid
from pathlib import Path

import lab_image
import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
IMAGE_DIR = REPO_ROOT / "labs" / "images" / "term"
DOCKERFILE = IMAGE_DIR / "Dockerfile"
ENTRYPOINT = IMAGE_DIR / "entrypoint.sh"

APT_PACKAGES = [
    "python3",
    "python3-venv",
    "python3-pytest",
    "python3-flask",
    "git",
    "sqlite3",
    "jq",
    "cron",
    "netcat-openbsd",
]
DAEMON_NAMES = ["cron", "crond", "atd", "anacron", "rsyslogd", "sshd", "dbus-daemon", "systemd"]


# ---------------------------------------------------------------- static


def _dockerfile() -> str:
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"
    return DOCKERFILE.read_text(encoding="utf-8")


def _code_lines(text: str) -> str:
    """Dockerfile or shell text without comment-only lines."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


@pytest.mark.parametrize("pkg", APT_PACKAGES)
def test_dockerfile_installs_course_package_with_apt(pkg):
    text = _code_lines(_dockerfile())
    assert re.search(r"apt(-get)?\s+install", text), "no apt install in the Dockerfile"
    assert re.search(rf"(?<![\w.+-]){re.escape(pkg)}(?![\w.+-])", text), f"{pkg} is not installed"


def test_dockerfile_does_not_install_python_packages_from_the_network():
    text = _code_lines(_dockerfile())
    assert not re.search(r"\bpip3?\s+install\b", text), "pip install: labs have no internet"
    assert not re.search(r"-m\s+pip\s+install\b", text), "pip install: labs have no internet"
    assert not re.search(r"\b(uv|pipx|poetry|conda)\s+(pip\s+)?install\b", text)


def test_dockerfile_does_not_enable_or_start_cron():
    text = _code_lines(_dockerfile())
    assert not re.search(r"systemctl\s+(enable|start)", text), "service enabled in the image"
    assert not re.search(r"update-rc\.d|rc-update|service\s+cron\s+start", text)
    assert not re.search(r"(^|[\s;&|(])(cron|crond)(\s+-f)?\s*($|[;&|)])", text, flags=re.M), (
        "cron daemon started from the Dockerfile"
    )


def test_entrypoint_does_not_start_a_daemon():
    assert ENTRYPOINT.is_file(), f"{ENTRYPOINT} is missing"
    text = _code_lines(ENTRYPOINT.read_text(encoding="utf-8"))
    assert not re.search(r"(^|[\s;&|(])(cron|crond|atd|anacron)(\s|$)", text, flags=re.M)
    assert not re.search(r"service\s+\S+\s+start|systemctl\s+start", text)


def test_dockerfile_keeps_ttyd_entrypoint_and_student_user():
    text = _dockerfile()
    assert re.search(r"^\s*ENTRYPOINT\s+.*lf-entrypoint", text, flags=re.M)
    assert "useradd" in text and "student" in text
    assert not re.search(r"^\s*USER\s+", text, flags=re.M), "image must keep its root-then-su model"


# ---------------------------------------------------------------- docker


def _docker(*args: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


@pytest.fixture(scope="module")
def image() -> str:
    return lab_image.ensure_lab_image()


@pytest.fixture(scope="module")
def container(image, request) -> str:
    name = f"lf-exam-{uuid.uuid4().hex[:10]}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    _docker(
        "run", "-d", "--name", name, "--runtime=runsc", "--log-driver", "none",
        "-e", "LF_BASE_PATH=/s/exam675/term", image,
    )
    return name


def _student(name: str, script: str) -> subprocess.CompletedProcess:
    return _docker(
        "exec", "-u", "student", "-w", "/home/student", name, "sh", "-c", script,
        check=False, timeout=120,
    )


@pytest.mark.docker
def test_student_can_import_flask_and_pytest(container):
    res = _student(container, "python3 -c 'import flask, pytest; print(\"ok\")'")
    assert res.returncode == 0, res.stderr
    assert "ok" in res.stdout


@pytest.mark.docker
def test_student_can_create_a_working_venv(container):
    res = _student(
        container,
        'd=$(mktemp -d) && python3 -m venv "$d/v" && "$d/v/bin/python" -c \'print(40+2)\''
        ' && test -f "$d/v/bin/activate"',
    )
    assert res.returncode == 0, res.stderr
    assert "42" in res.stdout


@pytest.mark.docker
def test_student_can_run_pytest(container):
    script = (
        "d=$(mktemp -d) && printf 'def test_a():\\n    assert 1 + 1 == 2\\n' > \"$d/test_a.py\" "
        '&& cd "$d" && python3 -m pytest -q -p no:cacheprovider'
    )
    res = _student(container, script)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 passed" in res.stdout


@pytest.mark.docker
def test_student_can_run_git(container):
    res = _student(container, "git --version")
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("git version")


@pytest.mark.docker
def test_student_can_init_a_git_repository(container):
    res = _student(container, 'd=$(mktemp -d) && git init -q "$d" && test -d "$d/.git"')
    assert res.returncode == 0, res.stderr


@pytest.mark.docker
def test_student_can_run_sqlite3(container):
    res = _student(container, "sqlite3 -version")
    assert res.returncode == 0, res.stderr
    assert re.match(r"\d+\.\d+", res.stdout.strip())
    q = _student(container, "sqlite3 :memory: 'select 6*7;'")
    assert q.returncode == 0 and q.stdout.strip() == "42", q.stderr


@pytest.mark.docker
def test_student_can_run_jq(container):
    res = _student(container, "jq --version")
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("jq-")
    q = _student(container, "echo '{\"a\":[1,2]}' | jq -c '.a'")
    assert q.stdout.strip() == "[1,2]", q.stderr


@pytest.mark.docker
def test_crontab_works_for_student_and_reports_only_no_crontab(container):
    res = _student(container, "crontab -l")
    out = (res.stdout + res.stderr).lower()
    # Empty crontab: exit 1 with "no crontab for student". Anything else is a broken install.
    assert res.returncode in (0, 1), out
    if res.returncode == 1:
        assert "no crontab" in out, out
    assert "not found" not in out and "permission denied" not in out


@pytest.mark.docker
def test_student_can_install_and_read_back_a_crontab(container):
    script = "echo '*/5 * * * * echo hi' | crontab - && crontab -l; crontab -r"
    res = _student(container, script)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "*/5 * * * * echo hi" in res.stdout


@pytest.mark.docker
def test_student_can_run_netcat(container):
    res = _student(container, "nc -h 2>&1")
    out = res.stdout.lower()
    assert "usage" in out or "nc [" in out or "netcat" in out, out
    assert "not found" not in out


@pytest.mark.docker
def test_container_still_runs_the_lab_as_student_without_sudo(container):
    who = _student(container, "id -un; id -u")
    assert who.stdout.split() == ["student", "1001"], who.stdout
    res = _student(container, "command -v sudo >/dev/null 2>&1 && sudo -n true")
    assert res.returncode != 0, "student can sudo without a password"
    groups = _docker("exec", container, "id", "-nG", "student").stdout.split()
    assert "sudo" not in groups and "admin" not in groups, groups


@pytest.mark.docker
def test_no_cron_daemon_or_other_service_is_running(container):
    ps = _docker("exec", container, "ps", "-eo", "pid,ppid,comm,args", check=False).stdout
    rows = [ln.split(None, 3) for ln in ps.splitlines()[1:]]
    rows = [r for r in rows if len(r) >= 3]
    comms = [r[2] for r in rows]
    for daemon in DAEMON_NAMES:
        assert daemon not in comms, f"{daemon} is running:\n{ps}"
    assert "ttyd" in comms, ps
    # Every child of PID 1 (tini) is the terminal server; nothing else was started at boot.
    probes = {"ps", "sh", "docker", "runsc-sandbox"}
    kids = [r[2] for r in rows if r[1] == "1" and r[0] != "1" and r[2] not in probes]
    assert kids in (["ttyd"], []), f"unexpected services: {kids}\n{ps}"
