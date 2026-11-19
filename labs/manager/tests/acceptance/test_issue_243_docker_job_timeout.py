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
# A per-test timeout must leave at least this much before the job limit: setup and the tests
# that ran before it already used part of the job (a full docker exam run is ~8 minutes).
MARGIN_S = 10 * 60
# Any name containing "timeout" (keyword argument, constant, annotated default) assigned a value.
ASSIGN = re.compile(r"(?i)\b\w*timeout\w*\s*(?::\s*[\w\[\]. |]+?)?\s*=(?!=)\s*([^,)\n#]+)")
NUMBER = re.compile(r"[\d_]+(?:\.\d+)?(?:\s*\*\s*[\d_]+(?:\.\d+)?)*")


def _seconds(rhs: str) -> float | None:
    """The leading literal value in seconds, None when it is not a literal (a name, None, a call).
    Reads the number even inside a string (`"timeout=120" in line`): too strict beats too lax."""
    m = NUMBER.match(rhs.strip())
    if not m:
        return None
    value = 1.0
    for factor in m.group().split("*"):
        value *= float(factor.strip().replace("_", ""))
    return value


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
    offenders = []
    for path in LABS.glob("*/tests/acceptance/*.py"):
        if path.resolve() == SELF:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in ASSIGN.finditer(line):
                seconds = _seconds(m.group(1))
                if seconds is not None and seconds > limit_s:
                    where = f"{path.relative_to(REPO_ROOT)}:{n}"
                    offenders.append(f"{where}: {seconds:g}s > {limit_s}s")
    assert not offenders, "timeouts over the docker job limit:\n" + "\n".join(offenders)


def test_the_scan_reads_every_way_a_timeout_is_written():
    for text, seconds in [
        ("BUILD_TIMEOUT = 900", 900),
        ("def _run(cmd, timeout: int = 1800):", 1800),
        ("build_timeout = 1_800", 1800),
        ("TIMEOUT_S = 30 * 60", 1800),
        ("subprocess.run(cmd, timeout=600, check=True)", 600),
        ("timeout: float = 0.5", 0.5),
        ("TIMEOUT = 1800  # seconds", 1800),
    ]:
        m = ASSIGN.search(text)
        assert m and _seconds(m.group(1)) == seconds, text
    for text in ["timeout=timeout", "timeout=None", "timeout=BUILD_TIMEOUT", "if timeout == 5:"]:
        m = ASSIGN.search(text)
        assert m is None or _seconds(m.group(1)) is None, text
