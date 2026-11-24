"""Unit tests for the pure decisions of scripts/worktree_prune.py (#577)."""

import worktree_prune as wp


def pr(state, merged=None):
    return {"number": 1, "state": state, "mergedAt": merged}


def test_merged_is_removed():
    assert wp.decide_feature([pr("MERGED", "2026-11-20T10:00:00Z")], False, False)[0]


def test_closed_needs_the_remote():
    assert wp.decide_feature([pr("CLOSED")], False, True)[0]
    assert not wp.decide_feature([pr("CLOSED")], False, False)[0]


def test_open_none_and_claim_keep():
    assert not wp.decide_feature([pr("OPEN")], False, True)[0]
    assert not wp.decide_feature([], False, True)[0]
    assert not wp.decide_feature([pr("MERGED")], True, True)[0]


def test_subagent_needs_pass_start_age_and_pushed_tip():
    assert not wp.decide_subagent(100.0, None, True)[0]
    assert not wp.decide_subagent(200.0, 100.0, True)[0]
    assert not wp.decide_subagent(50.0, 100.0, False)[0]
    assert wp.decide_subagent(50.0, 100.0, True)[0]


def test_parse_worktrees_strips_branch_prefix():
    text = "worktree /a\nHEAD x\nbranch refs/heads/main\n\nworktree /b\nHEAD y\ndetached\n"
    trees = wp.parse_worktrees(text)
    assert [t["branch"] for t in trees] == ["main", ""]
    assert trees[1]["worktree"] == "/b"


def test_parse_iso_accepts_z_and_naive():
    assert wp.parse_iso("2026-11-20T10:00:00Z") == wp.parse_iso("2026-11-20T10:00:00")
