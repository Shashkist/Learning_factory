# ruff: noqa: E501
"""Acceptance tests for issue #98: the CI minute budget. Static checks on the parsed workflows
plus behaviour tests of two shell scripts run against a throwaway git repo. No Docker, network
or Postgres.

Interfaces the exam fixes (the implementer builds exactly these):
  * `scripts/ci/docs-only-paths.txt`: THE docs-only list, one GitHub-style glob per line (blank
    lines and `#` comments ignored). `*` does not cross `/`, `**` does, `**.md` is any .md file.
    It must hold at least docs/**, courses/**, **.md, .claude/**.
  * `ci.yml` `on.push.paths-ignore` is exactly the set of globs in that file.
  * `scripts/ci/docs_only.sh BASE HEAD` (bash, run inside a git repo; reads the list from
    `docs-only-paths.txt` next to the script): exit 0 when every path changed in BASE..HEAD is
    docs-only (also when nothing changed), exit 1 when any changed path is outside the list.
  * `scripts/ci/latest_code_commit.sh [REF]` (REF default HEAD): prints the full sha of the newest
    commit reachable from REF that touched a path outside the docs-only list.
  * `deploy.yml` uses both scripts (the stale check and the manual dispatch without a sha).
  * `ci.yml` job `changes` with output `heavy` ('true' when a PR touches labs/images/**,
    labs/manager/**, labs/deploy/**, labs/content/** or .github/workflows/**). Jobs `docker`,
    `image-term`, `image-manager` list `changes` in `needs` and have an `if` that reads
    `needs.changes.outputs.heavy` and `github.event_name` (push always runs them).
  * `factory-dashboard.yml` on.push.paths is limited to docs/factory/**, labs/factory-dashboard/**
    and .github/workflows/factory-dashboard.yml.
  * `docs/factory/FACTORY.md` has a section whose heading mentions the minute budget and whose
    text mentions batching state commits.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
WF_DIR = REPO_ROOT / ".github" / "workflows"
CI = WF_DIR / "ci.yml"
DEPLOY = WF_DIR / "deploy.yml"
DASH = WF_DIR / "factory-dashboard.yml"
SCRIPTS = REPO_ROOT / "scripts" / "ci"
LIST_FILE = SCRIPTS / "docs-only-paths.txt"
DOCS_ONLY = SCRIPTS / "docs_only.sh"
LATEST = SCRIPTS / "latest_code_commit.sh"
FACTORY_MD = REPO_ROOT / "docs" / "factory" / "FACTORY.md"

REQUIRED_DOCS = {"docs/**", "courses/**", "**.md", ".claude/**"}
HEAVY_PATHS = [
    "labs/images/**",
    "labs/manager/**",
    "labs/deploy/**",
    "labs/content/**",
    ".github/workflows/**",
]
HEAVY_JOBS = ["docker", "image-term", "image-manager"]


def _load(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    assert path.is_file(), f"{path.name} is missing"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(wf: dict) -> dict:
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True
    assert isinstance(on, dict), "needs an `on:` mapping"
    return on


def _needs(job: dict) -> list[str]:
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


def _docs_list() -> list[str]:
    assert LIST_FILE.is_file(), "scripts/ci/docs-only-paths.txt is missing"
    out = []
    for line in LIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _bash() -> str:
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if os.path.isfile(c):
            return c
    b = shutil.which("bash")
    assert b, "bash is needed"
    return b


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    for rel, body in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c " + ",".join(files))
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _commit(r, {"README.md": "x\n", "labs/manager/app/base.py": "x = 0\n"})
    return r


def _script(script: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    assert script.is_file(), f"{script.relative_to(REPO_ROOT)} is missing"
    return subprocess.run([_bash(), script.as_posix(), *args], cwd=repo, capture_output=True, text=True)


# ------------------------------------------------- 1. ci.yml push filter, PR unfiltered


def test_ci_push_to_main_ignores_docs_only_paths():
    push = _on(_load(CI)).get("push")
    assert isinstance(push, dict)
    assert push.get("branches") == ["main"]
    ignore = set(push.get("paths-ignore") or [])
    missing = REQUIRED_DOCS - ignore
    assert not missing, f"on.push.paths-ignore lacks {sorted(missing)}"
    assert "paths" not in push, "use paths-ignore, not paths, on push"


def test_ci_pull_request_runs_on_every_pr_so_exam_integrity_is_never_skipped():
    on = _on(_load(CI))
    assert "pull_request" in on
    pr = on["pull_request"] or {}
    for key in ("paths", "paths-ignore"):
        assert key not in pr, f"on.pull_request must have no {key}: exam-integrity runs on every PR"


# ------------------------------------------------- 2. one docs-only list, two places


def test_docs_only_list_file_has_the_required_globs():
    assert REQUIRED_DOCS <= set(_docs_list())


def test_ci_paths_ignore_equals_the_docs_only_list_file():
    ignore = _on(_load(CI)).get("push", {}).get("paths-ignore") or []
    assert sorted(ignore) == sorted(_docs_list()), "ci.yml paths-ignore and docs-only-paths.txt drifted"


def test_deploy_workflow_uses_the_docs_only_scripts():
    text = DEPLOY.read_text(encoding="utf-8")
    assert "docs_only.sh" in text, "the deploy stale check must call scripts/ci/docs_only.sh"
    assert "latest_code_commit.sh" in text, "manual dispatch without a sha must call scripts/ci/latest_code_commit.sh"


def test_docs_only_script_classifies_paths_by_the_list(repo):
    base = _git(repo, "rev-parse", "HEAD")
    cases = {
        "docs/factory/STATE.md": True,
        "docs/deep/er/x.txt": True,
        "courses/a/b/c.txt": True,
        "README.md": True,
        "labs/manager/NOTES.md": True,
        ".claude/settings.json": True,
        "labs/manager/app/new.py": False,
        "labs/images/term/Dockerfile": False,
        ".github/workflows/ci.yml": False,
        "scripts/x.py": False,
    }
    for rel, docs in cases.items():
        head = _commit(repo, {rel: "y\n"})
        r = _script(DOCS_ONLY, repo, base, head)
        assert r.returncode == (0 if docs else 1), f"{rel}: expected docs-only={docs}, got rc={r.returncode} {r.stderr}"
        base = head


def test_stale_check_deploys_x_when_every_later_commit_is_docs_only(repo):
    x = _commit(repo, {"labs/manager/app/feature.py": "a = 1\n"})
    _commit(repo, {"docs/factory/STATE.md": "state\n"})
    head = _commit(repo, {"README.md": "more\n"})
    assert _script(DOCS_ONLY, repo, x, head).returncode == 0, "X must deploy: only docs followed it"


def test_stale_check_skips_x_when_a_later_commit_touches_code(repo):
    x = _commit(repo, {"labs/manager/app/feature.py": "a = 1\n"})
    _commit(repo, {"docs/factory/STATE.md": "state\n"})
    head = _commit(repo, {"labs/manager/app/other.py": "b = 2\n"})
    assert _script(DOCS_ONLY, repo, x, head).returncode == 1, "X must be skipped: newer code exists"


def test_stale_check_treats_a_mixed_commit_as_code(repo):
    x = _commit(repo, {"labs/manager/app/feature.py": "a = 1\n"})
    head = _commit(repo, {"docs/a.md": "d\n", "labs/web/src/x.ts": "t\n"})
    assert _script(DOCS_ONLY, repo, x, head).returncode == 1


def test_stale_check_x_equal_to_head_deploys(repo):
    x = _commit(repo, {"labs/manager/app/feature.py": "a = 1\n"})
    assert _script(DOCS_ONLY, repo, x, x).returncode == 0


# ------------------------------------------------- 3. dispatch without a sha


def test_dispatch_without_sha_resolves_to_newest_code_commit(repo):
    code = _commit(repo, {"labs/manager/app/feature.py": "a = 1\n"})
    _commit(repo, {"docs/factory/STATE.md": "s1\n"})
    _commit(repo, {"courses/x/y.md": "s2\n"})
    r = _script(LATEST, repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == code


def test_latest_code_commit_follows_a_newer_code_commit(repo):
    _commit(repo, {"labs/manager/app/feature.py": "a = 1\n"})
    _commit(repo, {"docs/a.md": "d\n"})
    newer = _commit(repo, {"labs/web/src/y.ts": "t\n"})
    _commit(repo, {".claude/x.json": "{}\n"})
    r = _script(LATEST, repo, "HEAD")
    assert r.stdout.strip() == newer


# ------------------------------------------------- 4. heavy jobs only when the PR needs them


def test_changes_job_exposes_heavy_output_and_lists_the_heavy_paths():
    jobs = _load(CI)["jobs"]
    assert "changes" in jobs, "ci.yml needs a `changes` job"
    job = jobs["changes"]
    assert "heavy" in (job.get("outputs") or {}), "job `changes` needs the output `heavy`"
    body = repr(job)
    for p in HEAVY_PATHS:
        assert p in body, f"the changes job does not name {p}"


@pytest.mark.parametrize("name", HEAVY_JOBS)
def test_heavy_job_waits_for_changes_and_runs_on_push_always(name):
    job = _load(CI)["jobs"][name]
    assert "changes" in _needs(job), f"{name} must list `changes` in needs"
    cond = str(job.get("if") or "")
    assert "needs.changes.outputs.heavy" in cond, f"{name} `if` must read the heavy output"
    assert "github.event_name" in cond, f"{name} `if` must let push events run it regardless"


def test_manager_web_and_exam_integrity_are_not_gated_by_changes():
    jobs = _load(CI)["jobs"]
    for name in ("manager", "web", "exam-integrity"):
        assert name in jobs
        assert "changes" not in _needs(jobs[name])
        assert "needs.changes" not in str(jobs[name].get("if") or "")
    assert jobs["exam-integrity"].get("if") == "github.event_name == 'pull_request'"


# ------------------------------------------------- 5. concurrency


def test_ci_cancels_superseded_pull_request_runs_but_never_main():
    conc = _load(CI).get("concurrency")
    assert isinstance(conc, dict), "ci.yml needs a top-level concurrency mapping"
    group = str(conc.get("group"))
    assert "github.event.pull_request.number" in group, "PR runs group by PR number"
    assert "github.sha" in group, "main runs must get a group of their own per commit"
    cancel = re.sub(r"[\s${}]", "", str(conc.get("cancel-in-progress")))
    assert cancel in ("github.event_name=='pull_request'", "github.event_name=="+'"pull_request"'), (
        "cancel-in-progress must be true only for pull_request runs"
    )


# ------------------------------------------------- 6. factory dashboard


def test_dashboard_push_is_limited_to_factory_state_and_dashboard_code():
    on = _on(_load(DASH))
    push = on.get("push")
    assert isinstance(push, dict) and push.get("branches") == ["main"]
    paths = set(push.get("paths") or [])
    assert paths, "on.push needs a paths filter"
    assert {"docs/factory/**", "labs/factory-dashboard/**"} <= paths
    allowed = {"docs/factory/**", "labs/factory-dashboard/**", ".github/workflows/factory-dashboard.yml"}
    assert paths <= allowed, f"unexpected paths {sorted(paths - allowed)}"


def test_dashboard_keeps_schedule_and_manual_triggers():
    on = _on(_load(DASH))
    assert "schedule" in on
    assert "workflow_dispatch" in on


# ------------------------------------------------- 7. documented budget


def test_factory_md_documents_the_minute_budget_and_batching_state_commits():
    text = FACTORY_MD.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^#{1,6} ", text)[1:]
    hits = [s for s in sections if re.match(r"[^\n]*minute", s, re.I)]
    assert hits, "FACTORY.md needs a section whose heading mentions the minute budget"
    body = hits[0].lower()
    assert "batch" in body and "state commit" in body, "the section must say to batch state commits"
