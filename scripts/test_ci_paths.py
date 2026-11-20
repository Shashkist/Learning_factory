"""Tests for scripts/ci_paths.py, on throwaway git repos."""

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


def latest(repo: Path) -> str:
    r = run(repo, "latest-code", "HEAD")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_latest_code_skips_docs_only_head(repo: Path) -> None:
    code = commit(repo, "labs/web/a.ts")
    commit(repo, "docs/factory/STATE.md")
    commit(repo, "courses/x.md", ".claude/s.json")
    assert latest(repo) == code


def test_mixed_commit_counts_as_code(repo: Path) -> None:
    mixed = commit(repo, "docs/a.md", "labs/web/b.ts")
    assert latest(repo) == mixed


def test_lab_content_markdown_is_code(repo: Path) -> None:
    c = commit(repo, "labs/content/x/task.he.md")
    assert latest(repo) == c


def test_root_readme_is_code(repo: Path) -> None:
    c = commit(repo, "README.md")
    assert latest(repo) == c


def test_hebrew_name_under_courses_is_docs_only(repo: Path) -> None:
    code = commit(repo, "labs/web/a.ts")
    commit(repo, "courses/שיעור.md")
    assert latest(repo) == code


def test_latest_code_fails_when_none(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "docs/a.md")
    r = run(tmp_path, "latest-code", "HEAD")
    assert r.returncode != 0
    assert r.stderr.strip()


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
