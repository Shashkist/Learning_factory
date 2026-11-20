"""Tests for scripts/ci_paths.py, on throwaway git repos."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("ci_paths.py")


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        [
            "git",
            "-c", "user.name=t",
            "-c", "user.email=t@example.com",
            "-c", "commit.gpgsign=false",
            "-c", "core.quotePath=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return out.stdout.strip()


def commit(repo: Path, *files: str) -> str:
    for f in files:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{f}\n{git(repo, 'rev-list', '--all', '--count')}\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c " + ",".join(files))
    return git(repo, "rev-parse", "HEAD")


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "labs/manager/app.py")
    return tmp_path


def deploys(repo: Path, tag: str, head: str) -> str:
    r = run(repo, "deploys", tag, head)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


# Path classes, through the rule Deploy uses: code below, <files> on top, may the code's CI deploy?
def test_docs_only_on_top_deploys(repo: Path) -> None:
    code = commit(repo, "labs/web/a.ts")
    commit(repo, "docs/factory/STATE.md")
    head = commit(repo, "courses/x.md", ".claude/s.json")
    assert deploys(repo, code, head) == "true"


def test_mixed_commit_counts_as_code(repo: Path) -> None:
    code = commit(repo, "labs/web/a.ts")
    head = commit(repo, "docs/a.md", "labs/web/b.ts")
    assert deploys(repo, code, head) == "false"


def test_lab_content_markdown_is_code(repo: Path) -> None:
    code = commit(repo, "labs/web/a.ts")
    head = commit(repo, "labs/content/x/task.he.md")
    assert deploys(repo, code, head) == "false"


def test_root_readme_is_code(repo: Path) -> None:
    code = commit(repo, "labs/web/a.ts")
    head = commit(repo, "README.md")
    assert deploys(repo, code, head) == "false"


def test_non_ascii_lab_content_is_code(repo: Path) -> None:
    code = commit(repo, "labs/web/a.ts")
    head = commit(repo, "labs/content/x/שלב.md")
    assert deploys(repo, code, head) == "false"


def test_hebrew_name_under_courses_is_docs_only(repo: Path) -> None:
    code = commit(repo, "labs/web/a.ts")
    head = commit(repo, "courses/שיעור.md")
    assert deploys(repo, code, head) == "true"


def test_unknown_tag_fails_loudly(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    r = run(repo, "deploys", "0" * 40, head)
    assert r.returncode != 0
    assert r.stderr.strip()


def test_deploys_when_only_docs_landed_after(repo: Path) -> None:
    code = commit(repo, "labs/manager/b.py")
    head = commit(repo, "docs/factory/STATE.md")
    assert deploys(repo, code, head) == "true"


def test_deploys_the_docs_head_of_a_mixed_push(repo: Path) -> None:
    commit(repo, "labs/manager/b.py")
    head = commit(repo, "docs/factory/STATE.md")
    assert deploys(repo, head, head) == "true"


def test_older_code_never_deploys(repo: Path) -> None:
    old = commit(repo, "labs/manager/b.py")
    commit(repo, "docs/a.md")
    head = commit(repo, "labs/web/c.ts")
    assert deploys(repo, old, head) == "false"


def test_code_then_revert_then_docs_still_deploys(repo: Path) -> None:
    # One push [X, revert X, docs] is docs-only for GitHub's paths-ignore: no CI, so the code
    # commit below must still deploy (review of #278, finding 4).
    c1 = commit(repo, "labs/manager/b.py")
    commit(repo, "labs/web/a.ts")
    git(repo, "revert", "--no-edit", "HEAD")
    head = commit(repo, "docs/y.md")
    assert deploys(repo, c1, head) == "true"


def test_a_tag_off_main_never_deploys(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    side = commit(repo, "docs/a.md")
    git(repo, "checkout", "-q", "main")
    git(repo, "reset", "-q", "--hard", base)
    head = commit(repo, "docs/b.md")
    assert deploys(repo, side, head) == "false"


def test_paths_ignore_matches_docs_only() -> None:
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        from ci_paths import DOCS_ONLY
    finally:
        sys.path.pop(0)
    ci = (SCRIPT.parent.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    block = ci.split("paths-ignore:", 1)[1].split("\n\n", 1)[0]
    entries = [ln.strip()[2:].strip() for ln in block.splitlines() if ln.strip().startswith("- ")]
    # Every entry must have the one shape the script understands, so no extra rule hides here.
    assert entries and all(re.fullmatch(r'"[^"*/]+/\*\*"', e) for e in entries), entries
    assert sorted(e[1:-4] for e in entries) == sorted(DOCS_ONLY)


def test_docs_commit_before_newer_code_does_not_deploy(repo: Path) -> None:
    docs = commit(repo, "docs/a.md")
    head = commit(repo, "labs/web/c.ts")
    assert deploys(repo, docs, head) == "false"


def heavy(repo: Path, base: str, head: str = "HEAD") -> str:
    r = run(repo, "heavy", base, head)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def branch_with(repo: Path, *files: str) -> str:
    git(repo, "checkout", "-q", "-b", "pr")
    commit(repo, *files)
    return "main"


@pytest.mark.parametrize(
    "path",
    ["labs/web/src/a.ts", "docs/a.md", "README.md", "labs/web/x.md", "labs/managerial/x.py"],
)
def test_heavy_false(repo: Path, path: str) -> None:
    base = branch_with(repo, path)
    assert heavy(repo, base) == "false"


@pytest.mark.parametrize(
    "path",
    [
        "labs/images/term/Dockerfile",
        "labs/manager/app/x.py",
        "labs/deploy/compose.yml",
        "labs/content/x/lab.yaml",
        ".github/workflows/ci.yml",
        "labs/content/x/שלב.md",
    ],
)
def test_heavy_true(repo: Path, path: str) -> None:
    base = branch_with(repo, path)
    assert heavy(repo, base) == "true"


def test_heavy_uses_merge_base(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "pr")
    commit(repo, "labs/web/a.ts")
    git(repo, "checkout", "-q", "main")
    commit(repo, "labs/manager/new.py")
    git(repo, "checkout", "-q", "pr")
    assert heavy(repo, "main") == "false"
