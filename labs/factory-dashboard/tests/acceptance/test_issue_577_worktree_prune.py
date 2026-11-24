"""Exam for issue #577 (rules in Shashkist/factory#47): `scripts/worktree_prune.py`.

Interface fixed here
  python scripts/worktree_prune.py [--dry-run] [--issue <n>] [--pass-start <iso>]
      [--repo <owner/name>]
  - run with the cwd inside a clone; exits 0 when it could do its job.
  - without --issue it sweeps every worktree (the orient call); `--issue n` looks only at the
    worktree whose branch is `f/<n>-...` (the close-step call).
  - one stdout line per worktree: `<STATUS> <path> <reason>`, STATUS is REMOVED, KEPT or SKIPPED.
    Under --dry-run the line says what would be done, same STATUS word, nothing changes.
  - untracked files of a worktree that is removed are listed (their names) on stdout.
  - the main worktree is never removed and never touched.
  - `gh` is called through the PATH (resolve it with shutil.which so a Windows `gh.cmd` works):
      gh pr list --head <branch> --state all --json number,state,mergedAt   -> JSON list, state is
          OPEN / CLOSED / MERGED; an empty list means no PR (kept)
      gh issue view <n> --json comments  -> the claim comment (scripts/issue_lock.py marker); a live
          claim (no "released") on the issue number taken from the branch `f/<n>-...` keeps it
    A trailing `-R <repo>` is allowed. Whether a branch is on the remote is read from git itself
    (`origin`), and so is whether a subagent tip is on the remote.
  - a subagent worktree (`.claude/worktrees/agent-*`, branch `worktree-agent-*`) counts as created
    when its folder or `.git` file was last modified (the exam sets that time); created at or after
    --pass-start means kept. Older: removed only if its tip commit is on a remote branch.
  - a folder under `.claude/worktrees/` holding only a `.git` file, unknown to git, is removed.
Not tested (not portable): "a folder Windows reports busy is skipped with one log line".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "worktree_prune.py"
SKILL = ROOT / ".claude" / "skills" / "factory" / "SKILL.md"

FAKE_GH = """\
import json, os, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
a = list(sys.argv[1:])
if "-R" in a:
    i = a.index("-R")
    del a[i:i + 2]
if a[:2] == ["pr", "list"]:
    branch = a[a.index("--head") + 1]
    print(json.dumps(state["prs"].get(branch, [])))
elif a[:2] == ["issue", "view"]:
    print(json.dumps({"comments": [
        {"body": b, "url": "https://x/issues/%s#issuecomment-%d" % (a[2], i + 1)}
        for i, b in enumerate(state["claims"].get(a[2], []))]}))
else:
    sys.stderr.write("fake gh: unsupported " + " ".join(a) + chr(10))
    sys.exit(1)
"""


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


def iso(seconds_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - seconds_ago, UTC).isoformat()


class World:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.remote = tmp / "remote.git"
        self.repo = tmp / "repo"
        self.state_file = tmp / "gh_state.json"
        self.state: dict = {"prs": {}, "claims": {}}
        self.save()
        bindir = tmp / "bin"
        bindir.mkdir()
        (bindir / "fake_gh.py").write_text(FAKE_GH)
        py = sys.executable
        if sys.platform == "win32":
            (bindir / "gh.cmd").write_text(f'@"{py}" "%~dp0fake_gh.py" %*\r\n')
        else:
            g = bindir / "gh"
            g.write_text(f'#!/bin/sh\nexec "{py}" "{bindir}/fake_gh.py" "$@"\n')
            g.chmod(0o755)
        ident = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        self.env = {**os.environ, **ident, "FAKE_GH_STATE": str(self.state_file),
                    "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
        os.environ.update(ident)
        git(tmp, "init", "--bare", "-b", "main", str(self.remote))
        git(tmp, "init", "-b", "main", str(self.repo))
        git(self.repo, "remote", "add", "origin", str(self.remote))
        (self.repo / "README.md").write_text("hello\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "init")
        git(self.repo, "push", "origin", "main")

    def save(self):
        self.state_file.write_text(json.dumps(self.state))

    def pr(self, branch: str, state: str):
        merged = "2026-11-20T10:00:00Z" if state == "MERGED" else None
        self.state["prs"][branch] = [{"number": 1, "state": state, "mergedAt": merged}]
        self.save()

    def claim(self, issue: int):
        stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
        body = {"session": "other-session", "branch": f"f/{issue}-x",
                "heartbeat": stamp, "claimed": stamp}
        text = "<!-- pulsesign-issue-claim\n" + json.dumps(body) + "\n-->\nClaimed"
        self.state["claims"][str(issue)] = [text]
        self.save()

    def worktree(self, branch: str, folder: str, push: bool = False, inside: bool = False) -> Path:
        path = (self.repo / folder) if inside else (self.tmp / folder)
        git(self.repo, "worktree", "add", "-b", branch, str(path), "main")
        (path / "work.txt").write_text(branch + "\n")
        git(path, "add", "work.txt")
        git(path, "commit", "-m", "work on " + branch)
        if push:
            git(path, "push", "origin", branch)
        return path

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def branches(self) -> list[str]:
        return git(self.repo, "branch", "--format=%(refname:short)").split()

    @staticmethod
    def line(out: str, folder: str) -> str:
        for ln in out.splitlines():
            if folder in ln.replace("\\", "/"):
                return ln
        return ""


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def ok(proc: subprocess.CompletedProcess) -> str:
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def set_age(path: Path, seconds_ago: float):
    when = time.time() - seconds_ago
    for p in (path, path / ".git"):
        os.utime(p, (when, when))


def test_merged_pr_removes_worktree_and_local_branch(w):
    wt = w.worktree("f/11-merged", "wt-11")
    w.pr("f/11-merged", "MERGED")
    out = ok(w.run())
    assert not wt.exists()
    assert "f/11-merged" not in w.branches()
    assert "REMOVED" in w.line(out, "wt-11")


def test_closed_pr_with_branch_on_remote_is_removed(w):
    wt = w.worktree("f/12-closed", "wt-12", push=True)
    w.pr("f/12-closed", "CLOSED")
    out = ok(w.run())
    assert not wt.exists()
    assert "f/12-closed" not in w.branches()
    assert "REMOVED" in w.line(out, "wt-12")


def test_closed_pr_with_branch_only_local_is_kept(w):
    wt = w.worktree("f/13-local", "wt-13")
    w.pr("f/13-local", "CLOSED")
    out = ok(w.run())
    assert wt.exists()
    assert "f/13-local" in w.branches()
    assert "KEPT" in w.line(out, "wt-13")


def test_tracked_change_is_kept_and_reported(w):
    wt = w.worktree("f/14-dirty", "wt-14")
    w.pr("f/14-dirty", "MERGED")
    (wt / "work.txt").write_text("edited\n")
    out = ok(w.run())
    assert wt.exists() and "f/14-dirty" in w.branches()
    line = w.line(out, "wt-14")
    assert "KEPT" in line and "tracked" in line.lower()


def test_staged_change_is_kept_and_reported(w):
    wt = w.worktree("f/15-staged", "wt-15")
    w.pr("f/15-staged", "MERGED")
    (wt / "new.txt").write_text("staged\n")
    git(wt, "add", "new.txt")
    out = ok(w.run())
    assert wt.exists() and "f/15-staged" in w.branches()
    line = w.line(out, "wt-15")
    assert "KEPT" in line and "tracked" in line.lower()


def test_untracked_only_is_listed_then_removed(w):
    wt = w.worktree("f/16-untracked", "wt-16")
    w.pr("f/16-untracked", "MERGED")
    (wt / "output.json").write_text("{}")
    (wt / "archive").mkdir()
    (wt / "archive" / "old-report.txt").write_text("x")
    out = ok(w.run())
    assert not wt.exists() and "f/16-untracked" not in w.branches()
    assert "output.json" in out and "archive" in out
    assert "REMOVED" in w.line(out, "wt-16")


def test_claimed_issue_is_kept(w):
    wt = w.worktree("f/20-claimed", "wt-20")
    w.pr("f/20-claimed", "MERGED")
    w.claim(20)
    out = ok(w.run())
    assert wt.exists() and "f/20-claimed" in w.branches()
    assert "KEPT" in w.line(out, "wt-20")


def test_open_pr_is_kept(w):
    wt = w.worktree("f/21-open", "wt-21", push=True)
    w.pr("f/21-open", "OPEN")
    out = ok(w.run())
    assert wt.exists() and "f/21-open" in w.branches()
    assert "KEPT" in w.line(out, "wt-21")


def test_branch_without_pr_is_kept(w):
    wt = w.worktree("f/22-nopr", "wt-22", push=True)
    out = ok(w.run())
    assert wt.exists() and "f/22-nopr" in w.branches()
    assert "KEPT" in w.line(out, "wt-22")


def test_main_worktree_is_never_touched(w):
    w.pr("main", "MERGED")
    out = ok(w.run())
    assert (w.repo / "README.md").exists() and "main" in w.branches()
    assert "REMOVED" not in w.line(out, "repo")


def test_issue_flag_limits_to_that_issues_branch(w):
    a = w.worktree("f/31-a", "wt-31")
    b = w.worktree("f/32-b", "wt-32")
    w.pr("f/31-a", "MERGED")
    w.pr("f/32-b", "MERGED")
    ok(w.run("--issue", "31"))
    assert not a.exists() and "f/31-a" not in w.branches()
    assert b.exists() and "f/32-b" in w.branches()


def test_subagent_worktree_newer_than_pass_start_is_kept(w):
    wt = w.worktree("worktree-agent-new", ".claude/worktrees/agent-new", push=True, inside=True)
    set_age(wt, 0)
    out = ok(w.run("--pass-start", iso(3600)))
    assert wt.exists() and "worktree-agent-new" in w.branches()
    assert "KEPT" in w.line(out, "agent-new")


def test_subagent_worktree_older_with_tip_on_remote_is_removed(w):
    wt = w.worktree("worktree-agent-old", ".claude/worktrees/agent-old", inside=True)
    git(wt, "push", "origin", "worktree-agent-old:refs/heads/f/40-saved")
    set_age(wt, 7200)
    out = ok(w.run("--pass-start", iso(3600)))
    assert not wt.exists() and "worktree-agent-old" not in w.branches()
    assert "REMOVED" in w.line(out, "agent-old")


def test_subagent_worktree_older_with_tip_not_on_remote_is_kept(w):
    wt = w.worktree("worktree-agent-lost", ".claude/worktrees/agent-lost", inside=True)
    set_age(wt, 7200)
    ok(w.run("--pass-start", iso(3600)))
    assert wt.exists() and "worktree-agent-lost" in w.branches()


def test_leftover_folder_with_only_a_git_file_is_removed(w):
    leftover = w.repo / ".claude" / "worktrees" / "agent-gone"
    leftover.mkdir(parents=True)
    (leftover / ".git").write_text(f"gitdir: {w.repo / '.git' / 'worktrees' / 'agent-gone'}\n")
    out = ok(w.run("--pass-start", iso(3600)))
    assert not leftover.exists()
    assert "REMOVED" in w.line(out, "agent-gone")


def test_dry_run_changes_nothing_but_says_what_it_would_do(w):
    merged = w.worktree("f/51-merged", "wt-51")
    (merged / "scratch.txt").write_text("x")
    w.pr("f/51-merged", "MERGED")
    sub = w.worktree("worktree-agent-dry", ".claude/worktrees/agent-dry", inside=True)
    git(sub, "push", "origin", "worktree-agent-dry:refs/heads/f/52-saved")
    set_age(sub, 7200)
    leftover = w.repo / ".claude" / "worktrees" / "agent-empty"
    leftover.mkdir(parents=True)
    (leftover / ".git").write_text("gitdir: /nonexistent\n")
    out = ok(w.run("--dry-run", "--pass-start", iso(3600)))
    assert merged.exists() and sub.exists() and leftover.exists()
    assert {"f/51-merged", "worktree-agent-dry"} <= set(w.branches())
    for folder in ("wt-51", "agent-dry", "agent-empty"):
        assert "REMOVED" in w.line(out, folder), out


def test_skill_names_the_script_in_orient_and_close():
    text = SKILL.read_text(encoding="utf-8")
    orient = text.split("## 0", 1)[1].split("\n## 1", 1)[0]
    close = text.split("## 7", 1)[1].split("\n## 8", 1)[0]
    assert "worktree_prune" in orient
    assert "worktree_prune" in close
