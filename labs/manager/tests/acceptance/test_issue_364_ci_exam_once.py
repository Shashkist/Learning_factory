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
BACKSLASH_NEWLINE = "\\\n"


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _runs(job: dict) -> list[str]:
    return [s["run"] for s in job.get("steps", []) if "run" in s]


def _in_manager_dir(job: dict, step: dict) -> bool:
    where = step.get("working-directory") or job.get("defaults", {}).get("run", {}).get(
        "working-directory")
    return where == "labs/manager"


def _step_pytest_lines(job: dict) -> list[tuple[dict, str]]:
    """(step, logical line) for every line of the job's run steps that runs pytest."""
    out = []
    for step in job.get("steps", []):
        for line in step.get("run", "").replace(BACKSLASH_NEWLINE, " ").splitlines():
            if not line.lstrip().startswith("#") and re.search(r"\bpytest\b", line):
                out.append((step, line.strip()))
    return out


def _pytest_lines(job: dict) -> list[str]:
    return [ln for _, ln in _step_pytest_lines(job)]


def _is_docker_run(line: str) -> bool:
    return re.search(r"-m\s+docker\b", line) is not None


def _only_unit(line: str) -> bool:
    return bool(re.search(r"\btests/unit\b", line)) and "tests/acceptance" not in line


def _runs_exam(job: dict, step: dict, line: str) -> bool:
    """True when this pytest run executes the non-docker acceptance exam, named or through
    testpaths: any pytest in labs/manager, unless it ignores the exam, targets only
    tests/unit, or is the docker-marked run."""
    if _is_docker_run(line) or IGNORE_EXAM.search(line):
        return False
    if "tests/acceptance" in line:
        return True
    return _in_manager_dir(job, step) and not _only_unit(line)


def _exam_runs() -> list[tuple[str, dict, dict, str]]:
    return [(name, job, step, ln) for name, job in _jobs().items()
            for step, ln in _step_pytest_lines(job) if _runs_exam(job, step, ln)]


def _once_message(runs: list) -> str:
    return (f"the non-docker exam is started {len(runs)} times: "
            f"{[(n, ln) for n, _, _, ln in runs]}")


def _exam_job() -> tuple[str, dict]:
    names = {name for name, *_ in _exam_runs()}
    assert len(names) == 1, f"want one job running the non-docker exam, got {sorted(names)}"
    name = next(iter(names))
    return name, _jobs()[name]


def _exam_line() -> str:
    runs = _exam_runs()
    assert len(runs) == 1, _once_message(runs)
    return runs[0][3]


def _exam_step() -> dict:
    return _exam_runs()[0][2]


def _gate_steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps", []) if "junit_gate.py" in s.get("run", "")]


# 1. the exam runs once

def test_the_exam_runs_in_exactly_one_non_docker_place():
    runs = _exam_runs()
    assert len(runs) == 1, _once_message(runs)
    assert "not docker" in _exam_line()


def test_the_manager_job_does_not_run_the_exam():
    lines = _pytest_lines(_jobs()["manager"])
    assert lines, "the manager job runs no pytest at all"
    assert "manager" not in {n for n, *_ in _exam_runs()}, "the manager job runs the exam"
    assert all(IGNORE_EXAM.search(ln) or _only_unit(ln) for ln in lines), (
        f"every manager pytest run must exclude the exam: {lines}")


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


def test_the_exam_run_is_not_filtered_by_any_other_option():
    line = re.sub(r"\s--junitxml[= ]\S+", "", _exam_line())
    args = shlex.split(line)
    assert args[args.index("tests/acceptance"):] == [
        "tests/acceptance", "-q", "--confcutdir=tests", "-o", "addopts=", "-m", "not docker"]
    assert args[:args.index("tests/acceptance")][-1] == "pytest"


def test_the_exam_job_and_steps_cannot_fail_or_skip_softly():
    _, job = _exam_job()
    gates = _gate_steps(job)
    assert gates, "no junit_gate step"
    checked = [("job", job), ("exam step", _exam_step())]
    checked += [("gate step", g) for g in gates]
    for what, item in checked:
        assert "continue-on-error" not in item, f"{what} has continue-on-error"
        assert "if" not in item, f"{what} has an if condition"
    for step in [_exam_step(), *gates]:
        text = step["run"]
        assert not re.search(r"\bset\s+\+e\b", text), text
        assert not re.search(r"(^|[;&|\s])exit\s+0\s*$", text.strip()), text
        assert not re.search(r";\s*true\b", text), text
        assert "|| true" not in text and "|| :" not in text, text


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
    lines = _pytest_lines(_jobs()["manager"])
    assert lines, "no unit pytest run in the manager job"
    for ln in lines:
        assert IGNORE_EXAM.search(ln) or _only_unit(ln), ln
        for flag in (" -k ", "--deselect", "--collect-only", "--lf ", "-m docker"):
            assert flag not in ln + " ", ln
    assert any(IGNORE_EXAM.search(ln) or "tests/unit" in ln for ln in lines)
