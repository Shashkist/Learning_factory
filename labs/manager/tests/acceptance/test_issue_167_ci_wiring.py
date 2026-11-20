"""Acceptance tests for issue #167, non-docker part: how ci.yml wires the browser flow.

The browser exam (test_issue_167_browser_e2e_docker.py) needs Node, the installed npm packages,
the built web app and Chromium in the `docker` job, and only there: no other job pays for it, and
the web job keeps its own fake-backend suite. Parses ci.yml and the exam files; no Docker.
"""
import re
from pathlib import Path

import yaml

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
WEB_ACCEPTANCE = REPO_ROOT / "labs" / "web" / "tests" / "acceptance"
DOCKER_EXAM = Path(__file__).resolve().parent / "test_issue_167_browser_e2e_docker.py"
WEB = "labs/web"
BROWSER_RE = re.compile(r"(?i)\b(firefox|webkit|msedge|chrome\b)")


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _steps(job: str) -> list[dict]:
    return _jobs()[job]["steps"]


def _where(step: dict, job: str) -> str:
    default = (_jobs()[job].get("defaults") or {}).get("run", {}).get("working-directory")
    return step.get("working-directory") or default or "."


def _index(job: str, pred) -> int:
    for i, step in enumerate(_steps(job)):
        if pred(step):
            return i
    return -1


def _run(step: dict) -> str:
    return step.get("run") or ""


def _is_node_setup(step: dict) -> bool:
    return str(step.get("uses", "")).startswith("actions/setup-node@")


def _is_install(step: dict) -> bool:
    return re.search(r"\bnpm ci\b", _run(step)) is not None


def _is_build(step: dict) -> bool:
    return re.search(r"\bnpm run build\b", _run(step)) is not None


def _is_chromium(step: dict) -> bool:
    return re.search(r"\bplaywright install\b", _run(step)) is not None


def _is_gate(step: dict) -> bool:
    run = _run(step)
    return "tests/acceptance" in run and "-m docker" in run and "pytest" in run


def test_docker_job_sets_up_node_22_with_the_npm_cache():
    i = _index("docker", _is_node_setup)
    assert i >= 0, "the docker job has no actions/setup-node step"
    w = _steps("docker")[i]["with"]
    assert str(w["node-version"]) == "22", w
    assert w.get("cache") == "npm", w
    assert w.get("cache-dependency-path") == f"{WEB}/package-lock.json", w


def test_docker_job_runs_npm_ci_in_labs_web():
    i = _index("docker", _is_install)
    assert i >= 0, "the docker job never runs `npm ci`"
    step = _steps("docker")[i]
    assert _where(step, "docker") == WEB, f"npm ci runs in {_where(step, 'docker')}"


def test_docker_job_builds_the_web_app_in_labs_web():
    i = _index("docker", _is_build)
    assert i >= 0, "the docker job never runs `npm run build`"
    step = _steps("docker")[i]
    assert _where(step, "docker") == WEB, f"npm run build runs in {_where(step, 'docker')}"


def test_docker_job_installs_chromium_only_for_playwright():
    i = _index("docker", _is_chromium)
    assert i >= 0, "the docker job never installs a Playwright browser"
    step = _steps("docker")[i]
    assert _where(step, "docker") == WEB, f"playwright install runs in {_where(step, 'docker')}"
    cmd = _run(step).strip()
    m = re.fullmatch(r"npx playwright install((?: --with-deps| --only-shell)*) chromium", cmd)
    assert m, f"want `npx playwright install [--with-deps] chromium`, got: {cmd!r}"
    assert not BROWSER_RE.search(cmd), cmd


def test_node_chromium_and_build_steps_come_before_the_docker_gate():
    gate = _index("docker", _is_gate)
    assert gate >= 0, "the docker job has no `pytest tests/acceptance ... -m docker` gate step"
    for name, pred in [("setup-node", _is_node_setup), ("npm ci", _is_install),
                       ("npm run build", _is_build), ("playwright install", _is_chromium)]:
        i = _index("docker", pred)
        assert 0 <= i < gate, f"{name} (step {i}) must come before the docker gate (step {gate})"
    assert _index("docker", _is_install) < _index("docker", _is_build)
    assert _index("docker", _is_install) < _index("docker", _is_chromium)


def test_the_docker_gate_still_runs_every_docker_exam_and_the_junit_gate():
    step = _steps("docker")[_index("docker", _is_gate)]
    run = _run(step)
    assert "-m docker" in run and "--junitxml=docker-acceptance.xml" in run
    line = next(ln for ln in run.splitlines() if "uv run pytest" in ln)
    pytest_line = line[line.index("uv run pytest"):]  # not the `timeout -k 60` before it
    for flag in (" -k ", " -k=", "--deselect", "--ignore", "--lf ", "--collect-only"):
        msg = f"the gate must not narrow the run with {flag.strip()!r}"
        assert flag not in pytest_line + " ", msg
    assert "junit_gate.py docker-acceptance.xml" in run


def test_no_step_of_the_docker_job_may_fail_softly():
    job = _jobs()["docker"]
    assert job.get("continue-on-error") in (None, False)
    for step in job["steps"]:
        assert step.get("continue-on-error") in (None, False), step
        assert "|| true" not in _run(step), step


def test_docker_job_adds_no_other_web_work():
    """Few CI minutes: the fake-backend suite, lint and tests stay in the web job."""
    for step in _steps("docker"):
        run = _run(step)
        for bad in (r"npm run e2e", r"npm test", r"npm run lint", r"npx tsc", r"vite preview",
                    r"playwright test", r"npm install\b"):
            assert not re.search(bad, run), f"docker job step runs {bad!r}: {run!r}"


def test_no_job_other_than_web_and_docker_uses_node_or_a_browser():
    for name, job in _jobs().items():
        if name in ("web", "docker"):
            continue
        for step in job["steps"]:
            assert not _is_node_setup(step), f"job {name} sets up Node"
            run = _run(step)
            assert not re.search(r"\b(npm|npx|node)\b", run), f"job {name} runs: {run!r}"


def test_web_job_keeps_its_own_fake_backend_suite():
    runs = [_run(s) for s in _steps("web")]
    assert any("npm run e2e" in r for r in runs)
    assert any("playwright install" in r and "chromium" in r for r in runs)


def test_docker_job_timeout_still_covers_the_browser_flow():
    minutes = _jobs()["docker"]["timeout-minutes"]
    assert 20 <= int(minutes) <= 69, minutes


def test_the_browser_files_exist_where_the_docker_exam_expects_them():
    assert (WEB_ACCEPTANCE / "playwright.docker.config.ts").is_file()
    assert (WEB_ACCEPTANCE / "issue-167-docker-e2e.pw.ts").is_file()
    assert "pytestmark = pytest.mark.docker" in DOCKER_EXAM.read_text(encoding="utf-8")


def test_the_browser_config_starts_no_server_of_its_own():
    """The server under test is the real manager; a vite preview here would test a fake one."""
    text = (WEB_ACCEPTANCE / "playwright.docker.config.ts").read_text(encoding="utf-8")
    assert "webServer" not in text
    assert 'browserName: "chromium"' in text
    assert not BROWSER_RE.search(text.replace('browserName: "chromium"', ""))


def test_the_web_unit_test_runner_does_not_pick_up_the_browser_files():
    """vitest picks up *.test.* and *.spec.* files; the browser files must match neither."""
    for f in WEB_ACCEPTANCE.glob("*167*"):
        assert ".test." not in f.name and ".spec." not in f.name, f.name
