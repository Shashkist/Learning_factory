"""Exam for #595, review round 1 (D158): `claim` keeps the branch an un-park named, `unpark`
releases every live claim, writes the release before the relabel, prints the branch even when
the relabel fails, records a missing branch, and refuses another session's fresh claim without
--force; `stale` reads old `status:blocked` claims.

Same harness as test_issue_595_unpark.py (loaded by path): `gh` faked at the subprocess boundary.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE_DIR = Path(__file__).resolve().parent


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"{path} must exist"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = _load(HERE_DIR / "test_issue_595_unpark.py", "exam595_base")
HERE, BRANCH, CID, iso = base.HERE, base.BRANCH, base.CID, base.iso
RELEASE_REASON = f"un-parked; continue branch {BRANCH}"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("LF_CREW_DIR", raising=False)
    repos_file = tmp_path / "repos.yaml"
    repos_file.write_text(base.REPOS_YAML, encoding="utf-8")
    monkeypatch.setenv("LF_REPOS_FILE", str(repos_file))
    tracker = base.Tracker([HERE, base.OTHER])
    monkeypatch.setattr(subprocess, "run", tracker.run)
    w = SimpleNamespace()
    w.lock = base.load(base.ISSUE_LOCK, "issue_lock_exam595r1")
    w.tracker = tracker
    w.monkeypatch = monkeypatch
    return w


def url(cid=CID):
    return f"https://github.com/o/r/issues/9#issuecomment-{cid}"


def raw_claim(w, claim, cid=CID):
    """A claim comment body from a dict, exactly as given (no branch key added)."""
    body = w.lock.MARKER + "\n" + json.dumps(claim) + "\n" + w.lock.END + "\nclaim"
    return {"body": body, "url": url(cid)}


def old_claim(session="lf-old", **over):
    c = {"session": session, "branch": BRANCH, "worktree": "C:/wt", "claimed": iso(600),
         "heartbeat": iso(300), "step": "parked"}
    c.update(over)
    return c


def release_comment(w, reason, cid=CID):
    return {"body": w.lock.retraction("lf-old", reason), "url": url(cid)}


def claim(w, branch, *extra):
    return w.lock.main(["claim", "9", "--session", "lf-new", "--branch", branch, *extra])


def parsed(w, index=0):
    return w.lock.parse_claim(w.tracker.repos[HERE][9]["comments"][index]["body"])


# ---- claim keeps the branch an un-park named -----------------------------------------------------

def test_claim_on_a_released_unpark_with_another_branch_is_refused_naming_the_branch(
        world, capsys):
    world.tracker.add(HERE, 9, ["status:ready"], [release_comment(world, RELEASE_REASON)])
    before = world.tracker.repos[HERE][9]["comments"][0]["body"]
    assert claim(world, "f/9-other") == 1
    out = capsys.readouterr()
    assert BRANCH in out.out + out.err
    assert world.tracker.writes() == []
    assert world.tracker.repos[HERE][9]["comments"][0]["body"] == before
    assert world.tracker.repos[HERE][9]["labels"] == {"status:ready"}


def test_claim_on_a_released_unpark_with_the_named_branch_is_taken(world):
    world.tracker.add(HERE, 9, ["status:ready"], [release_comment(world, RELEASE_REASON)])
    assert claim(world, BRANCH) == 0
    c = parsed(world)
    assert c["session"] == "lf-new" and c["branch"] == BRANCH and "released" not in c
    assert "status:in-progress" in world.tracker.repos[HERE][9]["labels"]


def test_claim_with_steal_and_reason_overrides_the_unpark_branch(world):
    world.tracker.add(HERE, 9, ["status:ready"], [release_comment(world, RELEASE_REASON)])
    assert claim(world, "f/9-other", "--steal", "--reason", "branch is unusable") == 0
    c = parsed(world)
    assert c["session"] == "lf-new" and c["branch"] == "f/9-other" and "released" not in c


def test_claim_after_an_ordinary_release_is_not_blocked(world):
    world.tracker.add(HERE, 9, ["status:ready"], [release_comment(world, "shipped")])
    assert claim(world, "f/9-other") == 0
    assert parsed(world)["branch"] == "f/9-other"


# ---- stale reads status:blocked ------------------------------------------------------------------

def test_stale_lists_an_hours_old_claim_on_a_blocked_issue(world, capsys):
    world.tracker.add(HERE, 9, ["status:blocked"], [raw_claim(world, old_claim())],
                      updated_min=240)
    assert world.lock.main(["stale"]) == 0
    out = capsys.readouterr().out
    assert "#9" in out and "lf-old" in out and BRANCH in out


# ---- unpark: order, output, every claim, missing branch, fresh claim -----------------------------

def test_unpark_writes_the_release_before_the_relabel(world):
    world.tracker.add(HERE, 9, ["status:needs-owner"], [raw_claim(world, old_claim())])
    assert world.lock.main(["unpark", "9", "--session", "lf-new"]) == 0
    kinds = [("patch" if argv[:1] == ["api"] else "edit")
             for _, argv in world.tracker.calls
             if argv[:1] == ["api"] or argv[:2] == ["issue", "edit"]]
    assert kinds == ["patch", "edit"]


def test_unpark_prints_the_branch_even_when_the_relabel_fails(world, capsys):
    world.tracker.add(HERE, 9, ["status:needs-owner"], [raw_claim(world, old_claim())])
    real = world.tracker.run

    def failing(cmd, **kw):
        if list(cmd[1:3]) == ["issue", "edit"]:
            raise RuntimeError("gh issue edit failed: boom")
        return real(cmd, **kw)

    world.monkeypatch.setattr(subprocess, "run", failing)
    try:
        world.lock.main(["unpark", "9", "--session", "lf-new"])
    except RuntimeError:
        pass
    assert BRANCH in capsys.readouterr().out
    assert "released" in parsed(world)


def test_unpark_releases_every_live_claim(world):
    comments = [raw_claim(world, old_claim("lf-old"), CID),
                raw_claim(world, old_claim("lf-two", branch="f/9-second"), CID + 7)]
    world.tracker.add(HERE, 9, ["status:blocked"], comments)
    assert world.lock.main(["unpark", "9", "--session", "lf-new"]) == 0
    for i, branch in enumerate([BRANCH, "f/9-second"]):
        c = parsed(world, i)
        assert "released" in c and branch in c["reason"]
    assert world.tracker.repos[HERE][9]["labels"] == {"status:ready"}


def test_unpark_of_a_claim_without_a_branch_says_no_branch_recorded(world):
    c = old_claim()
    del c["branch"]
    world.tracker.add(HERE, 9, ["status:needs-owner"], [raw_claim(world, c)])
    assert world.lock.main(["unpark", "9", "--session", "lf-new"]) == 0
    released = parsed(world)
    assert "released" in released and "(no branch recorded)" in released["reason"]


def test_unpark_of_anothers_fresh_claim_is_refused_naming_the_holder(world, capsys):
    fresh = raw_claim(world, old_claim(heartbeat=iso(10)))
    world.tracker.add(HERE, 9, ["status:needs-owner"], [fresh])
    assert world.lock.main(["unpark", "9", "--session", "lf-new"]) == 1
    out = capsys.readouterr()
    assert "lf-old" in out.out + out.err
    assert world.tracker.writes() == []
    assert world.tracker.repos[HERE][9]["comments"][0]["body"] == fresh["body"]
    assert world.tracker.repos[HERE][9]["labels"] == {"status:needs-owner"}


def test_unpark_of_anothers_fresh_claim_with_force_releases_it(world):
    world.tracker.add(HERE, 9, ["status:needs-owner"],
                      [raw_claim(world, old_claim(heartbeat=iso(10)))])
    assert world.lock.main(["unpark", "9", "--session", "lf-new", "--force"]) == 0
    c = parsed(world)
    assert "released" in c and BRANCH in c["reason"]
    assert world.tracker.repos[HERE][9]["labels"] == {"status:ready"}


def test_unpark_of_ones_own_fresh_claim_needs_no_force(world):
    world.tracker.add(HERE, 9, ["status:needs-owner"],
                      [raw_claim(world, old_claim("lf-new", heartbeat=iso(10)))])
    assert world.lock.main(["unpark", "9", "--session", "lf-new"]) == 0
    assert "released" in parsed(world)
