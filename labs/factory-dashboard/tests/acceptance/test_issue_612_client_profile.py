"""Exam for #612: Learning Factory is client #1 of the factory (Shashkist/factory#7).

OFFLINE. Reads `.factory/factory.yaml`, `CLAUDE.md` and `.github/workflows/ci.yml` and checks
the facts: Learning Factory's real values (not the example fixture's 6/3/4), required checks
that exist in ci.yml, the verify commands of CLAUDE.md, the "Factory facts" section, and the
pinned `factory-tools profile check` step in the `tooling` job.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
PROFILE = ROOT / ".factory" / "factory.yaml"
CLAUDE_MD = ROOT / "CLAUDE.md"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture
def profile():
    assert PROFILE.is_file(), ".factory/factory.yaml does not exist"
    data = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "the profile must be a YAML mapping"
    return data


@pytest.fixture(scope="module")
def ci():
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))


def _facts_section():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    m = re.search(r"^#+[^\n]*Factory facts[^\n]*\n(.*?)(?=^#{1,2} |\Z)", text, re.M | re.S | re.I)
    assert m, 'CLAUDE.md has no section whose heading contains "Factory facts"'
    return m.group(1)


def test_profile_exists_parses_and_sets_methodology(profile):
    assert profile.get("methodology"), "methodology must be set"


def test_limits_are_learning_factorys_real_values(profile):
    limits = profile.get("limits")
    assert limits == {"ci_rounds": 5, "review_rounds": 2, "parallel_issues": 3}


def test_required_checks_exist_in_ci_yml(profile, ci):
    checks = profile.get("required_checks")
    assert isinstance(checks, list) and checks
    names = set()
    for job_id, job in ci["jobs"].items():
        names.add(job_id)
        if isinstance(job, dict) and job.get("name"):
            names.add(job["name"])
    missing = [c for c in checks if c not in names]
    assert not missing, f"required_checks not found as jobs in ci.yml: {missing}"
    assert "manager-exam" in checks
    assert "exam-integrity" in checks


def _commands(profile, area):
    block = (profile.get("verify") or {}).get(area)
    assert isinstance(block, dict), f"verify.{area} missing"
    run = block.get("run")
    return Path(str(block.get("cwd", "."))).as_posix().strip("/"), list(run or [])


def test_verify_manager_commands_match_claude_md(profile):
    cwd, run = _commands(profile, "manager")
    assert cwd == "labs/manager"
    for cmd in ("uv run pytest -q", "uv run ruff check .", "uv run mypy app"):
        assert cmd in run


def test_verify_web_commands_match_claude_md(profile):
    cwd, run = _commands(profile, "web")
    assert cwd == "labs/web"
    for cmd in ("npm test -- --run", "npx tsc --noEmit", "npm run lint", "npm run build"):
        assert cmd in run


def test_exam_globs_include_acceptance_tests(profile):
    assert "labs/**/tests/acceptance/**" in (profile.get("exam") or [])


def test_smoke_workflow_is_server_smoke_and_exists(profile):
    assert (profile.get("smoke") or {}).get("workflow") == "server-smoke.yml"
    assert (ROOT / ".github" / "workflows" / "server-smoke.yml").is_file()


def test_planning_spec_points_at_an_existing_file(profile):
    spec = (profile.get("planning") or {}).get("spec")
    assert spec, "planning.spec must be set"
    assert (ROOT / spec).is_file(), f"planning.spec {spec} does not exist"


def test_claude_md_factory_facts_state_commit_recipe():
    s = _facts_section()
    assert "git pull --rebase origin main && git push origin HEAD:main" in s
    assert "chore(factory): state" in s


def test_claude_md_factory_facts_worktrees():
    s = _facts_section()
    assert "git worktree add" in s
    assert "factory" in s and "branch" in s
    assert "C:\\projects\\lf-<n>" in s
    assert "C:\\projects\\factory-<n>" in s


def test_claude_md_factory_facts_deploy_watch_and_smoke_target():
    s = _facts_section()
    assert re.search(r"deploy", s, re.I) and re.search(r"watch", s, re.I)
    assert "Deploy" in s, "name the Deploy workflow"
    assert "labs.shashkist.com" in s
    assert "Host:" in s


def test_tooling_job_runs_profile_check_pinned_by_sha():
    text = CI_YML.read_text(encoding="utf-8")
    ci = yaml.safe_load(text)
    steps = ci["jobs"]["tooling"]["steps"]
    assert any("factory-tools profile check" in str(s.get("run", "")) for s in steps), (
        "the tooling job must run `factory-tools profile check`"
    )
    pinned = re.search(
        r"Shashkist/factory(?:\.git)?@([0-9a-f]{40})\b[^\n]*#\s*v\d+\.\d+\.\d+", text
    )
    assert pinned, "Shashkist/factory must be pinned by a 40-hex SHA with a `# v<semver>` comment"
