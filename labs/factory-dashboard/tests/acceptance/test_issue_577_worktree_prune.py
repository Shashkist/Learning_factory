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
if "all" in state.get("fail", []) or (a and a[0] in state.get("fail", [])):
    sys.stderr.write("fake gh: boom" + chr(10))
    sys.exit(1)
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

    def pr(self, branch: str, state: str, head: str | None = None):
        """Record a PR for the branch. headRefOid defaults to the branch tip right now."""
        merged = "2026-11-20T10:00:00Z" if state == "MERGED" else None
        if head is None:
            r = subprocess.run(["git", "rev-parse", "--verify", "-q", "refs/heads/" + branch],
                               cwd=self.repo, capture_output=True, text=True)
            head = r.stdout.strip()
        self.state["prs"][branch] = [{"number": 1, "state": state, "mergedAt": merged,
                                      "headRefOid": head}]
        self.save()

    def gh_fails(self, *commands: str):
        self.state["fail"] = list(commands)
        self.save()

    def claim(self, issue: int):
        stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
        body = {"session": "other-session", "branch": f"f/{issue}-x",
                "heartbeat": stamp, "claimed": stamp}
        text = "<!-- pulsesign-issue-claim\n" + json.dumps(body) + "\n-->\nClaimed"
        self.state["claims"][str(issue)] = [text]
        self.save()

    def worktree(self, branch: str, folder: str, push: bool = False, inside: bool = False,
                 commit: bool = True) -> Path:
        path = (self.repo / folder) if inside else (self.tmp / folder)
        git(self.repo, "worktree", "add", "-b", branch, str(path), "main")
        if not commit:
            return path
        (path / "work.txt").write_text(branch + "\n")
        git(path, "add", "work.txt")
        git(path, "commit", "-m", "work on " + branch)
        if push:
            git(path, "push", "origin", branch)
        return path

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd or self.repo,
                              env=self.env, capture_output=True, text=True, timeout=120)

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


def gitdir(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--absolute-git-dir"))


def set_age(path: Path, seconds_ago: float):
    """Make the whole worktree look untouched for that long: folder, .git file and git dir."""
    when = time.time() - seconds_ago
    targets = [path, path / ".git"]
    for root, dirs, files in os.walk(gitdir(path)):
        targets += [Path(root) / n for n in dirs + files]
    targets.append(gitdir(path))
    for p in targets:
        os.utime(p, (when, when))


def touch_activity(path: Path, seconds_ago: float):
    """Recent git activity (index, logs/HEAD) while the folder and .git file stay old."""
    when = time.time() - seconds_ago
    for name in ("index", "logs/HEAD"):
        f = gitdir(path) / name
        if f.exists():
            os.utime(f, (when, when))


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
    # The main worktree sits on a merged, pushed feature branch and the sweep runs from another
    # worktree, so only a rule "never the main worktree" (not "branch must be f/..." and not
    # "never the current one") keeps it.
    git(w.repo, "checkout", "-b", "f/60-main")
    (w.repo / "main-work.txt").write_text("x\n")
    git(w.repo, "add", "main-work.txt")
    git(w.repo, "commit", "-m", "work in the main worktree")
    git(w.repo, "push", "origin", "f/60-main")
    w.pr("f/60-main", "MERGED")
    (w.repo / "scratch.txt").write_text("untracked in main\n")
    elsewhere = w.worktree("f/61-elsewhere", "wt-61")
    out = ok(w.run(cwd=elsewhere))
    assert (w.repo / "README.md").exists() and (w.repo / "scratch.txt").exists()
    assert git(w.repo, "branch", "--show-current") == "f/60-main"
    main_lines = [ln for ln in out.replace("\\", "/").splitlines()
                  if str(w.repo).replace("\\", "/") + " " in ln + " "]
    assert not any(ln.startswith("REMOVED") for ln in main_lines), out
    assert "f/60-main" in w.branches()


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


# ---- review round 1 (PR #581): safety rules that must hold ------------------------------------


def test_merged_pr_with_a_local_commit_after_the_merge_is_kept(w):
    wt = w.worktree("f/70-after", "wt-70")
    w.pr("f/70-after", "MERGED")  # headRefOid is the tip at merge time
    (wt / "late.txt").write_text("made after the merge\n")
    git(wt, "add", "late.txt")
    git(wt, "commit", "-m", "late work")
    out = ok(w.run())
    assert wt.exists() and "f/70-after" in w.branches()
    assert "KEPT" in w.line(out, "wt-70"), out


def test_merged_pr_with_local_tip_behind_the_merged_head_is_removed(w):
    wt = w.worktree("f/71-behind", "wt-71")
    w.pr("f/71-behind", "MERGED")
    git(wt, "reset", "--hard", "HEAD~1")  # tip is an ancestor of the merged head
    out = ok(w.run())
    assert not wt.exists() and "f/71-behind" not in w.branches()
    assert "REMOVED" in w.line(out, "wt-71")


def test_closed_pr_with_unpushed_commits_beyond_the_remote_branch_is_kept(w):
    wt = w.worktree("f/72-unpushed", "wt-72", push=True)
    w.pr("f/72-unpushed", "CLOSED")
    (wt / "more.txt").write_text("never pushed\n")
    git(wt, "add", "more.txt")
    git(wt, "commit", "-m", "unpushed work")
    out = ok(w.run())
    assert wt.exists() and "f/72-unpushed" in w.branches()
    assert "KEPT" in w.line(out, "wt-72"), out


def test_closed_pr_with_branch_deleted_on_the_remote_but_stale_tracking_ref_is_kept(w):
    wt = w.worktree("f/73-stale", "wt-73", push=True)
    w.pr("f/73-stale", "CLOSED")
    # deleted on the server behind the clone's back: the local tracking ref stays
    git(w.remote, "update-ref", "-d", "refs/heads/f/73-stale")
    assert git(w.repo, "rev-parse", "--verify", "refs/remotes/origin/f/73-stale")
    out = ok(w.run())
    assert wt.exists() and "f/73-stale" in w.branches()
    assert "KEPT" in w.line(out, "wt-73"), out


def test_closed_pr_with_remote_ahead_of_the_local_tip_is_removed(w):
    wt = w.worktree("f/74-ahead", "wt-74", push=True)
    (wt / "more.txt").write_text("pushed later\n")
    git(wt, "add", "more.txt")
    git(wt, "commit", "-m", "more")
    git(wt, "push", "origin", "f/74-ahead")
    git(wt, "reset", "--hard", "HEAD~1")  # local tip is contained in the remote branch
    w.pr("f/74-ahead", "CLOSED")
    out = ok(w.run())
    assert not wt.exists() and "f/74-ahead" not in w.branches()
    assert "REMOVED" in w.line(out, "wt-74")


def test_closed_pr_with_unreachable_remote_is_kept(w):
    wt = w.worktree("f/75-offline", "wt-75", push=True)
    w.pr("f/75-offline", "CLOSED")
    git(w.repo, "remote", "set-url", "origin", str(w.tmp / "no-such-remote.git"))
    out = ok(w.run())
    assert wt.exists() and "f/75-offline" in w.branches()
    assert "KEPT" in w.line(out, "wt-75"), out


def test_unreadable_git_status_with_a_tracked_edit_is_kept_and_the_sweep_goes_on(w):
    broken = w.worktree("f/76-broken", "wt-76")
    other = w.worktree("f/77-fine", "wt-77")
    w.pr("f/76-broken", "MERGED")
    w.pr("f/77-fine", "MERGED")
    (broken / "work.txt").write_text("precious edit\n")
    (gitdir(broken) / "index").write_bytes(b"this is not an index file")
    r = subprocess.run(["git", "status", "--porcelain"], cwd=broken, capture_output=True, text=True)
    assert r.returncode != 0, "setup: git status must fail in the broken worktree"
    out = ok(w.run())
    assert broken.exists() and "f/76-broken" in w.branches()
    assert (broken / "work.txt").read_text() == "precious edit\n"
    assert "KEPT" in w.line(out, "wt-76"), out
    assert not other.exists() and "f/77-fine" not in w.branches(), "one failure stopped the sweep"


def test_leftover_folder_holding_a_git_directory_is_kept(w):
    repo_inside = w.repo / ".claude" / "worktrees" / "agent-fullrepo"
    repo_inside.mkdir(parents=True)
    git(repo_inside, "init", "-b", "main")
    out = ok(w.run("--pass-start", iso(3600)))
    assert (repo_inside / ".git" / "HEAD").exists()
    assert "REMOVED" not in w.line(out, "agent-fullrepo"), out


def test_empty_leftover_folder_is_removed(w):
    # Decision (#577 nit 7): an empty folder holds nothing, so removing it is safe; the line
    # must not call it busy and must not claim it "holds files".
    empty = w.repo / ".claude" / "worktrees" / "agent-empty-dir"
    empty.mkdir(parents=True)
    out = ok(w.run("--pass-start", iso(3600)))
    line = w.line(out, "agent-empty-dir")
    assert not empty.exists(), out
    assert "REMOVED" in line and "busy" not in line.lower() and "holds files" not in line


def test_subagent_worktree_with_recent_git_activity_is_kept(w):
    wt = w.worktree("worktree-agent-live", ".claude/worktrees/agent-live", push=True, inside=True)
    set_age(wt, 7200)  # folder and .git file look old ...
    touch_activity(wt, 300)  # ... but the index and logs/HEAD were written 5 minutes ago
    out = ok(w.run("--pass-start", iso(3600)))
    assert wt.exists() and "worktree-agent-live" in w.branches()
    assert "KEPT" in w.line(out, "agent-live"), out


def test_commitless_subagent_worktree_with_recent_git_activity_is_kept(w):
    wt = w.worktree("worktree-agent-fresh", ".claude/worktrees/agent-fresh", inside=True,
                    commit=False)  # tip is main, which is on origin
    set_age(wt, 7200)
    touch_activity(wt, 300)
    out = ok(w.run("--pass-start", iso(3600)))
    assert wt.exists() and "worktree-agent-fresh" in w.branches()
    assert "KEPT" in w.line(out, "agent-fresh"), out


def test_subagent_worktree_idle_longer_than_the_window_is_removed(w):
    wt = w.worktree("worktree-agent-idle", ".claude/worktrees/agent-idle", push=True, inside=True)
    set_age(wt, 8 * 3600)
    out = ok(w.run("--pass-start", iso(3600)))
    assert not wt.exists() and "worktree-agent-idle" not in w.branches()
    assert "REMOVED" in w.line(out, "agent-idle")


# ---- exam gaps -----------------------------------------------------------------------------


@pytest.mark.parametrize("failing", [["all"], ["pr"], ["issue"]])
def test_gh_failure_keeps_the_worktree(w, failing):
    wt = w.worktree("f/80-gh", "wt-80")
    w.pr("f/80-gh", "MERGED")
    w.gh_fails(*failing)
    out = ok(w.run())
    assert wt.exists() and "f/80-gh" in w.branches()
    assert "KEPT" in w.line(out, "wt-80"), out


def test_the_worktree_the_command_runs_from_is_never_removed(w):
    here = w.worktree("f/81-here", "wt-81")
    other = w.worktree("f/82-other", "wt-82")
    w.pr("f/81-here", "MERGED")
    w.pr("f/82-other", "MERGED")
    out = ok(w.run(cwd=here))
    assert here.exists() and "f/81-here" in w.branches()
    assert "KEPT" in w.line(out, "wt-81"), out
    assert not other.exists()


def test_unknown_folders_are_never_removed(w):
    notes = w.repo / ".claude" / "worktrees" / "notes"
    notes.mkdir(parents=True)
    (notes / "todo.md").write_text("mine\n")
    gitless_copy = w.repo / ".claude" / "worktrees" / "agent-copy"
    (gitless_copy / "src").mkdir(parents=True)
    (gitless_copy / "src" / "a.py").write_text("x = 1\n")
    (gitless_copy / ".git").write_text("gitdir: /nonexistent\n")  # a .git file, but also files
    elsewhere = w.repo / "scratch-dir"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep\n")
    out = ok(w.run("--pass-start", iso(3600)))
    assert (notes / "todo.md").exists()
    assert (gitless_copy / "src" / "a.py").exists()
    assert (elsewhere / "keep.txt").exists()
    assert "REMOVED" not in w.line(out, "/notes") and "REMOVED" not in w.line(out, "agent-copy")


def test_worktree_on_a_non_factory_branch_is_never_removed(w):
    wt = w.worktree("feature/mine", "wt-90")
    w.pr("feature/mine", "MERGED")
    out = ok(w.run())
    assert wt.exists() and "feature/mine" in w.branches()
    assert "REMOVED" not in w.line(out, "wt-90")


def test_locked_worktree_is_kept_and_reported_as_locked(w):
    wt = w.worktree("f/91-locked", "wt-91")
    w.pr("f/91-locked", "MERGED")
    git(w.repo, "worktree", "lock", "--reason", "on a usb stick", str(wt))
    out = ok(w.run())
    line = w.line(out, "wt-91")
    assert wt.exists() and "f/91-locked" in w.branches()
    assert "KEPT" in line and "locked" in line.lower() and "busy" not in line.lower(), out


def test_ignored_files_are_listed_before_a_removable_worktree_is_removed(w):
    wt = w.worktree("f/92-ignored", "wt-92")
    (wt / ".gitignore").write_text(".venv/\nnode_modules/\n.env\n")
    git(wt, "add", ".gitignore")
    git(wt, "commit", "-m", "ignore things")
    w.pr("f/92-ignored", "MERGED")
    (wt / ".venv").mkdir()
    (wt / ".venv" / "pyvenv.cfg").write_text("home = x\n")
    (wt / "node_modules" / "pkg").mkdir(parents=True)
    (wt / "node_modules" / "pkg" / "index.js").write_text("1\n")
    (wt / ".env").write_text("A=1\n")
    out = ok(w.run())
    assert not wt.exists() and "f/92-ignored" not in w.branches()
    assert ".venv" in out and "node_modules" in out and ".env" in out, out


def test_dry_run_changes_nothing_for_ignored_files_and_empty_leftovers(w):
    wt = w.worktree("f/93-dry", "wt-93")
    (wt / ".gitignore").write_text(".env\n")
    git(wt, "add", ".gitignore")
    git(wt, "commit", "-m", "ignore")
    w.pr("f/93-dry", "MERGED")
    (wt / ".env").write_text("A=1\n")
    empty = w.repo / ".claude" / "worktrees" / "agent-empty-dry"
    empty.mkdir(parents=True)
    idle = w.worktree("worktree-agent-idle2", ".claude/worktrees/agent-idle2", push=True,
                      inside=True)
    set_age(idle, 8 * 3600)
    out = ok(w.run("--dry-run", "--pass-start", iso(3600)))
    assert (wt / ".env").read_text() == "A=1\n" and empty.exists() and idle.exists()
    assert {"f/93-dry", "worktree-agent-idle2"} <= set(w.branches())
    assert ".env" in out
    for folder in ("wt-93", "agent-empty-dry", "agent-idle2"):
        assert "REMOVED" in w.line(out, folder), out
