"""Acceptance tests for issue #364 (D116): CI runs the manager acceptance exam once.

The `manager` job keeps ruff, mypy and the unit tests; a parallel `manager-exam` job runs the
exam once with the no-skip command line. Parses ci.yml and runs the real gate; no Docker.

Two layouts are accepted (D166), each checked as strictly as the other: the local layout (the
exam job holds the steps) and the reusable layout of D165 (the exam job `uses:` the factory's
junit-gate.yml at a pinned sha and passes the exam command as `test-command`). A reusable job is
read as a job whose steps are the command lines of `test-command`, so the once, no-skip and
flag checks below run on the same command either way; the checks that are about a local gate
step become checks of the pinned call, its `junit-file` and the command's fail-fast shell.

The exam line must start with `uv run pytest` (no `timeout` prefix, no `python -m`): the
allow-list below pins the exam step to exactly the exam line and the gate line, so a prefix
would need an exam change on purpose.
"""
import posixpath
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
IGNORE_EXAM = re.compile(r"--ignore[= ](\./)?tests/acceptance/?(?=\s|$)")
MANAGER_DIR_IN_LINE = re.compile(r"(\bcd\s+|--directory[= ]\s*)(\./)?labs/manager/?(?=\s|$|;|&|\|)")
EXAM_PREFIX = ["uv", "run", "pytest"]
EXAM_ARGS = ["tests/acceptance", "-q", "--confcutdir=tests", "-o", "addopts=", "-m", "not docker"]


REUSABLE_GATE = re.compile(
    r"^Shashkist/factory/\.github/workflows/junit-gate\.yml@[0-9a-f]{40}$")
CD_MANAGER = re.compile(r"^cd\s+(\./)?labs/manager/?$")
# every line a reusable exam command may hold: fail-fast shell, the postgres start and wait,
# the database url, the move into labs/manager, the environment install, the exam line itself
SLOT = r"[^;&|$`(){}<>]*"  # no command separator, substitution, redirection or group
REUSABLE_LINES = [
    r"set -euo pipefail",
    r"docker run -d " + SLOT + r"\bpostgres:16",
    r"for i in \$\(seq \d+ \d+\); do",
    r"if docker exec [\w.-]+ pg_isready( -U \w+)?; then break; fi",
    r"sleep \d+",
    r"done",
    r"docker exec [\w.-]+ pg_isready( -U \w+)?",
    r"export LF_TEST_DATABASE_URL=postgresql://[\w:@./-]+",
    r"cd labs/manager",
    r"uv sync",
    r"uv run pytest " + SLOT,
]


def _wf() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _read_as_job(job: dict) -> dict:
    """A `uses:` caller job of junit-gate.yml, read as a job of steps: one step per command
    line of its `test-command`, in labs/manager after a `cd labs/manager` line. Other jobs
    come back as they are."""
    if "uses" not in job:
        return job
    with_ = job.get("with") or {}
    steps, where = [], None
    for line in _logical_lines(str(with_.get("test-command", ""))):
        steps.append({"run": line, **({"working-directory": where} if where else {})})
        if CD_MANAGER.match(line):
            where = "labs/manager"
    out = {k: v for k, v in job.items() if k not in ("uses", "with")}
    out.update(steps=steps, _uses=job["uses"], _with=with_)
    if with_.get("timeout-minutes") is not None:
        out["timeout-minutes"] = with_["timeout-minutes"]
    return out


def _reusable(job: dict) -> bool:
    return "_uses" in job


def _jobs() -> dict:
    return {k: _read_as_job(v) for k, v in _wf()["jobs"].items()}


def _logical_lines(run: str) -> list[str]:
    """The command lines of a run script: backslash lines joined, comments and blanks dropped."""
    out = []
    for line in run.replace("\\\n", " ").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _runs(job: dict) -> list[str]:
    return [s["run"] for s in job.get("steps", []) if "run" in s]


def _in_manager_dir(job: dict, step: dict, line: str) -> bool:
    where = step.get("working-directory") or job.get("defaults", {}).get("run", {}).get(
        "working-directory")
    if where and posixpath.normpath(str(where)) == "labs/manager":
        return True
    return MANAGER_DIR_IN_LINE.search(line) is not None


def _step_pytest_lines(job: dict) -> list[tuple[dict, str]]:
    """(step, logical line) for every command line of the job that runs pytest."""
    return [(step, line) for step in job.get("steps", [])
            for line in _logical_lines(step.get("run", "")) if re.search(r"\bpytest\b", line)]


def _pytest_lines(job: dict) -> list[str]:
    return [ln for _, ln in _step_pytest_lines(job)]


def _is_docker_run(line: str) -> bool:
    """True when the marker pytest ends up using (the last -m wins) is exactly `docker`."""
    try:
        tokens = shlex.split(line)
    except ValueError:
        return False
    marker = None
    for i, tok in enumerate(tokens):
        if tok == "-m" and i + 1 < len(tokens):
            marker = tokens[i + 1]
        elif tok.startswith("-m") and not tok.startswith("--") and len(tok) > 2:
            marker = tok[2:].lstrip("=")
    return marker == "docker"


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
    return _in_manager_dir(job, step, line) and not _only_unit(line)


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


def _report() -> str:
    m = re.search(r"--junitxml[= ](\S+)", _exam_line())
    assert m, "the exam run writes no junit report"
    return m.group(1)


def _gate_re() -> re.Pattern:
    return re.compile(r"^python3?\s+(-I\s+)?\S*scripts/ci/junit_gate\.py\s+"
                      + re.escape(_report()) + r"$")


def _gate_steps(job: dict) -> list[dict]:
    """Steps with a command line that is the gate call on the exam's own report."""
    pattern = _gate_re()
    return [s for s in job.get("steps", [])
            if any(pattern.match(ln) for ln in _logical_lines(s.get("run", "")))]


def _gate_line(step: dict) -> str:
    return next(ln for ln in _logical_lines(step["run"]) if _gate_re().match(ln))


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
    assert "strategy" not in job, "the exam job must not be a matrix"


def test_the_docker_job_still_runs_its_own_docker_exam():
    lines = [ln for ln in _pytest_lines(_jobs()["docker"])
             if "tests/acceptance" in ln and "-m docker" in ln]
    assert len(lines) == 1 and "--junitxml=docker-acceptance.xml" in lines[0]


# 2. the no-skip guarantee stays

def test_the_exam_run_keeps_confcutdir_and_clears_addopts():
    line = _exam_line()
    assert "--confcutdir=tests" in line
    assert re.search(r"-o addopts=(\"\"|'')", line)


def _check_reusable_gate(job: dict) -> None:
    """The pinned junit-gate.yml call (its `gate` job judges the report), no `if`, and the
    `junit-file` input equal to the exam's report path relative to the repo root."""
    assert REUSABLE_GATE.match(job["_uses"]), job["_uses"]
    tooling = re.findall(r"git\+https://github\.com/Shashkist/factory@([0-9a-f]{40})",
                         CI.read_text(encoding="utf-8"))
    assert tooling and job["_uses"].rsplit("@", 1)[1] == tooling[0], "pin differs from tooling's"
    autoload = job["_with"].get("disable-pytest-plugin-autoload", True)
    assert str(autoload).lower() != "false", "plugin autoload must stay disabled"
    assert "if" not in job, "the exam call has an if condition"
    assert "continue-on-error" not in job
    cmd = [s for s in job["steps"] if _exam_line() == s["run"]][0]
    base = cmd.get("working-directory") or ""
    reported = posixpath.normpath(posixpath.join(base, _report()))
    declared = posixpath.normpath(str(job["_with"].get("junit-file", "acceptance.xml")))
    assert reported == declared, (reported, declared)


def test_the_exam_run_writes_a_junit_report_and_the_gate_reads_that_same_report():
    _, job = _exam_job()
    if _reusable(job):
        _check_reusable_gate(job)
        return
    gates = _gate_steps(job)
    assert len(gates) == 1, f"want one gate call on {_report()}, as a command of its own"
    assert "scripts/ci/junit_gate.py" in _gate_line(gates[0])


def test_the_exam_run_is_not_filtered_by_any_other_option():
    line = re.sub(r"\s--junitxml[= ]\S+", "", _exam_line())
    args = shlex.split(line)
    assert args == EXAM_PREFIX + EXAM_ARGS, args


def test_the_exam_step_holds_only_the_exam_and_the_gate():
    """Allow-list: the exam step is the exam line (plus the gate line when they share a step);
    a separate gate step is the gate line alone and comes after the exam step."""
    _, job = _exam_job()
    step = _exam_step()
    if _reusable(job):
        lines = [s["run"] for s in job["steps"]]
        assert lines[0] == "set -euo pipefail", "the exam command must fail fast"
        bad = [ln for ln in lines if not any(re.fullmatch(p, ln) for p in REUSABLE_LINES)]
        assert not bad, f"lines outside the allow-list: {bad}"
        assert lines.count(_exam_line()) == 1 and lines[-1] == _exam_line()
        return
    gates = _gate_steps(job)
    assert len(gates) == 1
    lines = _logical_lines(step["run"])
    if gates[0] == step:
        assert lines == [_exam_line(), _gate_line(step)], lines
    else:
        assert lines == [_exam_line()], lines
        assert _logical_lines(gates[0]["run"]) == [_gate_line(gates[0])]
        assert job["steps"].index(gates[0]) > job["steps"].index(step)


def test_the_exam_job_and_steps_cannot_fail_or_skip_softly():
    _, job = _exam_job()
    if _reusable(job):
        _check_reusable_gate(job)
        assert all("||" not in s["run"] for s in job["steps"]), "a command may fail softly"
        assert job["steps"][0]["run"] == "set -euo pipefail"
        return
    gates = _gate_steps(job)
    assert len(gates) == 1
    checked = [("job", job), ("exam step", _exam_step()), ("gate step", gates[0])]
    for what, item in checked:
        assert "continue-on-error" not in item, f"{what} has continue-on-error"
        assert "if" not in item, f"{what} has an if condition"


def test_no_shell_override_can_drop_the_fail_fast_flag():
    _, job = _exam_job()
    if _reusable(job):
        assert "defaults" not in job and "shell" not in job["_with"], "the exam call sets a shell"
        assert job["steps"][0]["run"] == "set -euo pipefail"
        steps = [_exam_step()]
    else:
        steps = [_exam_step(), _gate_steps(job)[0]]
    for step in steps:
        assert "shell" not in step, "a step overrides the shell"
    assert "shell" not in job.get("defaults", {}).get("run", {}), "the exam job sets a shell"
    assert "shell" not in (_wf().get("defaults") or {}).get("run", {}), "the workflow sets a shell"


def test_no_pytest_environment_variable_can_change_the_exam_run():
    _, job = _exam_job()
    scopes = [("workflow", _wf()), ("exam job", job), ("exam step", _exam_step())]
    if not _reusable(job):
        scopes.append(("gate step", _gate_steps(job)[0]))
    else:
        runs = (s["run"] for s in job["steps"])
        bad = [ln for ln in runs if re.search(r"PYTEST_\w+=", ln)]
        assert not bad, f"the exam command sets {bad}"
    for what, scope in scopes:
        env = scope.get("env") or {}
        bad = [k for k in env if str(k).upper().startswith("PYTEST")]
        assert not bad, f"{what} sets {bad}"


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
    if _reusable(job):
        # no services: in a reusable call; the command starts postgres:16 and waits for it
        lines = [s["run"] for s in job["steps"]]
        starts = [i for i, ln in enumerate(lines)
                  if ln.startswith("docker run") and "postgres:16" in ln]
        waits = [i for i, ln in enumerate(lines) if "pg_isready" in ln]
        exam = lines.index(_exam_line())
        assert starts and waits and starts[0] < max(waits) < exam, lines
        env = [i for i, ln in enumerate(lines)
               if ln.startswith("export LF_TEST_DATABASE_URL=postgresql://")]
        assert env and env[0] < exam, "LF_TEST_DATABASE_URL must be set before the exam"
        assert job["steps"][exam].get("working-directory") == "labs/manager"
        return
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
