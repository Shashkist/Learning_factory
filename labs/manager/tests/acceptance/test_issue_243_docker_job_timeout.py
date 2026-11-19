"""Acceptance tests for issue #243: the docker CI job has an explicit timeout, and every per-test
timeout in the exams stays well under it, so a hung build prints its own clear error before the
job is cancelled. Non-docker: parses ci.yml and the exam sources."""
import re
from pathlib import Path

import yaml

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LABS = REPO_ROOT / "labs"
SELF = Path(__file__).resolve()
MARGIN_S = 5 * 60  # a per-test timeout must leave at least this much before the job limit


def _docker_job_minutes() -> int:
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8")).get("jobs") or {}
    assert "docker" in jobs, "ci.yml has no docker job"
    value = jobs["docker"].get("timeout-minutes")
    assert value is not None, "docker job has no timeout-minutes (GitHub default is 360)"
    return int(value)


def test_docker_job_has_explicit_timeout_between_20_and_69_minutes():
    minutes = _docker_job_minutes()
    assert 20 <= minutes <= 69, f"docker job timeout-minutes is {minutes}, want 20..69"


def test_every_acceptance_timeout_is_under_the_docker_job_limit():
    limit_s = _docker_job_minutes() * 60 - MARGIN_S
    pattern = re.compile(r"(?:\btimeout\s*=|\b[A-Z_]*TIMEOUT\s*=)\s*(\d{3,})\b")
    offenders = []
    for path in LABS.glob("*/tests/acceptance/*.py"):
        if path.resolve() == SELF:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = pattern.search(line)
            if m and int(m.group(1)) > limit_s:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}: {m.group(1)}s > {limit_s}s")
    assert not offenders, "timeouts over the docker job limit:\n" + "\n".join(offenders)


def test_build_timeout_constant_is_under_the_docker_job_limit():
    src = (LABS / "manager" / "tests" / "acceptance" / "lab_image.py").read_text(encoding="utf-8")
    m = re.search(r"^BUILD_TIMEOUT\s*=\s*(\d+)", src, re.M)
    assert m, "lab_image.BUILD_TIMEOUT not found"
    assert int(m.group(1)) <= _docker_job_minutes() * 60 - MARGIN_S
