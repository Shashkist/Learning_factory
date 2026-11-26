"""Unit tests for the pure decisions of scripts/worktree_prune.py (#577)."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import worktree_prune as wp

ROOT = Path(__file__).resolve().parent.parent


def pr(state, merged=None):
    return {"number": 1, "state": state, "mergedAt": merged}


def yes(*_):
    return True, "ok"


def no(*_):
    return False, "nope"


def test_merged_follows_the_tip_check():
    merged = [pr("MERGED", "2026-11-20T10:00:00Z")]
    assert wp.decide_feature(merged, False, yes, no) == (True, "PR merged")
    assert wp.decide_feature(merged, False, no, yes) == (False, "nope")


def test_closed_follows_the_remote_check():
    assert wp.decide_feature([pr("CLOSED")], False, no, yes)[0]
    assert wp.decide_feature([pr("CLOSED")], False, yes, no) == (False, "nope")


def test_open_none_and_claim_keep():
    assert not wp.decide_feature([pr("OPEN")], False, yes, yes)[0]
    assert not wp.decide_feature([], False, yes, yes)[0]
    assert not wp.decide_feature([pr("MERGED")], True, yes, yes)[0]


def test_subagent_needs_pass_start_age_and_pushed_tip():
    assert not wp.decide_subagent(100.0, None, True)[0]
    assert not wp.decide_subagent(200.0, 100.0, True)[0]
    assert not wp.decide_subagent(50.0, 100.0, False)[0]
    assert wp.decide_subagent(50.0, 100.0, True)[0]


def test_subagent_with_recent_activity_is_kept():
    window = wp.STALE_AFTER
    assert not wp.decide_subagent(50.0, 100.0, True, now=50.0 + window - 1)[0]
    assert wp.decide_subagent(50.0, 100.0, True, now=50.0 + window + 1)[0]


def test_stale_window_is_the_claim_window():
    assert wp.STALE_AFTER == 90 * 60


def test_parse_worktrees_strips_branch_prefix_and_keeps_lock():
    text = ("worktree /a\nHEAD x\nbranch refs/heads/main\n\n"
            "worktree /b\nHEAD y\ndetached\nlocked usb\n")
    trees = wp.parse_worktrees(text)
    assert [t["branch"] for t in trees] == ["main", ""]
    assert trees[1]["worktree"] == "/b" and trees[1]["locked"] == "usb"


def test_parse_iso_accepts_z_and_naive():
    assert wp.parse_iso("2026-11-20T10:00:00Z") == wp.parse_iso("2026-11-20T10:00:00")


def test_merged_safe_needs_a_head_and_an_ancestor(monkeypatch):
    monkeypatch.setattr(wp, "is_ancestor", lambda cwd, c, of: of == "H2")
    assert not wp.merged_safe(None, "t", [{"state": "MERGED"}])[0]
    assert not wp.merged_safe(None, "t", [{"headRefOid": "H1"}])[0]
    assert wp.merged_safe(None, "t", [{"headRefOid": "H1"}, {"headRefOid": "H2"}])[0]


# ---- real temporary repositories (review round 2, #577) -------------------------------------


def g(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=True)
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    main = tmp_path / "main"
    g(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    g(tmp_path, "init", "-q", "-b", "main", str(main))
    g(main, "remote", "add", "origin", str(remote))
    (main / "a.txt").write_text("a\n")
    g(main, "add", "a.txt")
    g(main, "commit", "-q", "-m", "init")
    g(main, "push", "-q", "origin", "main")
    return main


def add_worktree(main, name, branch):
    path = main.parent / name
    g(main, "worktree", "add", "-q", "-b", branch, str(path), "main")
    return path


def commit_file(wt, name):
    (wt / name).write_text(name)
    g(wt, "add", name)
    g(wt, "commit", "-q", "-m", name)
    return g(wt, "rev-parse", "HEAD")


def test_busy_folder_is_skipped_and_left_intact(repo, capsys):
    if sys.platform != "win32":
        pytest.skip("a folder held by a process only refuses to move on Windows")
    wt = add_worktree(repo, "wt", "f/1-busy")
    (wt / "sub").mkdir()
    (wt / "note.txt").write_text("x")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=wt / "sub")
    try:
        # registered: every worktree path, resolved, the way main() builds it
        wp.remove(repo, wt, "f/1-busy", "PR merged", False, [repo.resolve(), wt.resolve()])
    finally:
        holder.kill()
        holder.wait()
    out = capsys.readouterr().out
    assert "SKIPPED" in out and "busy" in out
    assert "note.txt" in out  # names printed before any attempt
    assert (wt / ".git").exists() and (wt / "note.txt").exists()
    assert "f/1-busy" in g(repo, "branch", "--format=%(refname:short)").split()
    assert wt.name in g(repo, "worktree", "list")


def test_free_folder_check_renames_back(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "f").write_text("1")
    assert wp.folder_is_free(d) and (d / "f").exists()
    assert not wp.folder_is_free(tmp_path / "missing")


def test_status_collapses_ignored_and_untracked_folders(repo):
    wt = add_worktree(repo, "wt", "f/2-collapse")
    (wt / ".gitignore").write_text(".venv/\n")
    g(wt, "add", ".gitignore")
    g(wt, "commit", "-q", "-m", "ignore")
    (wt / ".venv").mkdir()
    for i in range(30):
        (wt / ".venv" / f"f{i}").write_text("x")
    (wt / "newdir").mkdir()
    (wt / "newdir" / "deep.txt").write_text("x")
    tracked, untracked, ignored = wp.split_status(wt)
    assert ignored == [".venv/"] and untracked == ["newdir/"] and tracked == []


def test_names_are_capped():
    text = wp.list_names([f"f{i}" for i in range(50)], [])
    assert "f0" in text and "f49" not in text and "30 more" in text


def test_new_file_in_an_existing_subfolder_counts_as_activity(repo):
    wt = add_worktree(repo, "wt", "worktree-agent-x")
    (wt / "sub").mkdir()
    commit_file(wt, "sub/old.txt")
    old = time.time() - 7200
    for p in (wt, wt / "sub", wt / "sub" / "old.txt"):
        os.utime(p, (old, old))
    (wt / "sub" / "new.txt").write_text("n")
    os.utime(wt / "sub", (old, old))  # the folder itself looks idle
    status = wp.split_status(wt)
    newest = max(p.stat().st_mtime for p in wp.changed_paths(wt, status) if p.exists())
    assert time.time() - newest < 60


def test_tip_on_remote_ignores_a_stale_tracking_ref(repo):
    wt = add_worktree(repo, "wt", "worktree-agent-y")
    tip = commit_file(wt, "w.txt")
    g(wt, "push", "-q", "origin", "worktree-agent-y:refs/heads/f/9-saved")
    g(repo, "fetch", "-q", "origin")  # a local tracking ref now exists
    assert wp.tip_on_remote(repo, tip)[0]
    g(repo.parent / "remote.git", "update-ref", "-d", "refs/heads/f/9-saved")  # gone on the server only
    assert g(repo, "branch", "-r", "--contains", tip)
    assert not wp.tip_on_remote(repo, tip)[0]


def test_tip_on_remote_keeps_when_the_remote_fails(repo):
    g(repo, "remote", "set-url", "origin", str(repo.parent / "nowhere.git"))
    ok, why = wp.tip_on_remote(repo, g(repo, "rev-parse", "HEAD"))
    assert not ok and "unreachable" in why


def remote_ahead(repo):
    other = repo.parent / "other"
    g(repo.parent, "clone", "-q", str(repo.parent / "remote.git"), str(other))
    commit_file(other, "n.txt")
    g(other, "push", "-q", "origin", "HEAD:refs/heads/f/7-ahead")


def test_dry_run_never_fetches_and_says_so(repo):
    remote_ahead(repo)
    wt = add_worktree(repo, "wt", "worktree-agent-z")
    local = commit_file(wt, "w.txt")
    ok, why = wp.tip_on_remote(repo, local, dry_run=True)
    assert not ok and "would fetch to confirm" in why
    assert "f/7-ahead" not in g(repo, "branch", "-r")
    ok, why = wp.remote_safe(repo, "f/7-ahead", local, dry_run=True)
    assert not ok and "would fetch to confirm" in why
    assert "f/7-ahead" not in g(repo, "branch", "-r")


def test_real_run_fetches_to_check_heads_missing_locally(repo):
    wt = add_worktree(repo, "wt", "worktree-agent-w")
    tip = commit_file(wt, "w.txt")
    g(wt, "push", "-q", "origin", "worktree-agent-w:refs/heads/f/8-base")
    other = repo.parent / "other"
    g(repo.parent, "clone", "-q", "-b", "f/8-base", str(repo.parent / "remote.git"), str(other))
    commit_file(other, "n.txt")
    g(other, "push", "-q", "origin", "f/8-base")
    ahead = g(other, "rev-parse", "HEAD")
    assert ahead != tip
    assert wp.tip_on_remote(repo, tip, dry_run=True)[0] is False  # head not local, not fetched
    assert wp.tip_on_remote(repo, tip)[0]
    assert g(repo, "cat-file", "-t", ahead) == "commit"


def test_leftover_sweep_rereads_the_worktree_list(repo, monkeypatch):
    seen = {}
    created = {}

    def fake_judge(wt, *a):
        if "path" not in created:  # another pass adds a worktree while this one judges
            path = repo / ".claude" / "worktrees" / "late"
            g(repo, "worktree", "add", "-q", "-b", "f/5-late", str(path), "main")
            created["path"] = path.resolve()

    monkeypatch.setattr(wp, "judge", fake_judge)
    monkeypatch.setattr(wp, "sweep_leftovers", lambda root, known, dry: seen.update(known=known))
    add_worktree(repo, "wt", "f/4-first")
    monkeypatch.chdir(repo)
    assert wp.main([]) == 0
    assert created["path"] in seen["known"]


def test_leftover_being_added_is_kept(repo, capsys):
    # Mid `git worktree add`: the admin dir points at the folder, the folder is still empty.
    d = repo / ".claude" / "worktrees" / "agent-new"
    d.mkdir(parents=True)
    admin = repo / ".git" / "worktrees" / "agent-new"
    admin.mkdir(parents=True)
    (admin / "gitdir").write_text(str(d / ".git") + "\n")
    wp.sweep_leftovers(repo.resolve(), set(), dry_run=False)
    assert d.exists()
    assert "KEPT" in capsys.readouterr().out


def test_leftover_whose_git_file_points_at_a_live_admin_dir_is_kept(repo):
    d = repo / ".claude" / "worktrees" / "agent-mid"
    d.mkdir(parents=True)
    admin = repo / ".git" / "worktrees" / "agent-mid"
    admin.mkdir(parents=True)
    (d / ".git").write_text(f"gitdir: {admin}\n")
    wp.sweep_leftovers(repo.resolve(), set(), dry_run=False)
    assert d.exists()


def test_true_leftovers_are_still_removed(repo):
    gone = repo / ".claude" / "worktrees" / "agent-gone"
    gone.mkdir(parents=True)
    (gone / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / 'agent-gone'}\n")
    empty = repo / ".claude" / "worktrees" / "agent-empty"
    empty.mkdir()
    wp.sweep_leftovers(repo.resolve(), set(), dry_run=False)
    assert not gone.exists() and not empty.exists()


def test_merged_head_missing_locally_is_named(repo):
    ok, why = wp.merged_safe(repo, g(repo, "rev-parse", "HEAD"),
                             [{"headRefOid": "1" * 40, "state": "MERGED"}])
    assert not ok and why == "merged head not available locally"


def test_skill_notes_the_pass_start_at_the_session_name():
    text = (ROOT / ".claude" / "skills" / "factory" / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("## Live progress")[0]
    assert "date -u +%Y-%m-%dT%H:%M:%SZ" in head and "--pass-start" in head
