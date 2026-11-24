#!/usr/bin/env python3
"""Remove the worktrees and local branches whose work is finished (#577, factory#47).

A worktree is removed only when its work is safe somewhere else: the PR merged,
or the PR closed and the branch is on the remote, or (a subagent worktree older
than this pass) its tip commit is on a remote branch. Anything with tracked
changes, a live claim, an open PR or no PR is KEPT. Untracked-only worktrees are
removed after their file names are listed. The main worktree and the worktree
this command runs from are never touched. A folder Windows reports busy is
SKIPPED with one line, never forced further.

One line per worktree: `<STATUS> <path> <reason>`, STATUS REMOVED | KEPT | SKIPPED.
`--dry-run` prints the same words and changes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import issue_lock

TIMEOUT = 120
FEATURE = re.compile(r"^f/(\d+)-")


def decide_feature(prs: list[dict], claimed: bool, on_remote: bool) -> tuple[bool, str]:
    """(remove?, reason) for an `f/<n>-...` branch from its PRs, claim and remote presence."""
    if claimed:
        return False, "issue has a live claim"
    if not prs:
        return False, "no PR for this branch"
    if any(p.get("state") == "OPEN" for p in prs):
        return False, "PR is open"
    if any(p.get("state") == "MERGED" or p.get("mergedAt") for p in prs):
        return True, "PR merged"
    if on_remote:
        return True, "PR closed, branch on the remote"
    return False, "PR closed but the branch is only local"


def decide_subagent(
    created: float, pass_start: float | None, tip_on_remote: bool
) -> tuple[bool, str]:
    """(remove?, reason) for a subagent worktree from its age and whether its tip is pushed."""
    if pass_start is None:
        return False, "no --pass-start, age unknown"
    if created >= pass_start:
        return False, "created in this pass"
    if tip_on_remote:
        return True, "older than this pass, tip is on the remote"
    return False, "older than this pass but the tip is not on any remote branch"


def git(cwd: Path | str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=TIMEOUT, check=False,
    )


def parse_worktrees(porcelain: str) -> list[dict]:
    out: list[dict] = []
    for block in porcelain.strip().split("\n\n"):
        wt: dict = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            wt[key] = value
        if "worktree" in wt:
            wt["branch"] = wt.get("branch", "").removeprefix("refs/heads/")
            out.append(wt)
    return out


def make_gh(repo: str | None):
    exe = shutil.which("gh")

    def run(args: list[str]) -> str:
        if not exe:
            raise RuntimeError("gh not found")
        cmd = [exe, *args] + (["-R", repo] if repo else [])
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TIMEOUT, check=False)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or "gh failed")
        return p.stdout

    return run


def split_status(path: Path) -> tuple[list[str], list[str]]:
    """(tracked changes, untracked files) of a worktree."""
    out = git(path, "status", "--porcelain", "-uall").stdout.splitlines()
    tracked = [ln[3:] for ln in out if not ln.startswith("??")]
    untracked = [ln[3:] for ln in out if ln.startswith("??")]
    return tracked, untracked


def parse_iso(text: str) -> float:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def say(status: str, path: Path, reason: str) -> None:
    print(f"{status} {path} {reason}", flush=True)


def remove(main_root: Path, path: Path, branch: str, reason: str, dry_run: bool) -> None:
    tracked, untracked = split_status(path)
    if tracked:
        say("KEPT", path, "tracked changes: " + ", ".join(tracked[:5]))
        return
    names = "  untracked files: " + ", ".join(untracked) if untracked else ""
    if dry_run:
        say("REMOVED", path, "would remove (" + reason + ")")
        if names:
            print(names, flush=True)
        return
    r = git(main_root, "worktree", "remove", "--force", str(path))
    if r.returncode != 0 or path.exists():
        first = (r.stderr.strip().splitlines() or ["folder still present"])[0]
        say("SKIPPED", path, "busy, not removed: " + first)
        return
    if branch:
        git(main_root, "branch", "-D", branch)
    say("REMOVED", path, reason)
    if names:
        print(names, flush=True)


def judge(wt: dict, main_root: Path, here: Path, args, pass_start, gh) -> None:
    path = Path(wt["worktree"]).resolve()
    branch = wt["branch"]
    m = FEATURE.match(branch)
    if args.issue is not None and not (m and int(m.group(1)) == args.issue):
        return
    if path == here:
        say("KEPT", path, "the worktree this command runs from")
    elif not path.exists():
        say("KEPT", path, "folder missing, left to git worktree prune")
    elif m:
        try:
            claim, _ = issue_lock.find_claim(int(m.group(1)), gh)
            prs = json.loads(gh(["pr", "list", "--head", branch, "--state", "all",
                                 "--json", "number,state,mergedAt"]))
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as e:
            say("KEPT", path, "gh unavailable: " + str(e)[:80])
            return
        on_remote = git(main_root, "rev-parse", "--verify", "-q",
                        "refs/remotes/origin/" + branch).returncode == 0
        go, reason = decide_feature(prs, claim is not None, on_remote)
        if go:
            remove(main_root, path, branch, reason, args.dry_run)
        else:
            say("KEPT", path, reason)
    elif branch.startswith("worktree-agent-"):
        gitfile = path / ".git"
        created = max(path.stat().st_mtime, gitfile.stat().st_mtime if gitfile.exists() else 0)
        tip = git(path, "rev-parse", "HEAD").stdout.strip()
        pushed = bool(tip) and bool(git(main_root, "branch", "-r", "--contains", tip).stdout.strip())
        go, reason = decide_subagent(created, pass_start, pushed)
        if go:
            remove(main_root, path, branch, reason, args.dry_run)
        else:
            say("KEPT", path, reason)
    else:
        say("KEPT", path, "not a factory branch")


def sweep_leftovers(main_root: Path, known: set[Path], dry_run: bool) -> None:
    """Folders under .claude/worktrees holding only a .git file that git does not know."""
    for d in sorted((main_root / ".claude" / "worktrees").glob("*")):
        if not d.is_dir() or d.resolve() in known:
            continue
        if {p.name for p in d.iterdir()} != {".git"}:
            say("KEPT", d, "unknown to git but holds files")
        elif dry_run:
            say("REMOVED", d, "would remove (leftover folder with only a .git file)")
        else:
            try:
                shutil.rmtree(d)
                say("REMOVED", d, "leftover folder with only a .git file")
            except OSError as e:
                say("SKIPPED", d, "busy, not removed: " + str(e)[:80])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--issue", type=int)
    ap.add_argument("--pass-start")
    ap.add_argument("--repo")
    args = ap.parse_args(argv)
    pass_start = parse_iso(args.pass_start) if args.pass_start else None
    gh = make_gh(args.repo)

    here = Path(git(".", "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if not args.dry_run:
        git(here, "worktree", "prune")
    trees = parse_worktrees(git(here, "worktree", "list", "--porcelain").stdout)
    main_root = Path(trees[0]["worktree"]).resolve()
    for wt in trees[1:]:
        judge(wt, main_root, here, args, pass_start, gh)
    if args.issue is None:
        known = {Path(t["worktree"]).resolve() for t in trees}
        sweep_leftovers(main_root, known, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
