"""Exam for #613 (D165): Learning Factory's CI calls the factory's reusable workflows.

OFFLINE and static. Reads .github/workflows/*.yml, .factory/factory.yaml and `git ls-files`,
and drives scripts/merge_gate.py through its `main(argv, run=fake_gh)` entry point.

The factory's matcher (Shashkist/factory docs/ci.md) is written here from its description, not
imported: `exam-paths` globs are fnmatch style against the repo-relative path, `*` crosses `/`,
and conftest.py, pytest.ini, .pytest.ini, tox.ini and setup.cfg always count as exam, except
inside a `tests/unit/` directory. Its verdict must equal scripts/ci/exam_integrity.py::_is_exam.
"""

import importlib.util
import json
import posixpath
import re
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
WORKFLOWS = ROOT / ".github" / "workflows"
CI_YML = WORKFLOWS / "ci.yml"
INCIDENT_YML = WORKFLOWS / "incident.yml"
PROFILE = ROOT / ".factory" / "factory.yaml"

FACTORY = "Shashkist/factory/.github/workflows/"
PIN_LINE = re.compile(
    r"uses:\s*(Shashkist/factory/\.github/workflows/([\w.-]+\.yml))@([0-9a-f]{40})"
    r"[ \t]+#[ \t]*(v\d+\.\d+\.\d+\S*)[ \t]*$",
    re.M,
)
PR_IF = "github.event_name == 'pull_request'"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


issue_lock = _load("issue_lock")
merge_gate = _load("merge_gate")
import exam_integrity  # noqa: E402


def _yaml(path):
    assert path.is_file(), f"{path} does not exist"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _jobs(path):
    return _yaml(path)["jobs"]


def _norm_if(value):
    s = str(value).strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", s, re.S)
    return " ".join((m.group(1) if m else s).split())


def _pin(path, job_id, workflow):
    """(sha, version) of the reusable call in `job_id`, read from the raw text so the
    `# v<semver>` comment is checked on the very line of the `uses:`."""
    job = _jobs(path).get(job_id)
    assert job is not None, f"{path.name} has no job {job_id}"
    uses = job.get("uses", "")
    assert uses.startswith(FACTORY + workflow + "@"), f"job {job_id} must call {workflow}: {uses!r}"
    lines = [m for m in PIN_LINE.finditer(path.read_text(encoding="utf-8"))
             if m.group(2) == workflow and m.group(1) + "@" + m.group(3) == uses]
    assert lines, f"{uses} needs a `# v<semver>` comment on the same line and a 40-hex sha"
    return lines[0].group(3), lines[0].group(4)


def _all_pins():
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yml")))
    pat = r"Shashkist/factory/\.github/workflows/[\w.-]+\.yml@([0-9a-zA-Z._-]+)"
    return set(re.findall(pat, text))


# 1. exam-integrity call

def test_exam_integrity_job_calls_the_reusable_workflow_pinned_with_a_version_comment():
    sha, version = _pin(CI_YML, "exam-integrity", "exam-integrity.yml")
    assert re.fullmatch(r"[0-9a-f]{40}", sha) and version.startswith("v")


def test_exam_integrity_job_condition_is_exactly_the_pull_request_condition():
    job = _jobs(CI_YML)["exam-integrity"]
    assert _norm_if(job.get("if", "")) == PR_IF


def test_exam_integrity_job_passes_exam_paths_and_ci_paths():
    with_ = _jobs(CI_YML)["exam-integrity"].get("with") or {}
    assert str(with_.get("exam-paths", "")).split(), "exam-paths missing"
    assert str(with_.get("ci-paths", "")).split(), "ci-paths missing"


def test_ci_paths_include_the_ci_workflow_and_the_ci_scripts():
    with_ = _jobs(CI_YML)["exam-integrity"].get("with") or {}
    ci_paths = str(with_.get("ci-paths", "")).split()
    assert ".github/workflows/ci.yml" in ci_paths
    assert "scripts/ci/**" in ci_paths


# 2. exam-paths give the same verdict as _is_exam

PYTEST_CONFIG = {"conftest.py", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"}


def _in_unit_dir(path):
    parts = path.split("/")[:-1]
    return any(a == "tests" and b == "unit" for a, b in zip(parts, parts[1:], strict=False))


def factory_is_exam(path, globs):
    if path.rsplit("/", 1)[-1] in PYTEST_CONFIG and not _in_unit_dir(path):
        return True
    return any(fnmatchcase(path, g) for g in globs)


EDGE_PATHS = [
    "labs/manager/tests/integration/x.py",
    "labs/manager/tests/conftest.py",
    "labs/manager/tests/unit/conftest.py",
    "labs/manager/tests/unit/test_a.py",
    "labs/manager/tests/unit/deep/test_a.py",
    "labs/manager/tests/unit/pytest.ini",
    "labs/manager/tests/unit_extra/test_a.py",
    "labs/manager/tests/test_top.py",
    "labs/manager/tests/acceptance/test_a.py",
    "labs/manager/tests/acceptance/conftest.py",
    "labs/web/tests/acceptance/a.spec.ts",
    "labs/web/tests/e2e/a.spec.ts",
    "labs/web/tests/unit/a.ts",
    "labs/manager/app/tests_helper.py",
    "labs/manager/app/main.py",
    "labs/manager/conftest.py",
    "labs/manager/pytest.ini",
    "labs/manager/tox.ini",
    "labs/manager/setup.cfg",
    "labs/web/src/conftest.py",
    "labs/manager/app/tests/helper.py",
    "labs/manager/tests_data/x.py",
    "labs/images/term/tests/x.sh",
    "labs/factory-dashboard/tests/acceptance/test_x.py",
    "labs/factory-dashboard/tests/unit/test_x.py",
    "labs/factory-dashboard/conftest.py",
    "scripts/ci/x.py",
    "scripts/ci/tests/test_x.py",
    "scripts/conftest.py",
    "tests/x.py",
    "tests/acceptance/x.py",
    "conftest.py",
    "labs/README.md",
    ".github/workflows/ci.yml",
]


def _tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True,
                         text=True).stdout
    return [p for p in out.split("\n") if p]


def _exam_globs():
    with_ = _jobs(CI_YML)["exam-integrity"].get("with") or {}
    globs = str(with_.get("exam-paths", "")).split()
    assert globs, "exam-paths missing"
    return globs


def test_exam_paths_match_exam_integrity_on_every_tracked_path():
    globs = _exam_globs()
    bad = [p for p in _tracked()
           if factory_is_exam(p, globs) != exam_integrity._is_exam(p)]
    assert not bad, f"factory and exam_integrity.py disagree on: {bad[:15]}"


@pytest.mark.parametrize("path", EDGE_PATHS)
def test_exam_paths_match_exam_integrity_on_edge_path(path):
    globs = _exam_globs()
    assert factory_is_exam(path, globs) == exam_integrity._is_exam(path), path


# 3. manager exam through junit-gate.yml

def _manager_exam():
    job = _jobs(CI_YML).get("manager-exam")
    assert job is not None, "ci.yml has no manager-exam job"
    return job


def test_manager_exam_calls_junit_gate_at_the_same_pin_and_comment():
    sha, version = _pin(CI_YML, "manager-exam", "junit-gate.yml")
    assert (sha, version) == _pin(CI_YML, "exam-integrity", "exam-integrity.yml")


def test_manager_exam_has_no_condition_that_could_skip_it_on_a_pull_request():
    assert "if" not in _manager_exam()


def _command():
    with_ = _manager_exam().get("with") or {}
    cmd = with_.get("test-command")
    assert cmd, "manager-exam passes no test-command"
    return str(cmd)


def test_test_command_starts_postgres_16_and_waits_for_pg_isready_before_pytest():
    cmd = _command()
    i_run = cmd.find("docker run")
    i_ready = cmd.find("pg_isready")
    i_pytest = cmd.find("pytest")
    assert i_run >= 0 and "postgres:16" in cmd, "must docker run postgres:16"
    assert i_ready > i_run, "must wait with pg_isready after starting postgres"
    assert i_pytest > i_ready, "pytest must run after the wait"


def test_test_command_points_the_exam_at_that_database():
    cmd = _command()
    assert "LF_TEST_DATABASE_URL" in cmd and "5432" in cmd


def test_test_command_runs_the_manager_acceptance_exam_with_the_strict_flags():
    cmd = _command()
    assert "tests/acceptance" in cmd
    assert "--confcutdir=tests" in cmd
    assert re.search(r"-o\s+addopts=(\"\"|'')", cmd), "-o addopts must be emptied"
    assert re.search(r"-m\s+[\"']not docker[\"']", cmd), "-m not docker missing"
    assert "labs/manager" in cmd, "the command runs from the repo root and must reach labs/manager"


def test_junitxml_path_equals_the_junit_file_input_relative_to_the_repo_root():
    cmd = _command()
    m = re.search(r"--junitxml[= ]([^\s\"']+)", cmd)
    assert m, "--junitxml missing"
    moved = re.search(r"cd\s+labs/manager|--directory[ =]labs/manager", cmd)
    base = "labs/manager" if moved else ""
    reported = posixpath.normpath(posixpath.join(base, m.group(1)))
    declared = (_manager_exam().get("with") or {}).get("junit-file", "acceptance.xml")
    assert reported == posixpath.normpath(str(declared))


def test_manager_exam_time_limit_is_at_least_25_minutes():
    value = (_manager_exam().get("with") or {}).get("timeout-minutes")
    assert value is not None and int(value) >= 25


def test_manager_exam_keeps_plugin_autoload_disabled():
    value = (_manager_exam().get("with") or {}).get("disable-pytest-plugin-autoload", True)
    assert str(value).lower() != "false"


# 4. docker exam keeps its own no-skip step

def test_docker_job_still_runs_the_docker_exam_and_the_no_skip_gate_on_its_report():
    docker = _jobs(CI_YML)["docker"]
    runs = "\n".join(s.get("run", "") for s in docker["steps"])
    m = re.search(r"-m docker\b.*--junitxml=(\S+)", runs)
    assert m, "docker exam with --junitxml missing"
    assert re.search(r"scripts/ci/junit_gate\.py\s+" + re.escape(m.group(1)), runs)


# 5. incident

TRIGGERS = ["CI", "Dashboard CI", "Factory dashboard", "Deploy", "Server smoke", "Server bootstrap"]


def _incident_jobs():
    doc = _yaml(INCIDENT_YML)
    return doc, doc["jobs"]


def _call_job():
    _, jobs = _incident_jobs()
    calls = {k: j for k, j in jobs.items()
             if str(j.get("uses", "")).startswith(FACTORY + "incident.yml@")}
    assert len(calls) == 1, "incident.yml needs exactly one job calling the reusable incident.yml"
    return next(iter(calls.items()))


def test_incident_keeps_its_workflow_run_triggers():
    doc, _ = _incident_jobs()
    on = doc.get("on", doc.get(True))
    assert on["workflow_run"]["workflows"] == TRIGGERS
    assert on["workflow_run"]["types"] == ["completed"]


def test_incident_calls_the_reusable_workflow_at_the_same_pin_and_comment():
    job_id, _ = _call_job()
    expected = _pin(CI_YML, "exam-integrity", "exam-integrity.yml")
    assert _pin(INCIDENT_YML, job_id, "incident.yml") == expected


def test_incident_call_has_issues_write_and_the_three_inputs():
    _, job = _call_job()
    assert (job.get("permissions") or {}).get("issues") == "write"
    with_ = job.get("with") or {}
    want = {"workflow-name": "github.event.workflow_run.name",
            "run-url": "github.event.workflow_run.html_url",
            "conclusion": "github.event.workflow_run.conclusion"}
    for key, expr in want.items():
        assert expr in str(with_.get(key, "")), f"{key} must come from {expr}"


def test_incident_decision_stays_in_a_job_the_call_needs():
    _, call = _call_job()
    _, jobs = _incident_jobs()
    needs = call.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    deciders = [n for n in needs if "incident_gate.py" in json.dumps(jobs.get(n, {}))]
    assert deciders, "the call must need the job that runs scripts/incident_gate.py"


def test_incident_call_fails_open_when_the_decision_is_empty():
    _, call = _call_job()
    cond = _norm_if(call.get("if", ""))
    assert "'raise'" in cond and "''" in cond and "needs." in cond and "outputs." in cond, cond
    assert "always()" in cond or "!cancelled()" in cond, "a failed decide must not skip the call"


# 6. tooling job

def test_tooling_job_lints_and_tests_the_ci_scripts():
    steps = _jobs(CI_YML)["tooling"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "ruff check scripts/ci" in runs
    assert "pytest scripts/ci" in runs


# 7. merge gate default exam gate

SESSION = "lf-pass-1"
HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
NEW_GATE = "manager-exam / gate"
WRITES = {"merge", "comment", "review", "edit", "close", "reopen", "ready"}


def check(name, conclusion="SUCCESS", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def rollup(*exam_checks):
    base = [check("manager"), check("web"), check("tooling"), check("changes"),
            check("docker", "SKIPPED"), check("exam-integrity / exam-integrity")]
    return base + list(exam_checks)


class Gh:
    def __init__(self, checks):
        now = issue_lock.now()
        claim = issue_lock.render({"session": SESSION, "branch": "f/5-x", "worktree": "C:/wt",
                                   "claimed": now, "heartbeat": now, "step": "ship"})
        self.claim = {"body": claim, "url": "https://github.com/o/r/issues/5#issuecomment-77"}
        self.files = ["labs/manager/app/thing.py"]
        self.pr = {
            "author": {"login": "Shashkist"},
            "body": "What.\n\nCloses #5\n",
            "headRefOid": HEAD,
            "commits": [{"oid": HEAD}],
            "statusCheckRollup": checks,
            "files": [{"path": f} for f in self.files],
            "comments": [{"author": {"login": "Shashkist"},
                          "body": "Review round 1: APPROVE at " + HEAD + "\n\nOk.",
                          "createdAt": "2026-11-24T10:00:00Z"}],
        }

    def __call__(self, argv):
        assert argv[0] not in WRITES and argv[:2] != ["pr", "merge"], "gate must be read-only"
        if argv[:2] == ["pr", "view"]:
            return json.dumps(self.pr)
        if argv[:2] == ["pr", "diff"]:
            return "\n".join(self.files) + "\n"
        if argv[0] == "api":
            path = next((a for a in argv[1:] if a.startswith("repos/")), "").split("?")[0]
            if path.endswith("/pulls/9"):
                return json.dumps({"commits": 1})
            if path.endswith("/pulls/9/commits"):
                page = [{"sha": HEAD}]
                return json.dumps([page]) if "--slurp" in argv else json.dumps(page)
            files = [{"filename": f, "status": "modified"} for f in self.files]
            return json.dumps({"files": files})
        if argv[:2] == ["issue", "view"]:
            return json.dumps({"comments": [self.claim]})
        return ""


def gate(capsys, checks):
    code = merge_gate.main(["9", "--session", SESSION], run=Gh(checks))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, lines


def test_default_gate_merges_with_manager_exam_gate_and_reusable_exam_integrity(capsys):
    assert gate(capsys, rollup(check("manager-exam / exam"), check(NEW_GATE))) == (0, ["MERGE"])


@pytest.mark.parametrize("state", ["SKIPPED", "FAILURE"])
def test_default_gate_holds_when_manager_exam_gate_is_not_success(capsys, state):
    code, lines = gate(capsys, rollup(check("manager-exam / exam"), check(NEW_GATE, state)))
    assert code == 1 and "MERGE" not in lines, lines
    assert any(ln.startswith("HOLD: ") and NEW_GATE in ln for ln in lines), lines


def test_default_gate_holds_when_manager_exam_gate_is_missing(capsys):
    code, lines = gate(capsys, rollup(check("manager-exam / exam")))
    assert code == 1 and "MERGE" not in lines, lines
    assert any(ln.startswith("HOLD: ") and NEW_GATE in ln for ln in lines), lines


def test_default_gate_holds_for_a_bare_manager_exam_check(capsys):
    code, lines = gate(capsys, rollup(check("manager-exam")))
    assert code == 1 and "MERGE" not in lines, lines
    assert any(ln.startswith("HOLD: ") and NEW_GATE in ln for ln in lines), lines


# 8. profile

def test_required_checks_name_the_reusable_checks_not_the_bare_jobs():
    checks = _yaml(PROFILE)["required_checks"]
    assert NEW_GATE in checks
    assert "exam-integrity / exam-integrity" in checks
    assert "manager-exam" not in checks
    assert "exam-integrity" not in checks


# 9. one pin everywhere

def test_every_factory_reference_uses_one_and_the_same_sha():
    shas = _all_pins()
    tooling = re.findall(r"git\+https://github\.com/Shashkist/factory@([0-9a-zA-Z._-]+)",
                         CI_YML.read_text(encoding="utf-8"))
    assert tooling, "the tooling job's factory-tools pin is gone"
    assert len(shas) >= 3, f"expected exam-integrity, junit-gate and incident calls: {shas}"
    assert len(shas | set(tooling)) == 1, shas | set(tooling)
    assert all(re.fullmatch(r"[0-9a-f]{40}", s) for s in shas)
