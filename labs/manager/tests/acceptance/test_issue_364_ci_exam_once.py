"""Acceptance tests for issue #364 (D116): CI runs the manager acceptance exam once.

The `manager` job keeps ruff, mypy and the unit tests; a parallel `manager-exam` job runs the
exam once with the no-skip command line. Parses ci.yml and runs the real gate; no Docker.
"""
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GATE = REPO_ROOT / "scripts" / "ci" / "junit_gate.py"
IGNORE_EXAM = re.compile(r"--ignore[= ]\S*tests/acceptance")


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _runs(job: dict) -> list[str]:
    return [s["run"] for s in job.get("steps", []) if "run" in s]


def _pytest_lines(job: dict) -> list[str]:
    """Every logical line of the job's run steps that runs pytest (joins backslash lines)."""
    out = []
    for run in _runs(job):
        for line in run.replace("\\\n", " ").splitlines():
            if re.search(r"\bpytest\b", line):
                out.append(line.strip())
    return out


def _runs_exam(line: str) -> bool:
    return "tests/acceptance" in line and "-m docker" not in line and not IGNORE_EXAM.search(line)


def _exam_jobs() -> dict:
    """Jobs that run the acceptance folder in the non-docker path, with their pytest lines."""
    found = {}
    for name, job in _jobs().items():
        lines = [ln for ln in _pytest_lines(job) if _runs_exam(ln)]
        if lines:
            found[name] = lines
    return found


def _exam_job() -> tuple[str, dict]:
    jobs = _exam_jobs()
    assert len(jobs) == 1, f"want exactly one job running the non-docker exam, got {sorted(jobs)}"
    name = next(iter(jobs))
    return name, _jobs()[name]


def _exam_line() -> str:
    name, _ = _exam_job()
    lines = _exam_jobs()[name]
    assert len(lines) == 1, f"job {name} runs the exam {len(lines)} times: {lines}"
    return lines[0]


# 1. the exam runs once

def test_the_exam_runs_in_exactly_one_non_docker_place():
    count = sum(len(v) for v in _exam_jobs().values())
    assert count == 1, f"the non-docker exam is started {count} times in ci.yml"
    assert "not docker" in _exam_line()


def test_the_manager_job_does_not_run_the_exam():
    job = _jobs()["manager"]
    assert _pytest_lines(job), "the manager job runs no pytest at all"
    assert "manager" not in _exam_jobs(), "the manager job still runs tests/acceptance"
    assert any(IGNORE_EXAM.search(ln) or "tests/unit" in ln for ln in _pytest_lines(job)), \
        "the manager unit run must exclude the exam"


def test_the_exam_is_its_own_parallel_job():
    name, job = _exam_job()
    assert name != "manager"
    assert "needs" not in job, "the exam job must run in parallel, not wait for another job"
    assert "needs" not in _jobs()["manager"]


def test_the_docker_job_still_runs_its_own_docker_exam():
    lines = [ln for ln in _pytest_lines(_jobs()["docker"])
             if "tests/acceptance" in ln and "-m docker" in ln]
    assert len(lines) == 1 and "--junitxml=docker-acceptance.xml" in lines[0]


# 2. the no-skip guarantee stays

def test_the_exam_run_keeps_confcutdir_and_clears_addopts():
    line = _exam_line()
    assert "--confcutdir=tests" in line
    assert re.search(r"-o addopts=(\"\"|'')", line)


def test_the_exam_run_writes_a_junit_report_and_the_gate_reads_that_same_report():
    _, job = _exam_job()
    m = re.search(r"--junitxml[= ](\S+)", _exam_line())
    assert m, "the exam run writes no junit report"
    report = m.group(1)
    gate = [r for r in _runs(job)
            if re.search(r"junit_gate\.py\s+" + re.escape(report) + r"(\s|$)", r)]
    assert gate, f"no junit_gate.py call on {report}"
    assert "scripts/ci/junit_gate.py" in gate[0]


def test_the_gate_step_runs_in_or_after_the_exam_step():
    _, job = _exam_job()
    runs = _runs(job)
    exam = next(i for i, r in enumerate(runs) if "tests/acceptance" in r and "not docker" in r)
    assert any("junit_gate.py" in r for r in runs[exam:])


def test_the_exam_step_cannot_fail_softly():
    _, job = _exam_job()
    for step in job["steps"]:
        text = step.get("run", "")
        if "tests/acceptance" in text or "junit_gate" in text:
            assert not step.get("continue-on-error"), step
            assert "|| true" not in text and "|| :" not in text


def _scratch(tmp_path: Path, body: str) -> Path:
    (tmp_path / "tests" / "acceptance").mkdir(parents=True)
    (tmp_path / "tests" / "acceptance" / "test_scratch.py").write_text(body, encoding="utf-8")
    return tmp_path


def _run_exam_and_gate(cwd: Path) -> tuple[int, str]:
    """The real exam pytest options from ci.yml on a scratch folder, then the real gate."""
    parts = shlex.split(_exam_line().replace("uv run pytest", "PYTEST"))
    assert parts[0] == "PYTEST"
    xml = next(p.split("=", 1)[1] for p in parts if p.startswith("--junitxml="))
    cmd = [sys.executable, "-I", "-m", "pytest", *parts[1:]]
    subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    gate = subprocess.run([sys.executable, "-I", str(GATE), xml], cwd=cwd,
                          capture_output=True, text=True, timeout=60)
    return gate.returncode, gate.stdout


def test_a_skipped_exam_case_fails_the_real_exam_command_and_gate(tmp_path):
    cwd = _scratch(tmp_path, "import pytest\n\ndef test_ok():\n    pass\n\n"
                             "def test_skipped():\n    pytest.skip('x')\n")
    code, out = _run_exam_and_gate(cwd)
    assert code != 0, "a skipped case went through the gate"
    assert "skipped" in out


def test_an_all_passing_exam_passes_the_real_exam_command_and_gate(tmp_path):
    cwd = _scratch(tmp_path, "def test_ok():\n    pass\n")
    code, out = _run_exam_and_gate(cwd)
    assert code == 0, out


# 3. both manager jobs: Postgres, env, tools, timeout

def _pick(which: str) -> dict:
    return _jobs()["manager"] if which == "manager" else _exam_job()[1]


@pytest.mark.parametrize("which", ["manager", "exam"])
def test_manager_jobs_have_postgres_and_the_test_database_url(which):
    job = _pick(which)
    assert "postgres" in job.get("services", {}), "no postgres service"
    assert "postgres" in str(job["services"]["postgres"].get("image"))
    assert job.get("env", {}).get("LF_TEST_DATABASE_URL", "").startswith("postgresql://")
    assert job["defaults"]["run"]["working-directory"] == "labs/manager"


@pytest.mark.parametrize("which", ["manager", "exam"])
def test_manager_jobs_have_a_timeout_with_margin(which):
    assert int(_pick(which)["timeout-minutes"]) >= 20


def test_the_manager_job_runs_ruff_and_mypy():
    text = "\n".join(_runs(_jobs()["manager"]))
    assert "uv run ruff check" in text
    assert re.search(r"uv run mypy\b", text)


@pytest.mark.parametrize("which", ["manager", "exam"])
def test_both_manager_jobs_install_the_environment(which):
    assert any("uv sync" in r for r in _runs(_pick(which)))


# 4. the unit run still covers everything but the exam

def test_the_unit_run_covers_everything_in_tests_except_acceptance():
    lines = [ln for ln in _pytest_lines(_jobs()["manager"]) if "uv run pytest" in ln]
    ok = [ln for ln in lines if IGNORE_EXAM.search(ln)
          or (re.search(r"\btests/unit\b", ln) and "tests/acceptance" not in ln)]
    assert ok, f"no manager pytest run excludes the exam: {lines}"
    for ln in ok:
        for flag in (" -k ", "--deselect", "--collect-only", "--lf ", "-m docker"):
            assert flag not in ln + " ", ln
