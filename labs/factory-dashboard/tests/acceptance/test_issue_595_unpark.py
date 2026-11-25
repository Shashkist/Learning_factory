"""Exam for #595 (D157): un-parking goes through `issue_lock.py unpark`, which releases the
claim naming its branch, and `status:blocked` claims stay visible like `status:needs-owner` ones.

Behaviour only. `gh` is faked at the subprocess boundary with an in-memory tracker per
repository; a call with `-R owner/name` reaches that repository, a bare call reaches this one.
The un-parking session is NOT the claim holder (a later pass), so the release must work
without --force.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
ISSUE_LOCK = ROOT / "scripts" / "issue_lock.py"
FACTORY_PICK = ROOT / "scripts" / "factory_pick.py"

HERE = "Shashkist/Learning_factory"
OTHER = "Shashkist/factory"
BRANCH = "f/9-parked-work"
CID = 4242

REPOS_YAML = f"""\
- repo: {HERE}
  default: true
  clone: C:\\projects\\Learning_factory
  ship: deploy-smoke
- repo: {OTHER}
  default: false
  clone: C:\\projects\\factory
  ship: main-ci
"""


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"{path} must exist"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iso(minutes_ago):
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat()


def claim_body(lock, released=False):
    claim = {"session": "lf-old", "branch": BRANCH, "worktree": "C:/wt",
             "claimed": iso(600), "heartbeat": iso(300), "step": "parked"}
    if released:
        claim["released"] = iso(200)
    return lock.render(claim)


class Tracker:
    """In-memory GitHub: per repository {number: {title, labels, comments, updated}}."""

    def __init__(self, repos):
        self.repos = {r: {} for r in repos}
        self.calls: list[tuple[str, list[str]]] = []

    def add(self, repo, number, labels, comments=(), updated_min=300):
        self.repos[repo][number] = {
            "title": f"Issue {number}", "labels": set(labels),
            "comments": [dict(c) for c in comments], "updated": iso(updated_min),
        }

    def writes(self, repo=None):
        out = []
        for r, argv in self.calls:
            if repo is not None and r != repo:
                continue
            if argv[:2] in (["issue", "edit"], ["issue", "comment"]) or argv[:1] == ["api"]:
                out.append(argv)
        return out

    def run(self, cmd, **_kw):
        argv = list(cmd[1:])
        assert cmd[0] == "gh", cmd
        repo, plain, i = None, [], 0
        while i < len(argv):
            if argv[i] in ("-R", "--repo") and i + 1 < len(argv):
                repo = argv[i + 1]
                i += 2
                continue
            plain.append(argv[i])
            i += 1
        if repo is None:
            for a in plain:
                if a.startswith("repos/") and not a.startswith("repos/{owner}"):
                    repo = "/".join(a.split("/")[1:3])
        reached = repo or HERE
        self.calls.append((reached, argv))
        issues = self.repos[reached]
        out = ""
        if plain[:2] == ["issue", "view"]:
            it = issues[int(plain[2])]
            out = json.dumps({
                "comments": it["comments"], "state": "OPEN", "title": it["title"],
                "labels": [{"name": x} for x in sorted(it["labels"])],
            })
        elif plain[:2] == ["issue", "list"]:
            wanted = []
            for n, a in enumerate(plain):
                if a in ("--label", "-l") and n + 1 < len(plain):
                    wanted += plain[n + 1].split(",")
            out = json.dumps([
                {"number": n, "title": it["title"], "updatedAt": it["updated"],
                 "createdAt": "2026-11-01T10:00:00Z", "body": "",
                 "labels": [{"name": x} for x in sorted(it["labels"])]}
                for n, it in issues.items() if all(w in it["labels"] for w in wanted)
            ])
        elif plain[:2] == ["issue", "comment"]:
            body = plain[plain.index("--body") + 1]
            url = f"https://github.com/{reached}/issues/{plain[2]}#issuecomment-{CID + 1}"
            issues[int(plain[2])]["comments"].append({"body": body, "url": url})
        elif plain[:1] == ["api"]:
            cid = next(a for a in plain if "/comments/" in a).rsplit("/", 1)[1]
            body = next(a for a in plain if a.startswith("body="))[5:]
            for it in issues.values():
                for c in it["comments"]:
                    if c["url"].endswith("issuecomment-" + cid):
                        c["body"] = body
        elif plain[:2] == ["issue", "edit"]:
            labels = issues[int(plain[2])]["labels"]
            for flag, val in zip(plain, plain[1:], strict=False):
                for one in val.split(","):
                    if flag == "--add-label":
                        labels.add(one)
                    if flag == "--remove-label":
                        labels.discard(one)
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("LF_CREW_DIR", raising=False)
    repos_file = tmp_path / "repos.yaml"
    repos_file.write_text(REPOS_YAML, encoding="utf-8")
    monkeypatch.setenv("LF_REPOS_FILE", str(repos_file))
    tracker = Tracker([HERE, OTHER])
    monkeypatch.setattr(subprocess, "run", tracker.run)
    w = SimpleNamespace()
    w.lock = load(ISSUE_LOCK, "issue_lock_exam595")
    w.tracker = tracker
    return w


def live_comment(w):
    return {"body": claim_body(w.lock), "url": f"https://github.com/o/r/issues/9#issuecomment-{CID}"}


def released_comment(w):
    return {"body": claim_body(w.lock, released=True),
            "url": f"https://github.com/o/r/issues/9#issuecomment-{CID}"}


def unpark(w, *extra):
    return w.lock.main(["unpark", "9", "--session", "lf-new", *extra])


def labels_of(w, repo=HERE):
    return w.tracker.repos[repo][9]["labels"]


def only_comment(w, repo=HERE):
    comments = w.tracker.repos[repo][9]["comments"]
    assert len(comments) == 1, "the claim comment is edited in place, not stacked"
    return comments[0]


# ---- unpark with a live claim --------------------------------------------------------------------

@pytest.mark.parametrize("parked", ["status:needs-owner", "status:blocked"])
def test_unpark_releases_a_live_claim_naming_the_branch(world, capsys, parked):
    world.tracker.add(HERE, 9, [parked, "type:bug"], [live_comment(world)])
    assert unpark(world) == 0
    claim = world.lock.parse_claim(only_comment(world)["body"])
    assert claim is not None and "released" in claim
    assert f"un-parked; continue branch {BRANCH}" in claim["reason"]
    assert BRANCH in capsys.readouterr().out


@pytest.mark.parametrize("parked", ["status:needs-owner", "status:blocked"])
def test_unpark_writes_the_retraction_through_the_claims_comment_id(world, parked):
    world.tracker.add(HERE, 9, [parked], [live_comment(world)])
    assert unpark(world) == 0
    patches = [a for a in world.tracker.writes(HERE) if a[:1] == ["api"]]
    assert len(patches) == 1 and f"/comments/{CID}" in " ".join(patches[0])
    assert not [a for a in world.tracker.writes(HERE) if a[:2] == ["issue", "comment"]]


@pytest.mark.parametrize("parked", ["status:needs-owner", "status:blocked"])
def test_unpark_relabels_to_ready_with_one_edit(world, parked):
    world.tracker.add(HERE, 9, [parked, "type:bug", "priority:p2"], [live_comment(world)])
    assert unpark(world) == 0
    assert labels_of(world) == {"status:ready", "type:bug", "priority:p2"}
    edits = [a for a in world.tracker.writes(HERE) if a[:2] == ["issue", "edit"]]
    assert len(edits) == 1


def test_unpark_removes_both_parked_labels_when_an_issue_carries_both(world):
    world.tracker.add(HERE, 9, ["status:needs-owner", "status:blocked"], [live_comment(world)])
    assert unpark(world) == 0
    assert labels_of(world) == {"status:ready"}


# ---- unpark without a live claim -----------------------------------------------------------------

def test_unpark_without_a_claim_only_relabels_and_says_so(world, capsys):
    world.tracker.add(HERE, 9, ["status:needs-owner"], [{"body": "just a comment", "url": ""}])
    assert unpark(world) == 0
    assert labels_of(world) == {"status:ready"}
    assert [a[:2] for a in world.tracker.writes(HERE)] == [["issue", "edit"]]
    assert "no claim" in capsys.readouterr().out.lower()
    assert world.tracker.repos[HERE][9]["comments"] == [{"body": "just a comment", "url": ""}]


def test_unpark_with_an_already_released_claim_only_relabels(world):
    world.tracker.add(HERE, 9, ["status:blocked"], [released_comment(world)])
    before = released_comment(world)["body"]
    assert unpark(world) == 0
    assert labels_of(world) == {"status:ready"}
    assert [a[:2] for a in world.tracker.writes(HERE)] == [["issue", "edit"]]
    assert only_comment(world)["body"] == before


# ---- refusal -------------------------------------------------------------------------------------

@pytest.mark.parametrize("labels", [["status:ready"], ["status:in-progress", "type:bug"], []])
def test_unpark_refuses_an_issue_that_is_not_parked_and_writes_nothing(world, labels):
    world.tracker.add(HERE, 9, labels, [live_comment(world)])
    body = only_comment(world)["body"]
    assert unpark(world) == 1
    assert world.tracker.writes() == []
    assert labels_of(world) == set(labels)
    assert only_comment(world)["body"] == body


# ---- --repo --------------------------------------------------------------------------------------

def test_unpark_with_repo_acts_on_the_other_repository_only(world, capsys):
    world.tracker.add(OTHER, 9, ["status:needs-owner"], [live_comment(world)])
    world.tracker.add(HERE, 9, ["status:needs-owner"], [live_comment(world)])
    assert unpark(world, "--repo", OTHER) == 0
    claim = world.lock.parse_claim(only_comment(world, OTHER)["body"])
    assert claim is not None and "released" in claim and BRANCH in claim["reason"]
    assert labels_of(world, OTHER) == {"status:ready"}
    assert BRANCH in capsys.readouterr().out
    assert world.tracker.writes(HERE) == []
    assert labels_of(world, HERE) == {"status:needs-owner"}
    assert "released" not in world.lock.parse_claim(only_comment(world, HERE)["body"])


def test_unpark_parser_takes_session_and_repo_like_the_other_subcommands(world):
    args = world.lock.build_parser().parse_args(
        ["unpark", "9", "--session", "s", "--repo", OTHER])
    assert args.number == 9 and args.session == "s" and args.repo == OTHER


# ---- status:blocked claims stay visible ----------------------------------------------------------

def test_claim_on_old_blocked_issue_shows_in_status(world, capsys):
    world.tracker.add(HERE, 9, ["status:blocked"], [live_comment(world)], updated_min=240)
    args = SimpleNamespace(json=False, older_than=90.0, repos=[HERE], default_repo=HERE,
                           clones={}, repo=None)
    assert world.lock.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "lf-old" in out and "No issues are claimed" not in out
    assert [(r["repo"], r["number"]) for r in world.lock.claimed([HERE])] == [(HERE, 9)]


def test_claim_on_old_unlabelled_issue_stays_out_of_status(world):
    world.tracker.add(HERE, 9, ["type:bug"], [live_comment(world)], updated_min=240)
    assert world.lock.claimed([HERE]) == []


def test_claim_on_blocked_issue_is_left_out_of_factory_pick(world, capsys):
    pick = load(FACTORY_PICK, "factory_pick_exam595")
    # Relabelled ready by hand while still blocked: the claim must keep it off the list.
    world.tracker.add(HERE, 9, ["status:ready", "status:blocked"], [live_comment(world)],
                      updated_min=240)
    # Control: the same shape without the blocked label and without a claim is offered.
    world.tracker.add(HERE, 10, ["status:ready"], [], updated_min=240)
    assert pick.main(["--repo", HERE]) == 0
    out = capsys.readouterr().out
    assert f"{HERE}#10" in out
    assert f"{HERE}#9" not in out


def test_old_claim_on_plain_ready_issue_is_the_gap_the_unpark_closes(world, capsys):
    """Harness sanity: with no parked label an hours-old claim is not read, which is
    why un-parking must release it."""
    pick = load(FACTORY_PICK, "factory_pick_exam595b")
    world.tracker.add(HERE, 9, ["status:ready"], [live_comment(world)], updated_min=240)
    assert pick.main(["--repo", HERE]) == 0
    assert re.search(rf"{re.escape(HERE)}#9\b", capsys.readouterr().out)
