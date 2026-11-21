"""Acceptance tests for issue #271 (D85): docs-only pushes run no CI and do not stale a Deploy,
and the docker and image jobs run on a PR only when it touches a heavy path.

Static checks on the parsed workflows, plus the two decision scripts run as bash against
throwaway git repositories: the `fresh` step of deploy.yml and the `heavy` step of ci.yml.
Nothing needs docker or the network. Cases that run bash are skipped only when bash is
not installed (a Windows laptop without Git Bash); CI runs on Linux.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
CI_PATHS = REPO_ROOT / "scripts" / "ci_paths.py"

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(
    BASH is None,
    reason="bash is not installed here (Windows without Git Bash); CI runs these on Linux",
)


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


def _step(wf: dict, job: str, step_id: str) -> dict:
    for s in wf["jobs"][job]["steps"]:
        if s.get("id") == step_id:
            return s
    raise AssertionError(f"job {job!r} has no step with id {step_id!r}")


# ---------------------------------------------------------------- criterion 1: triggers


def test_push_trigger_ignores_exactly_the_four_docs_only_entries():
    push = _on(_load(CI))["push"]
    want = ["docs/**", "courses/**", ".claude/**", "history.md"]
    assert sorted(push.get("paths-ignore") or []) == sorted(want)
    assert "paths" not in push, "a positive `paths` filter would change the meaning"


def test_pull_request_trigger_has_no_path_filter():
    on = _on(_load(CI))
    assert "pull_request" in on
    pr = on["pull_request"] or {}
    assert "paths" not in pr and "paths-ignore" not in pr


# ---------------------------------------------------------------- git helpers


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    r = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return r.stdout.strip()


def _commit(repo: Path, msg: str, files: dict) -> str:
    """Write (or delete, with None) files and commit; return the sha."""
    for name, content in files.items():
        p = repo / name
        if content is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _new_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "base", {
        "scripts/ci_paths.py": CI_PATHS.read_text(encoding="utf-8"),
        "labs/manager/app.py": "v0\n",
        "labs/web/index.html": "v0\n",
        "README.md": "v0\n",
    })
    return repo


def _run_script(script: str, repo: Path, tmp: Path, env: dict) -> subprocess.CompletedProcess:
    assert BASH
    sh = tmp / "step.sh"
    sh.write_text("set -e\n" + script, encoding="utf-8", newline="\n")
    out = tmp / "github_output"
    out.write_text("", encoding="utf-8")
    runner_temp = tmp / "runner_temp"
    runner_temp.mkdir(exist_ok=True)
    # The steps call python3; give them the interpreter running this test (a laptop may only
    # have a Store alias of that name). On a runner python3 already is this interpreter.
    shim = tmp / "shim"
    shim.mkdir(exist_ok=True)
    py = Path(sys.executable).as_posix()
    body = '#!/bin/sh\nexec "' + py + '" "$@"\n'
    (shim / "python3").write_text(body, encoding="utf-8", newline="\n")
    (shim / "python3").chmod(0o755)
    full = {
        **os.environ,
        "GITHUB_OUTPUT": str(out),
        "RUNNER_TEMP": str(runner_temp),
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": str(shim) + os.pathsep + os.environ.get("PATH", ""),
        **env,
    }
    return subprocess.run(
        [BASH, str(sh)], cwd=repo, env=full, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )


def _outputs(tmp: Path) -> dict:
    lines = (tmp / "github_output").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


# ---------------------------------------------------------------- criterion 1: the fresh step


def _fresh(tmp_path: Path, repo: Path, tag: str, event: str = "workflow_run"):
    script = _step(_load(DEPLOY), "deploy", "fresh")["run"]
    head = _git(repo, "rev-parse", "HEAD")
    r = _run_script(script, repo, tmp_path, {
        "GITHUB_SHA": head, "TAG_SOURCE": tag, "EVENT_NAME": event,
    })
    return r, _outputs(tmp_path)


@needs_bash
def test_fresh_is_true_when_the_tag_is_the_head(tmp_path):
    repo = _new_repo(tmp_path)
    tag = _commit(repo, "code", {"labs/manager/app.py": "v1\n"})
    r, out = _fresh(tmp_path, repo, tag)
    assert r.returncode == 0, r.stderr
    assert out.get("fresh") == "true"


@needs_bash
def test_fresh_is_true_when_only_docs_commits_sit_on_top(tmp_path):
    repo = _new_repo(tmp_path)
    tag = _commit(repo, "code", {"labs/manager/app.py": "v1\n"})
    _commit(repo, "docs", {"docs/factory/STATE.md": "x\n"})
    _commit(repo, "course", {"courses/שיעור-1/מצגת.md": "x\n", ".claude/notes.md": "x\n"})
    r, out = _fresh(tmp_path, repo, tag)
    assert r.returncode == 0, r.stderr
    assert out.get("fresh") == "true"


@needs_bash
@pytest.mark.parametrize("files", [
    {"history.md": "x\n"},
    {"history.md": "x\n", "docs/x.md": "x\n"},
], ids=["history-only", "history-and-docs"])
def test_fresh_is_true_when_only_root_history_md_sits_on_top(tmp_path, files):
    repo = _new_repo(tmp_path)
    tag = _commit(repo, "code", {"labs/manager/app.py": "v1\n"})
    _commit(repo, "history", files)
    r, out = _fresh(tmp_path, repo, tag)
    assert r.returncode == 0, r.stderr
    assert out.get("fresh") == "true"


@needs_bash
@pytest.mark.parametrize("name", ["a/history.md", "history.md.bak", "docs-history.md"])
def test_fresh_is_false_when_a_lookalike_of_history_md_sits_on_top(tmp_path, name):
    repo = _new_repo(tmp_path)
    tag = _commit(repo, "code", {"labs/manager/app.py": "v1\n"})
    _commit(repo, "lookalike", {name: "x\n"})
    r, out = _fresh(tmp_path, repo, tag)
    assert r.returncode == 0, r.stderr
    assert out.get("fresh") == "false"


@needs_bash
def test_fresh_is_true_when_root_history_md_is_deleted_on_top(tmp_path):
    repo = _new_repo(tmp_path)
    _commit(repo, "add history", {"history.md": "x\n"})
    tag = _commit(repo, "code", {"labs/manager/app.py": "v1\n"})
    _commit(repo, "drop history", {"history.md": None})
    r, out = _fresh(tmp_path, repo, tag)
    assert r.returncode == 0, r.stderr
    assert out.get("fresh") == "true"


@needs_bash
def test_fresh_is_true_when_the_push_was_code_then_a_revert_then_docs(tmp_path):
    repo = _new_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "code", {"labs/manager/app.py": "v1\n"})
    revert = _commit(repo, "revert", {"labs/manager/app.py": "v0\n"})
    _commit(repo, "docs", {"docs/a.md": "x\n"})
    for tag in (before, revert):
        sub = tmp_path / f"case-{tag[:7]}"
        sub.mkdir()
        r, out = _fresh(sub, repo, tag)
        assert r.returncode == 0, r.stderr
        assert out.get("fresh") == "true", f"tag {tag} should still deploy"


@pytest.mark.parametrize(
    "files",
    [
        {"labs/content/linux-101/tasks/01.md": "new text\n"},
        {"README.md": "v1\n"},
        {"labs/content/lab-עברית/משימה.md": "x\n"},
        {"labs/manager/app.py": "v2\n"},
        {"labs/web/index.html": "v1\n"},
    ],
    ids=["content-md", "root-readme", "non-ascii-content", "manager-code", "web-code"],
)
@needs_bash
def test_fresh_is_false_when_newer_code_is_on_top(tmp_path, files):
    repo = _new_repo(tmp_path)
    tag = _commit(repo, "code", {"labs/manager/app.py": "v1\n"})
    _commit(repo, "newer", files)
    _commit(repo, "docs", {"docs/a.md": "x\n"})
    r, out = _fresh(tmp_path, repo, tag)
    assert r.returncode == 0, r.stderr
    assert out.get("fresh") == "false"


@needs_bash
def test_fresh_is_false_when_the_tag_is_not_on_main(tmp_path):
    repo = _new_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    tag = _commit(repo, "side docs", {"docs/side.md": "x\n"})
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "main docs", {"docs/main.md": "x\n"})
    assert _git(repo, "rev-parse", "HEAD") != base
    r, out = _fresh(tmp_path, repo, tag)
    assert r.returncode == 0, r.stderr
    assert out.get("fresh") == "false", "a commit that is not on main must never deploy"


@pytest.mark.parametrize("bad", ["main", "HEAD~1", "abc;rm", "ABCDEF", ""])
@needs_bash
def test_a_non_hex_tag_makes_the_fresh_step_fail(tmp_path, bad):
    repo = _new_repo(tmp_path)
    r, out = _fresh(tmp_path, repo, bad)
    assert r.returncode != 0
    assert out.get("fresh") != "true"


@needs_bash
def test_fresh_uses_main_head_copy_of_ci_paths_not_the_checked_out_one(tmp_path):
    # D85: the checkout is the tag's commit; its ci_paths.py must not decide. The tag's copy
    # here always answers "true", main's head (the real one) sees newer code and says false.
    repo = _new_repo(tmp_path)
    stub = "print('true')\n"
    tag = _commit(repo, "code + hostile script", {
        "labs/manager/app.py": "v1\n", "scripts/ci_paths.py": stub,
    })
    head = _commit(repo, "newer code, real script", {
        "labs/manager/app.py": "v2\n",
        "scripts/ci_paths.py": CI_PATHS.read_text(encoding="utf-8"),
    })
    _git(repo, "checkout", "-q", "--detach", tag)  # what actions/checkout of the tag leaves
    _git(repo, "branch", "-q", "-D", "main")  # a checkout of a sha has no local main
    assert (repo / "scripts" / "ci_paths.py").read_text(encoding="utf-8") == stub
    script = _step(_load(DEPLOY), "deploy", "fresh")["run"]
    r = _run_script(script, repo, tmp_path, {
        "GITHUB_SHA": head, "TAG_SOURCE": tag, "EVENT_NAME": "workflow_run",
    })
    assert r.returncode == 0, r.stderr
    assert _outputs(tmp_path).get("fresh") == "false", "the checked-out copy decided"


@needs_bash
def test_workflow_dispatch_is_always_fresh(tmp_path):
    repo = _new_repo(tmp_path)
    tag = _commit(repo, "code", {"labs/manager/app.py": "v1\n"})
    _commit(repo, "newer", {"labs/manager/app.py": "v2\n"})
    r, out = _fresh(tmp_path, repo, tag, event="workflow_dispatch")
    assert r.returncode == 0, r.stderr
    assert out.get("fresh") == "true"


def test_deploy_step_requires_fresh_and_the_fresh_step_holds_no_secret():
    wf = _load(DEPLOY)
    deploy_step = _step(wf, "deploy", "deploy")
    assert str(deploy_step.get("if", "")).strip() == (
        "steps.gate.outputs.ready == 'true' && steps.fresh.outputs.fresh == 'true'"
    ), "both must hold: `||` would let a stale CI deploy older code"
    fresh = _step(wf, "deploy", "fresh")
    assert "secrets." not in str(fresh.get("env", {}))
    assert "secrets." not in str(fresh)
    # a secret at workflow or job level would reach the fresh step's environment too
    assert "secrets." not in str(wf.get("env", {}))
    assert "secrets." not in str(wf["jobs"]["deploy"].get("env", {}))


# ---------------------------------------------------------------- criterion 2: path-scoped jobs


def test_docker_job_needs_changes_and_runs_only_when_heavy():
    jobs = _load(CI)["jobs"]
    assert "changes" in jobs
    assert "changes" in _needs(jobs["docker"])
    assert "needs.changes.outputs.heavy == 'true'" in str(jobs["docker"].get("if", ""))


def test_image_jobs_need_docker():
    jobs = _load(CI)["jobs"]
    image_jobs = [n for n in jobs if n.startswith("image-")]
    assert image_jobs, "no image-* jobs"
    for n in image_jobs:
        assert "docker" in _needs(jobs[n]), f"{n} must need docker"


def _changes(tmp_path: Path, files: dict, event: str = "pull_request"):
    repo = _new_repo(tmp_path)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "branch", "-q", "-D", "main")  # a pull request checkout has only origin/main
    head = _commit(repo, "change", files)
    script = _step(_load(CI), "changes", "heavy")["run"]
    r = _run_script(
        script, repo, tmp_path, {"EVENT_NAME": event, "BASE_REF": "main", "HEAD_SHA": head}
    )
    return r, _outputs(tmp_path)


@pytest.mark.parametrize(
    "path",
    [
        "labs/web/src/i18n/en.json",
        "labs/web/src/pages/LabPage.tsx",
        "labs/web/tests/acceptance/issue-167-docker-e2e.pw.ts",
    ],
    ids=["web-src-json", "web-src-tsx", "web-browser-exam"],
)
@needs_bash
def test_a_web_only_pull_request_that_the_browser_exam_covers_is_heavy(tmp_path, path):
    # #312: labels, the "Lab terminal" iframe title, test ids and the .pw.ts exam itself live under
    # labs/web/; a PR touching only those must get the docker job, which runs the browser exam.
    r, out = _changes(tmp_path, {path: "v1\n", "docs/x.md": "x\n"})
    assert r.returncode == 0, r.stderr
    assert out.get("heavy") == "true", f"a PR touching only {path} gets no docker job"


@needs_bash
def test_a_docs_only_pull_request_is_not_heavy(tmp_path):
    r, out = _changes(tmp_path, {"docs/x.md": "x\n", "README.md": "x\n"})
    assert r.returncode == 0, r.stderr
    assert out.get("heavy") == "false"


@pytest.mark.parametrize(
    "path",
    [
        "labs/images/term/Dockerfile",
        "labs/manager/app.py",
        "labs/deploy/compose.yml",
        "labs/content/linux-101/lab.yaml",
        ".github/workflows/ci.yml",
        "labs/content/lab-עברית/משימה.md",
    ],
    ids=["images", "manager", "deploy", "content", "workflows", "non-ascii"],
)
@needs_bash
def test_each_heavy_prefix_makes_a_pull_request_heavy(tmp_path, path):
    r, out = _changes(tmp_path, {path: "x\n", "labs/web/index.html": "v1\n"})
    assert r.returncode == 0, r.stderr
    assert out.get("heavy") == "true"


def _emptied_heavy() -> str:
    """The real ci_paths.py with HEAVY emptied: a PR copy that would call nothing heavy."""
    text = CI_PATHS.read_text(encoding="utf-8")
    new, n = re.subn(r"^HEAVY = \(.*?\)$", "HEAVY = ()", text, flags=re.S | re.M)
    assert n == 1 and new != text, "could not empty HEAVY in scripts/ci_paths.py"
    return new


@needs_bash
def test_the_base_branch_copy_of_ci_paths_decides_heavy_not_the_pull_requests(tmp_path):
    # D85: a PR that empties HEAVY and touches labs/manager/ must still be heavy.
    r, out = _changes(
        tmp_path, {"scripts/ci_paths.py": _emptied_heavy(), "labs/manager/app.py": "v1\n"}
    )
    assert r.returncode == 0, r.stderr
    assert out.get("heavy") == "true", "the PR's own ci_paths.py decided instead of the base's"


@needs_bash
def test_a_push_is_always_heavy(tmp_path):
    r, out = _changes(tmp_path, {"labs/web/index.html": "v1\n"}, event="push")
    assert r.returncode == 0, r.stderr
    assert out.get("heavy") == "true"


# ---------------------------------------------------------------- exact wiring of the workflows

TAG_SOURCE_EXPR = "${{ github.event.workflow_run.head_sha || inputs.sha || github.sha }}"


def _env(step: dict) -> dict:
    return {k: str(v).strip() for k, v in (step.get("env") or {}).items()}


def _effective_env(wf: dict, job: str, step: dict) -> dict:
    """What the step sees: workflow env, then job env, then step env (later wins)."""
    wf_env = _env({"env": wf.get("env")})
    job_env = _env({"env": wf["jobs"][job].get("env")})
    return {**wf_env, **job_env, **_env(step)}


def _cond(value) -> str:
    """An `if:` value without an optional `${{ ... }}` wrapper (same meaning)."""
    v = str(value or "").strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", v, flags=re.S)
    return (m.group(1) if m else v).strip()


def _checkout(wf: dict, job: str) -> dict:
    for s in wf["jobs"][job]["steps"]:
        if str(s.get("uses", "")).startswith("actions/checkout@"):
            return s
    raise AssertionError(f"job {job!r} has no actions/checkout step")


def test_changes_job_output_heavy_is_the_heavy_step_output():
    out = _load(CI)["jobs"]["changes"].get("outputs") or {}
    assert str(out.get("heavy", "")).strip() == "${{ steps.heavy.outputs.heavy }}"


def test_docker_and_image_jobs_have_exactly_the_expected_conditions():
    jobs = _load(CI)["jobs"]
    assert "if" not in jobs["changes"], "a skipped changes job skips docker and the images"
    assert not _needs(jobs["changes"]), "a needed job that skips on push skips changes too"
    assert set(_needs(jobs["docker"])) == {"changes"}, "docker may need only changes"
    assert _cond(jobs["docker"].get("if")) == "needs.changes.outputs.heavy == 'true'"
    for n in ("image-term", "image-manager"):
        assert "if" not in jobs[n], f"{n} must have no job-level condition, it follows docker"
        assert "docker" in _needs(jobs[n]), f"{n} must need docker"
        assert set(_needs(jobs[n])) <= {"changes", "docker"}, f"{n} may need only these"


def test_heavy_step_reads_the_pull_request_context():
    assert "if" not in _step(_load(CI), "changes", "heavy"), "a skipped step leaves heavy empty"
    env = _env(_step(_load(CI), "changes", "heavy"))
    assert env == {
        "EVENT_NAME": "${{ github.event_name }}",
        "BASE_REF": "${{ github.base_ref }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }


def test_fresh_step_reads_the_same_tag_source_as_the_deploy_step():
    wf = _load(DEPLOY)
    fresh = _effective_env(wf, "deploy", _step(wf, "deploy", "fresh"))
    deploy = _effective_env(wf, "deploy", _step(wf, "deploy", "deploy"))
    assert fresh["TAG_SOURCE"] == deploy["TAG_SOURCE"] == TAG_SOURCE_EXPR
    for step in ("fresh", "deploy"):  # the value is used, not only declared
        assert 'tag="$TAG_SOURCE"' in _step(wf, "deploy", step)["run"], step
    assert fresh["EVENT_NAME"] == "${{ github.event_name }}"


def test_checkouts_fetch_full_history():
    for wf, job in ((_load(DEPLOY), "deploy"), (_load(CI), "changes")):
        assert str((_checkout(wf, job).get("with") or {}).get("fetch-depth")).strip() == "0", job
