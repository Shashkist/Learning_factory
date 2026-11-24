"""Unit tests for the pure decisions of scripts/worktree_prune.py (#577)."""

import worktree_prune as wp


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
