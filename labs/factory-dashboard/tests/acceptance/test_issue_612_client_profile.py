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


# jobs of the factory reusable workflows at v0.1.0 (docs/ci.md of Shashkist/factory)
REUSABLE_JOBS = {
    "exam-integrity.yml": ["exam-integrity"],
    "junit-gate.yml": ["exam", "gate"],
    "incident.yml": ["incident"],
}


def _called_jobs(job):
    uses = job.get("uses", "") if isinstance(job, dict) else ""
    m = re.fullmatch(r"Shashkist/factory/\.github/workflows/([\w.-]+\.yml)@[0-9a-f]{40}", uses)
    return REUSABLE_JOBS.get(m.group(1), []) if m else []


def test_required_checks_exist_in_ci_yml(profile, ci):
    checks = profile.get("required_checks")
    assert isinstance(checks, list) and checks
    names = set()
    for job_id, job in ci["jobs"].items():
        names.add(job_id)
        if isinstance(job, dict) and job.get("name"):
            names.add(job["name"])
        # D166: a caller job (`uses:`) of a factory reusable workflow also reports one check
        # per job of the called workflow, named `<caller job> / <called job>`
        called = _called_jobs(job)
        names.update(f"{job_id} / {c}" for c in called)
    missing = [c for c in checks if c not in names]
    assert not missing, f"required_checks not found as jobs in ci.yml: {missing}"
    # the exam must stay required, as the bare job or as the reusable job's checks
    assert any(c == "manager-exam" or c.startswith("manager-exam / ") for c in checks)
    assert any(c == "exam-integrity" or c.startswith("exam-integrity / ") for c in checks)


def _commands(profile, area):
    block = (profile.get("verify") or {}).get(area)
    assert isinstance(block, dict), f"verify.{area} missing"
    run = block.get("run")
    return Path(str(block.get("cwd", "."))).as_posix().strip("/"), list(run or [])


def _claude_verify_commands():
    """Parse the 'Verification commands' section of CLAUDE.md: {cwd: [commands]}."""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    m = re.search(r"^## Verification commands[^\n]*\n(.*?)(?=^#{1,2} |\Z)", text, re.M | re.S)
    assert m, "CLAUDE.md has no Verification commands section"
    result = {}
    for line in m.group(1).splitlines():
        if not line.startswith("From "):
            continue
        head, _, rest = line.partition(": ")
        if "repository root" in head:
            cwd = "."
        else:
            cwd_m = re.match(r"From `([^`]+)`", head)
            assert cwd_m, f"cannot read the directory in: {line}"
            cwd = cwd_m.group(1).strip("/")
        result[cwd] = re.findall(r"`([^`]+)`", rest)
    return result


@pytest.mark.parametrize(
    "area,cwd",
    [
        ("manager", "labs/manager"),
        ("web", "labs/web"),
        ("tooling", "."),
        ("dashboard", "labs/factory-dashboard"),
    ],
)
def test_verify_area_matches_claude_md_verification_commands(profile, area, cwd):
    got_cwd, run = _commands(profile, area)
    assert got_cwd == cwd
    documented = _claude_verify_commands()
    assert cwd in documented, f"CLAUDE.md Verification commands has no entry for {cwd}"
    assert run == documented[cwd], f"verify.{area} differs from CLAUDE.md"


def test_verify_exact_commands(profile):
    expected = {
        "manager": ("labs/manager", ["uv run pytest -q", "uv run ruff check .", "uv run mypy app"]),
        "web": (
            "labs/web",
            ["npm test -- --run", "npx tsc --noEmit", "npm run lint", "npm run build"],
        ),
        "tooling": (".", ["uvx pytest scripts -q"]),
        "dashboard": ("labs/factory-dashboard", ["uv run ruff check .", "uv run pytest -q"]),
    }
    for area, (cwd, cmds) in expected.items():
        assert _commands(profile, area) == (cwd, cmds), f"verify.{area}"


def test_exam_globs_are_the_five_claude_md_paths(profile):
    globs = profile.get("exam") or []
    for g in (
        "labs/**/tests/acceptance/**",
        "labs/**/conftest.py",
        "labs/**/pytest.ini",
        "labs/**/tox.ini",
        "labs/**/setup.cfg",
    ):
        assert g in globs, f"exam glob missing: {g}"


def test_smoke_workflow_is_server_smoke_and_exists(profile):
    assert (profile.get("smoke") or {}).get("workflow") == "server-smoke.yml"
    assert (ROOT / ".github" / "workflows" / "server-smoke.yml").is_file()


def test_planning_spec_points_at_an_existing_file_named_by_claude_md(profile):
    spec = (profile.get("planning") or {}).get("spec")
    assert spec == "docs/superpowers/specs/2026-11-14-lab-platform-design.md"
    assert (ROOT / spec).is_file(), f"planning.spec {spec} does not exist"
    assert spec in CLAUDE_MD.read_text(encoding="utf-8"), "CLAUDE.md must name the planning spec"


def test_claude_md_factory_facts_state_commit_recipe():
    s = _facts_section()
    assert "git pull --rebase origin main && git push origin HEAD:main" in s
    assert "chore(factory): state" in s


def test_claude_md_factory_facts_worktrees():
    s = _facts_section()
    assert "git worktree add -B factory" in s
    assert "origin/main" in s
    assert "C:\\projects\\lf-<n>" in s
    assert "C:\\projects\\factory-<n>" in s


def test_claude_md_factory_facts_independent_areas_with_examples():
    s = _facts_section()
    m = re.search(r"Independent areas.*?(?=\n- |\Z)", s, re.I | re.S)
    assert m, "Factory facts must state the independent-areas fact"
    bullet = m.group(0)
    for area in ("labs/content", "labs/manager", ".github"):
        assert area in bullet, f"independent-areas example missing: {area}"


def test_claude_md_factory_facts_deploy_watch_and_smoke_target():
    s = _facts_section()
    m = re.search(r"Deploy watch[^\n]*", s, re.I)
    assert m, "Factory facts must state the deploy watch"
    assert "deploy.yml" in m.group(0)
    assert "https://labs.shashkist.com" in s
    assert "`Host: labs.shashkist.com`" in s


def test_tooling_job_runs_profile_check_pinned_by_sha():
    text = CI_YML.read_text(encoding="utf-8")
    ci = yaml.safe_load(text)
    job = ci["jobs"]["tooling"]
    assert not job.get("continue-on-error"), "the tooling job must not continue on error"
    assert "if" not in job, "the tooling job must not be conditional (no `if:`)"
    steps = [s for s in job["steps"] if "factory-tools profile check" in str(s.get("run", ""))]
    assert len(steps) == 1, "exactly one tooling step must run `factory-tools profile check`"
    step = steps[0]
    assert "continue-on-error" not in step, "the profile check step must not continue on error"
    assert "if" not in step, "the profile check step must not be conditional (no `if:`)"
    run = str(step["run"]).strip()
    assert re.fullmatch(
        r"uvx --from git\+https://github\.com/Shashkist/factory@[0-9a-f]{40} "
        r"factory-tools profile check \.factory/factory\.yaml",
        run,
    ), "the step must be exactly the pinned `uvx --from ... factory-tools profile check` command"
    assert re.search(r"factory-tools profile check \.factory/factory\.yaml$", run), (
        "the check must target .factory/factory.yaml"
    )
    assert re.search(r"git\+https://github\.com/Shashkist/factory@[0-9a-f]{40}\s", run + " "), (
        "Shashkist/factory must be pinned by a 40-hex SHA in the step"
    )
    # YAML drops comments, so the `# v<semver>` comment is read from the raw line of the step.
    block = re.search(r"^  tooling:[ ]*$(.*?)(?=^  \S[^\n]*:[ ]*$|\Z)", text, re.M | re.S)
    assert block, "ci.yml has no tooling job block"
    raw = [ln for ln in block.group(1).splitlines() if "factory-tools profile check" in ln]
    assert len(raw) == 1
    assert re.search(
        r"git\+https://github\.com/Shashkist/factory@[0-9a-f]{40}\s.*#\s*v\d+\.\d+\.\d+\s*$",
        raw[0],
    ), "the pin line needs a `# v<semver>` comment on the same line"
