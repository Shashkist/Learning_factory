"""Exam for issue #584 (review round 1 of Shashkist/factory#55): `scripts/worktree_prune.py`.

Safety gaps found in the port, each pinned here:
  1. a finished worktree holding another registered worktree, or any `.git` file or folder below
     its top level (a nested repository), is KEPT, the reason names the nested path, and nothing
     under it is deleted;
  2. `--dry-run` never writes git admin data (the worktree index keeps its mtime) and removes
     nothing, also with a prunable worktree around;
  3. a non-ASCII (Hebrew) worktree path does not crash the output on a cp1252 console: the REMOVED
     line is printed and the sweep goes on;
  4. non-ASCII untracked file names count as activity (`git status -z`, no octal escapes);
  5. `--repo` that does not match the clone's `origin` exits non-zero and removes nothing.
The fixtures and the fake `gh` are those of the #577 exam.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest
from test_issue_577_worktree_prune import (
    SCRIPT,
    World,
    git,
    gitdir,
    iso,
    ok,
    set_age,
)

HEBREW = "עברית"
SHALOM = "שלום"


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def norm(text: str) -> str:
    return text.replace("\\", "/")


# ---- 1. nested worktrees and repositories ------------------------------------------------------


def test_worktree_holding_a_registered_worktree_is_kept_with_its_changes(w):
    parent = w.worktree("f/81-parent", "wt-81")
    w.pr("f/81-parent", "MERGED")
    nested = parent / ".claude" / "worktrees" / "agent-b"
    git(w.repo, "worktree", "add", "-b", "worktree-agent-b", str(nested), "main")
    (nested / "README.md").write_text("tracked modification that exists nowhere else\n")
    out = ok(w.run())
    assert parent.exists(), out
    assert nested.exists(), out
    assert (nested / "README.md").read_text() == "tracked modification that exists nowhere else\n"
    assert "f/81-parent" in w.branches()
    line = w.line(out, "wt-81")
    assert line.startswith("KEPT"), out
    assert "agent-b" in norm(line), line


@pytest.mark.parametrize("kind", ["repo", "gitfile"])
def test_worktree_with_a_nested_git_entry_is_kept_and_nothing_is_deleted(w, kind):
    parent = w.worktree("f/82-parent", "wt-82")
    w.pr("f/82-parent", "MERGED")
    inner = parent / "vendor" / "inner"
    inner.mkdir(parents=True)
    if kind == "repo":
        git(inner, "init", "-b", "main")
        (inner / "unpushed.txt").write_text("only commit of a nested repository\n")
        git(inner, "add", "unpushed.txt")
        git(inner, "commit", "-m", "unpushed nested work")
        tip = git(inner, "rev-parse", "HEAD")
    else:
        (inner / ".git").write_text("gitdir: /somewhere/else\n")
        (inner / "notes.txt").write_text("next to a .git file\n")
    out = ok(w.run())
    assert parent.exists(), out
    assert (inner / ("unpushed.txt" if kind == "repo" else "notes.txt")).exists()
    if kind == "repo":
        assert git(inner, "rev-parse", "HEAD") == tip
    assert "f/82-parent" in w.branches()
    line = w.line(out, "wt-82")
    assert line.startswith("KEPT"), out
    assert "inner" in norm(line), line


def test_nested_git_entry_also_keeps_a_subagent_worktree(w):
    sub = w.worktree("worktree-agent-n", ".claude/worktrees/agent-n", inside=True)
    git(sub, "push", "origin", "worktree-agent-n:refs/heads/f/83-saved")
    inner = sub / "scratch" / "clone"
    inner.mkdir(parents=True)
    git(inner, "init", "-b", "main")
    (inner / "a.txt").write_text("a\n")
    git(inner, "add", "a.txt")
    git(inner, "commit", "-m", "unpushed nested work")
    set_age(sub, 7200)
    out = ok(w.run("--pass-start", iso(3600)))
    assert sub.exists() and (inner / "a.txt").exists(), out
    line = w.line(out, "agent-n")
    assert line.startswith("KEPT") and "clone" in norm(line), out


# ---- 2. dry run and the activity check write nothing --------------------------------------------


def test_dry_run_leaves_the_worktree_index_mtime_unchanged(w):
    wt = w.worktree("f/84-dry", "wt-84")
    w.pr("f/84-dry", "MERGED")
    # a stat-dirty tracked file: a plain `git status` would refresh and rewrite the index
    (wt / "work.txt").write_text("f/84-dry\n")
    index = gitdir(wt) / "index"
    old = time.time() - 7200
    os.utime(index, (old, old))
    before = index.stat().st_mtime_ns
    out = ok(w.run("--dry-run"))
    assert index.stat().st_mtime_ns == before, "dry run wrote the index"
    assert wt.exists() and "f/84-dry" in w.branches()
    assert "REMOVED" in w.line(out, "wt-84"), out


def test_activity_check_of_a_subagent_worktree_leaves_the_index_mtime_unchanged(w):
    sub = w.worktree("worktree-agent-i", ".claude/worktrees/agent-i", inside=True)
    git(sub, "push", "origin", "worktree-agent-i:refs/heads/f/85-saved")
    (sub / "work.txt").write_text("worktree-agent-i\n")  # stat-dirty, same content
    set_age(sub, 7200)
    index = gitdir(sub) / "index"
    before = index.stat().st_mtime_ns
    ok(w.run("--dry-run", "--pass-start", iso(3600)))
    assert index.stat().st_mtime_ns == before, "the activity check wrote the index"


def test_dry_run_with_a_prunable_worktree_removes_nothing(w):
    gone = w.worktree("f/86-gone", "wt-86")
    w.pr("f/86-gone", "MERGED")
    shutil.rmtree(gone)  # git still has its admin data: the worktree is prunable
    admin = w.repo / ".git" / "worktrees"
    names_before = sorted(p.name for p in admin.iterdir())
    assert names_before
    ok(w.run("--dry-run"))
    assert sorted(p.name for p in admin.iterdir()) == names_before
    assert "f/86-gone" in w.branches()
    assert "wt-86" in git(w.repo, "worktree", "list", "--porcelain")


# ---- 3. non-ASCII paths on a cp1252 console ------------------------------------------------------


def run_cp1252(w: World, *args: str) -> tuple[int, str, str]:
    env = {**w.env, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    p = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=w.repo, env=env,
                       capture_output=True, timeout=120)
    return (p.returncode, p.stdout.decode("cp1252", errors="replace"),
            p.stderr.decode("cp1252", errors="replace"))


def test_hebrew_worktree_path_does_not_crash_a_cp1252_console_and_the_sweep_continues(w):
    first = w.worktree("f/87-first", f"wt-{HEBREW}")
    w.pr("f/87-first", "MERGED")
    second = w.worktree("f/88-second", "wt-zz-88")
    w.pr("f/88-second", "MERGED")
    code, out, err = run_cp1252(w)
    assert code == 0, out + err
    assert "Traceback" not in err, err
    assert not first.exists() and "f/87-first" not in w.branches()
    removed = [ln for ln in out.splitlines() if ln.startswith("REMOVED") and "wt-" in ln]
    assert len(removed) == 2, out
    assert not second.exists() and "f/88-second" not in w.branches()


def test_hebrew_worktree_path_is_printed_on_a_cp1252_console_under_dry_run(w):
    first = w.worktree("f/89-first", f"wt-{HEBREW}")
    w.pr("f/89-first", "MERGED")
    code, out, err = run_cp1252(w, "--dry-run")
    assert code == 0, out + err
    assert first.exists()
    assert any(ln.startswith("REMOVED") and "wt-" in ln for ln in out.splitlines()), out


# ---- 4. non-ASCII untracked file names count as activity -----------------------------------------


def test_fresh_non_ascii_untracked_file_keeps_a_subagent_worktree(w):
    sub = w.worktree("worktree-agent-h", ".claude/worktrees/agent-h", inside=True)
    (sub / "docs").mkdir()
    (sub / "docs" / "keep.txt").write_text("tracked\n")
    git(sub, "add", "docs/keep.txt")
    git(sub, "commit", "-m", "docs")
    git(sub, "push", "origin", "worktree-agent-h:refs/heads/f/90-saved")
    fresh = sub / "docs" / f"{SHALOM}.txt"
    fresh.write_text("written a moment ago by a live session\n")
    set_age(sub, 7200)  # folder, .git file and git dir are old ...
    now = time.time()
    os.utime(fresh, (now, now))  # ... only this file is new
    out = ok(w.run("--pass-start", iso(3600)))
    assert sub.exists() and fresh.exists(), out
    assert "worktree-agent-h" in w.branches()
    assert w.line(out, "agent-h").startswith("KEPT"), out


def test_non_ascii_untracked_file_is_listed_by_its_real_name_before_removal(w):
    wt = w.worktree("f/91-names", "wt-91")
    (wt / "docs").mkdir()
    (wt / "docs" / "keep.txt").write_text("tracked\n")
    git(wt, "add", "docs/keep.txt")
    git(wt, "commit", "-m", "docs")
    w.pr("f/91-names", "MERGED")
    (wt / "docs" / f"{SHALOM}.txt").write_text("x\n")
    env = {**w.env, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    p = subprocess.run([sys.executable, str(SCRIPT)], cwd=w.repo, env=env,
                       capture_output=True, timeout=120)
    out = p.stdout.decode("utf-8", errors="replace")
    assert p.returncode == 0, out + p.stderr.decode("utf-8", errors="replace")
    assert not wt.exists()
    assert f"{SHALOM}.txt" in out, out
    assert "\\327" not in out, out


# ---- 5. --repo must match origin ----------------------------------------------------------


def test_repo_that_does_not_match_origin_exits_nonzero_and_removes_nothing(w):
    git(w.repo, "remote", "set-url", "origin", "https://github.com/acme/widgets.git")
    wt = w.worktree("f/92-repo", "wt-92")
    w.pr("f/92-repo", "MERGED")
    p = w.run("--repo", "someone/else")
    assert p.returncode != 0, p.stdout + p.stderr
    assert wt.exists() and "f/92-repo" in w.branches()
    assert "REMOVED" not in p.stdout, p.stdout


def test_repo_mismatch_removes_nothing_under_dry_run_and_for_a_local_origin(w):
    wt = w.worktree("f/93-repo", "wt-93")  # origin is a local path: no owner/name to match
    w.pr("f/93-repo", "MERGED")
    p = w.run("--dry-run", "--repo", "someone/else")
    assert p.returncode != 0, p.stdout + p.stderr
    p = w.run("--repo", "someone/else")
    assert p.returncode != 0, p.stdout + p.stderr
    assert wt.exists() and "f/93-repo" in w.branches()


@pytest.mark.parametrize("url", [
    "https://github.com/acme/widgets.git",
    "https://github.com/acme/widgets",
    "git@github.com:acme/widgets.git",
])
def test_repo_that_matches_origin_still_works(w, url):
    git(w.repo, "remote", "set-url", "origin", url)
    wt = w.worktree("f/94-repo", "wt-94")
    w.pr("f/94-repo", "MERGED")
    out = ok(w.run("--repo", "acme/widgets"))
    assert not wt.exists() and "f/94-repo" not in w.branches()
    assert "REMOVED" in w.line(out, "wt-94")


def test_repo_mismatch_leaves_the_admin_entry_of_a_prunable_worktree(w):
    git(w.repo, "remote", "set-url", "origin", "https://github.com/acme/widgets.git")
    gone = w.worktree("f/95-gone", "wt-95")
    w.pr("f/95-gone", "MERGED")
    shutil.rmtree(gone)
    admin = w.repo / ".git" / "worktrees"
    names_before = sorted(p.name for p in admin.iterdir())
    assert names_before
    p = w.run("--repo", "someone/else")
    assert p.returncode != 0, p.stdout + p.stderr
    assert sorted(p.name for p in admin.iterdir()) == names_before


@pytest.mark.parametrize("url", [
    "https://gitlab.com/acme/widgets.git",
    "git@gitlab.com:acme/widgets.git",
])
def test_origin_on_another_host_is_a_mismatch(w, url):
    git(w.repo, "remote", "set-url", "origin", url)
    wt = w.worktree("f/96-host", "wt-96")
    w.pr("f/96-host", "MERGED")
    p = w.run("--repo", "acme/widgets")
    assert p.returncode != 0, p.stdout + p.stderr
    assert wt.exists() and "f/96-host" in w.branches()


def test_ssh_alias_origin_without_a_user_is_accepted_as_its_slug(w):
    git(w.repo, "remote", "set-url", "origin", "github-earnix:acme/widgets.git")
    wt = w.worktree("f/97-alias", "wt-97")
    w.pr("f/97-alias", "MERGED")
    out = ok(w.run("--repo", "acme/widgets"))
    assert not wt.exists() and "f/97-alias" not in w.branches()
    assert "REMOVED" in w.line(out, "wt-97")


# ---- links and bare repositories inside a finished worktree --------------------------------------


def make_link(link, target) -> None:
    """A directory symlink, or on Windows a junction when symlinks need a privilege we lack."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return
    pytest.skip("no directory link can be created here")


def run_timed(w: World, *args: str) -> tuple[subprocess.CompletedProcess, float]:
    start = time.monotonic()
    p = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=w.repo, env=w.env,
                       capture_output=True, text=True, timeout=60)
    return p, time.monotonic() - start


@pytest.mark.parametrize("dry", [False, True])
def test_worktree_with_a_link_to_an_outside_folder_is_kept_and_the_target_survives(w, dry):
    parent = w.worktree("f/98-link", "wt-98")
    w.pr("f/98-link", "MERGED")
    outside = w.tmp / "outside-plain"
    outside.mkdir()
    (outside / "precious.txt").write_text("not part of any worktree\n")
    (parent / "node_modules").mkdir()
    make_link(parent / "node_modules" / "pkg", outside)
    p, _ = run_timed(w, *(["--dry-run"] if dry else []))
    assert p.returncode == 0, p.stdout + p.stderr
    assert (outside / "precious.txt").read_text() == "not part of any worktree\n"
    assert parent.exists() and "f/98-link" in w.branches()
    line = w.line(p.stdout, "wt-98")
    assert line.startswith("KEPT"), p.stdout
    assert "pkg" in norm(line), line


@pytest.mark.parametrize("dry", [False, True])
def test_worktree_with_a_link_back_into_itself_is_kept_promptly(w, dry):
    parent = w.worktree("f/99-loop", "wt-99")
    w.pr("f/99-loop", "MERGED")
    (parent / "sub").mkdir()
    (parent / "sub" / "keep.txt").write_text("x\n")
    make_link(parent / "loop", parent)
    p, took = run_timed(w, *(["--dry-run"] if dry else []))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Traceback" not in p.stderr, p.stderr
    assert took < 45, took
    assert (parent / "sub" / "keep.txt").exists() and "f/99-loop" in w.branches()
    line = w.line(p.stdout, "wt-99")
    assert line.startswith("KEPT"), p.stdout
    assert "loop" in norm(line), line


@pytest.mark.parametrize("dry", [False, True])
def test_worktree_with_a_nested_bare_repository_is_kept(w, dry):
    parent = w.worktree("f/100-bare", "wt-100")
    w.pr("f/100-bare", "MERGED")
    bare = parent / "vendor" / "mirror"
    bare.mkdir(parents=True)
    git(bare, "init", "--bare", "-b", "main")
    assert not (bare / ".git").exists() and (bare / "HEAD").exists()
    out = ok(w.run(*(["--dry-run"] if dry else [])))
    assert parent.exists() and (bare / "HEAD").exists(), out
    assert "f/100-bare" in w.branches()
    line = w.line(out, "wt-100")
    assert line.startswith("KEPT"), out
    assert "mirror" in norm(line), line
