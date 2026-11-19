"""Exam for #228: build the lab image once, fail fast on a mirror outage.

Pinned contract (static tests, no Docker, run on every CI job):

1. Dockerfile: `labs/images/term/Dockerfile` bounds every apt-get call. Either
   each `apt-get update` / `apt-get install` carries BOTH
   `-o Acquire::Retries=<n>` (1..5) and `-o Acquire::http::Timeout=<s>` (5..60)
   on its own command line, or the Dockerfile writes one file under
   /etc/apt/apt.conf.d/ that sets `Acquire::Retries "<n>";` and
   `Acquire::http::Timeout "<s>";` in a RUN before the first apt-get call.
2. Shared helper: `tests/acceptance/lab_image.py` is the only module that builds
   the exam image (tag `lf-lab-term:exam`). API: `ensure_lab_image() -> str`,
   `ImageBuilder(build).get()`, `build_image(run=subprocess.run)`,
   `ImageBuildFailed`. A failed build is remembered, later calls raise at once
   with the build output tail, and the build is not attempted again.
3. Every docker exam module gets its image from `lab_image.ensure_lab_image()`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import lab_image
import pytest

HERE = Path(__file__).resolve().parent
DOCKERFILE = lab_image.IMAGE_DIR / "Dockerfile"
SELF = Path(__file__).name
HELPER = "lab_image.py"
APT_CALL = re.compile(r"\bapt-get\b[^&;|]*?\b(update|install)\b")
MIN_RETRIES, MAX_RETRIES = 1, 5
MIN_TIMEOUT, MAX_TIMEOUT = 5, 60


def _dockerfile_text() -> str:
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing"
    return DOCKERFILE.read_text(encoding="utf-8")


def _logical_lines(text: str) -> list[str]:
    return re.sub(r"\\r?\n", " ", text).splitlines()


def _apt_commands(text: str) -> list[str]:
    cmds = []
    for line in _logical_lines(text):
        for part in re.split(r"&&|;|\|\|", line):
            if APT_CALL.search(part):
                cmds.append(part.strip())
    return cmds


def _conf_bounds(text: str) -> bool:
    flat = " ".join(_logical_lines(text))
    m_r = re.search(r"Acquire::Retries\s*\"?(\d+)", flat)
    m_t = re.search(r"Acquire::http::Timeout\s*\"?(\d+)", flat)
    if not (m_r and m_t and "/etc/apt/apt.conf.d/" in flat):
        return False
    first_apt = APT_CALL.search(flat)
    if first_apt and first_apt.start() < min(m_r.start(), m_t.start()):
        return False
    return (
        MIN_RETRIES <= int(m_r.group(1)) <= MAX_RETRIES
        and MIN_TIMEOUT <= int(m_t.group(1)) <= MAX_TIMEOUT
    )


def _cmd_bounded(cmd: str) -> bool:
    r = re.search(r"Acquire::Retries=(\d+)", cmd)
    t = re.search(r"Acquire::http::Timeout=(\d+)", cmd)
    return bool(
        r
        and t
        and MIN_RETRIES <= int(r.group(1)) <= MAX_RETRIES
        and MIN_TIMEOUT <= int(t.group(1)) <= MAX_TIMEOUT
    )


def test_dockerfile_apt_get_has_bounded_retries_and_timeouts():
    text = _dockerfile_text()
    cmds = _apt_commands(text)
    assert cmds, "the Dockerfile no longer calls apt-get; update this exam"
    if _conf_bounds(text):
        return
    unbounded = [c for c in cmds if not _cmd_bounded(c)]
    assert not unbounded, (
        "apt-get without `-o Acquire::Retries=N` (1..5) and "
        "`-o Acquire::http::Timeout=S` (5..60), and no apt.conf.d file sets them: "
        + "; ".join(unbounded)
    )


def test_dockerfile_apt_get_does_not_disable_the_bounds():
    text = _dockerfile_text()
    assert not re.search(r"Acquire::Retries[=\s\"]+0\b", text)
    assert not re.search(r"Acquire::http::Timeout[=\s\"]+0\b", text)


def _acceptance_sources():
    return [p for p in sorted(HERE.glob("*.py")) if p.name not in (SELF, HELPER)]


def test_only_the_shared_helper_builds_the_exam_image():
    offenders = []
    for p in _acceptance_sources():
        text = p.read_text(encoding="utf-8")
        if "lf-lab-term:exam" in text or re.search(r"[\"']build[\"'][^\n]*IMAGE_DIR", text):
            offenders.append(p.name)
    assert not offenders, f"these modules still build the lab image themselves: {offenders}"


def test_every_docker_exam_gets_its_image_from_the_shared_helper():
    users = [
        p.name
        for p in _acceptance_sources()
        if re.search(r"^def image\(", p.read_text(encoding="utf-8"), re.M)
    ]
    assert len(users) >= 10, f"expected the ten docker exam modules, found {users}"
    missing = [
        n
        for n in users
        if "lab_image.ensure_lab_image()" not in (HERE / n).read_text(encoding="utf-8")
    ]
    assert not missing, f"image fixture does not use lab_image.ensure_lab_image(): {missing}"


def test_helper_builds_the_exam_tag_from_the_term_image_dir():
    assert lab_image.TAG == "lf-lab-term:exam"
    assert lab_image.IMAGE_DIR == DOCKERFILE.parent
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert lab_image.build_image(run=fake_run) == "lf-lab-term:exam"
    assert len(calls) == 1
    assert calls[0][:2] == ["docker", "build"]
    assert "lf-lab-term:exam" in calls[0]
    assert str(lab_image.IMAGE_DIR) in calls[0]


def test_failed_build_error_carries_the_last_output_lines():
    out = "\n".join(f"step {i}" for i in range(200))

    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=100, stdout=out, stderr="E: Unable to connect to mirror")

    with pytest.raises(lab_image.ImageBuildFailed) as err:
        lab_image.build_image(run=fake_run)
    msg = str(err.value)
    assert "Unable to connect to mirror" in msg
    assert "step 199" in msg
    assert "step 0\n" not in msg, "the whole log was dumped instead of the last lines"


def test_build_timeout_is_reported_as_a_build_failure():
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1, output=b"Err:1 http://security.ubuntu.com")

    with pytest.raises(lab_image.ImageBuildFailed) as err:
        lab_image.build_image(run=fake_run)
    assert "timed out" in str(err.value)


def test_failed_build_is_remembered_and_never_retried():
    calls = []

    def build():
        calls.append(1)
        raise lab_image.ImageBuildFailed("mirror refused connection\nlast line")

    builder = lab_image.ImageBuilder(build)
    for _ in range(5):
        with pytest.raises(lab_image.ImageBuildFailed, match="last line"):
            builder.get()
    assert len(calls) == 1


def test_successful_build_happens_once_for_every_caller():
    calls = []

    def build():
        calls.append(1)
        return "lf-lab-term:exam"

    builder = lab_image.ImageBuilder(build)
    assert [builder.get() for _ in range(4)] == ["lf-lab-term:exam"] * 4
    assert len(calls) == 1


def test_prebuilt_image_from_the_environment_skips_the_build(monkeypatch):
    monkeypatch.setenv("LF_LAB_TERM_IMAGE", "registry.example/lab:sha")
    assert lab_image.ensure_lab_image() == "registry.example/lab:sha"
