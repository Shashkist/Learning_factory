"""Acceptance tests for issue #9, Docker part: the runner against a real lab container (CI).

Drives `DockerRuntime.exec_script` (contract in test_issue_9_runner.py) on a container started
with the production spec (`container_spec`: runsc, `CapDrop ALL`, `CapAdd SETUID SETGID`,
`no-new-privileges`) from `lf-lab-term`. It proves D45 on the real thing: a setup script runs
as root with no extra capabilities, was streamed on stdin and left nothing behind, root cannot
read a student's 0600 file or write into the student's home, and a script that drops to
`student` itself (`su student -s /bin/sh -c ...`) can. The image is $LF_LAB_TERM_IMAGE if set,
else built from labs/images/term; the lab network is created if missing (as in #7's exam).
Only the `lab-<uuid>` container this module starts is touched. Student data is fake.
"""
import asyncio
import inspect
import os
import subprocess
import time
import uuid
from pathlib import Path

import lab_image
import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)

LAB_YAML = """id: runner
title:
  en: Runner lab
  he: מעבדת הרצה
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

SECRET = "fake-private-note-4471"


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
    return lab_image.ensure_lab_image()


@pytest.fixture(scope="module")
def runtime(network, image):
    mp = pytest.MonkeyPatch()
    mp.setenv("LF_LAB_TERM_IMAGE", image)
    mp.setenv("LF_LAB_NETWORK", network)
    mp.delenv("LF_DOCKER_HOST", raising=False)
    from app.runtime import DockerRuntime

    yield DockerRuntime()
    mp.undo()


@pytest.fixture(scope="module")
def box(runtime, call, tmp_path_factory, request) -> str:
    from app.content import load_labs
    from app.runtime import container_spec

    root = tmp_path_factory.mktemp("content")
    d = root / "test" / "runner"
    d.mkdir(parents=True)
    (d / "lab.yaml").write_text(LAB_YAML, encoding="utf-8")
    res = load_labs(root)
    assert "runner" in res.labs, res.errors
    session = str(uuid.uuid4())
    spec = container_spec(res.labs["runner"], session)
    name = f"lab-{session}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    assert str(call(runtime.start, spec)) == name
    deadline = time.time() + 60
    while time.time() < deadline:  # the entrypoint copies /etc/skel into the empty tmpfs home
        if docker("exec", name, "test", "-f", "/home/student/.bashrc", check=False).returncode == 0:
            return name
        time.sleep(0.5)
    raise AssertionError("/home/student/.bashrc never appeared (skel was not copied)")


def run(runtime, call, box: str, script: str, timeout: float = 60):
    return call(runtime.exec_script, box, script, timeout)


def student_exec(box: str, script: str) -> subprocess.CompletedProcess:
    return docker("exec", "-u", "student", box, "sh", "-c", script, check=False)


# ---------------------------------------------------------------- the runner on a real container


def test_a_setup_script_runs_as_root_in_the_session_container(runtime, call, box):
    res = run(runtime, call, box, "#!/bin/sh\nid -u\necho done\n")
    assert res.exit_code == 0 and res.timed_out is False
    assert res.output.split() == ["0", "done"]


def test_the_exec_has_root_with_a_clean_environment_in_the_root_directory(runtime, call, box):
    res = run(runtime, call, box, '#!/bin/sh\npwd\necho "$HOME"\nenv | cut -d= -f1 | sort\n')
    assert res.exit_code == 0
    lines = res.output.split()
    assert lines[:2] == ["/", "/root"], res.output
    names = set(lines[2:])
    assert {"PATH", "HOME"} <= names
    # An exec always inherits the container's own Env (set at create by `container_spec`:
    # LF_BASE_PATH, LF_SUDO); the exec adds only PATH, HOME and the timeout kill's per-exec
    # marker LF_EXEC_ID (#306, D98). Nothing from the manager.
    shell = {"PATH", "HOME", "PWD", "SHLVL", "_", "OLDPWD", "TERM", "HOSTNAME"}
    assert names <= shell | {"LF_BASE_PATH", "LF_SUDO", "LF_EXEC_ID"}, names


def test_exit_code_and_both_output_streams_come_back(runtime, call, box):
    res = run(runtime, call, box, "#!/bin/sh\necho to-out\necho to-err >&2\nexit 7\n")
    assert res.exit_code == 7
    assert "to-out" in res.output and "to-err" in res.output


def test_a_setup_script_can_prepare_the_students_home_by_dropping_to_the_student(
    runtime, call, box
):
    script = (
        "#!/bin/sh\nset -e\n"
        "su student -s /bin/sh -c 'echo prepared > /home/student/prepared.txt'\n"
        "echo ok\n"
    )
    res = run(runtime, call, box, script)
    assert res.exit_code == 0, res.output
    f = "/home/student/prepared.txt"
    seen = student_exec(box, f"cat {f}; stat -c %U {f}")
    assert seen.stdout.split() == ["prepared", "student"]


def test_the_script_was_streamed_never_written_into_the_container(runtime, call, box):
    marker = "STREAMED-ONLY-MARKER-" + uuid.uuid4().hex
    res = run(runtime, call, box, f"#!/bin/sh\n# {marker}\necho ran\n")
    assert res.exit_code == 0 and "ran" in res.output
    found = docker(
        "exec", box, "sh", "-c",
        "grep -rIl --exclude-dir=proc --exclude-dir=sys --exclude-dir=dev "
        f"-e {marker} / 2>/dev/null; true",
        check=False,
    )
    assert found.stdout.strip() == "", f"the script text is on the container's disk: {found.stdout}"


def test_root_in_the_exec_cannot_read_a_student_private_file(runtime, call, box):
    f = "/home/student/private.txt"
    student_exec(box, f"echo {SECRET} > {f} && chmod 600 {f}")
    assert student_exec(box, "cat /home/student/private.txt").stdout.strip() == SECRET
    res = run(runtime, call, box, "#!/bin/sh\ncat /home/student/private.txt\n")
    assert res.exit_code != 0, "root must not read the student's 0600 file (D45)"
    assert SECRET not in res.output
    assert "denied" in res.output.lower()


def test_root_in_the_exec_cannot_write_into_the_students_home_or_chown(runtime, call, box):
    res = run(runtime, call, box, "#!/bin/sh\ntouch /home/student/by-root\n")
    assert res.exit_code != 0
    res = run(runtime, call, box, "#!/bin/sh\nchown root /home/student/private.txt\n")
    assert res.exit_code != 0


def test_a_script_that_drops_to_the_student_can_read_the_private_file(runtime, call, box):
    f = "/home/student/private2.txt"
    student_exec(box, f"echo {SECRET} > {f} && chmod 600 {f}")
    script = "#!/bin/sh\nsu student -s /bin/sh -c 'cat /home/student/private2.txt'\n"
    res = run(runtime, call, box, script)
    assert res.exit_code == 0, res.output
    assert SECRET in res.output


def test_the_exec_holds_no_capability_beyond_setuid_and_setgid(runtime, call, box):
    res = run(runtime, call, box, "#!/bin/sh\ngrep -E '^CapEff' /proc/self/status\n")
    assert res.exit_code == 0, res.output
    caps = {ln.split()[0]: int(ln.split()[1], 16) for ln in res.output.splitlines() if ln.strip()}
    allowed = (1 << 6) | (1 << 7)  # CAP_SETGID, CAP_SETUID
    assert caps["CapEff:"] & ~allowed == 0, hex(caps["CapEff:"])


def test_a_script_that_outlives_its_timeout_is_reported_and_the_container_survives(
    runtime, call, box
):
    started = time.monotonic()
    res = run(runtime, call, box, "#!/bin/sh\nsleep 25\necho late\n", timeout=2)
    assert time.monotonic() - started < 15
    assert res.timed_out is True
    assert res.exit_code != 0
    assert "late" not in res.output
    again = run(runtime, call, box, "#!/bin/sh\necho still-alive\n")
    assert again.exit_code == 0 and "still-alive" in again.output


def test_a_script_without_a_shebang_runs_under_sh(runtime, call, box):
    res = run(runtime, call, box, "echo plain\n")
    assert res.exit_code == 0 and res.output.strip() == "plain"


def test_a_bash_shebang_runs_under_bash(runtime, call, box):
    res = run(runtime, call, box, '#!/bin/bash\necho "$BASH_VERSION" | cut -c1\n')
    assert res.exit_code == 0 and res.output.strip().isdigit()


def test_an_unknown_container_raises(runtime, call):
    with pytest.raises(Exception):  # noqa: B017
        run(runtime, call, "lab-" + str(uuid.uuid4()), "#!/bin/sh\ntrue\n", timeout=10)
