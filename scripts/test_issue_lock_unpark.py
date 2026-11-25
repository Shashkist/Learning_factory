"""Unit tests for `issue_lock.py unpark` (#595)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import issue_lock

CLAIM = {"session": "old", "branch": "f/1-x", "claimed": "2026-01-01T00:00:00+00:00",
         "heartbeat": "2026-01-01T00:00:00+00:00", "step": "parked"}


def make_run(labels, claim=None):
    calls = []
    comments = []
    if claim is not None:
        comments.append({"body": issue_lock.render(claim),
                         "url": "https://x/issues/1#issuecomment-77"})

    def run(argv):
        calls.append(argv)
        if argv[:2] == ["issue", "view"] and "labels" in argv:
            return json.dumps({"labels": [{"name": x} for x in labels]})
        if argv[:2] == ["issue", "view"]:
            return json.dumps({"comments": comments})
        return ""

    return run, calls


def ns():
    return argparse.Namespace(number=1, session="s", repo=None)


def writes(calls):
    return [c for c in calls if c[0] == "api" or c[:2] in (["issue", "edit"], ["issue", "comment"])]


def test_refuses_unparked_and_writes_nothing():
    run, calls = make_run(["status:ready"], CLAIM)
    assert issue_lock.cmd_unpark(ns(), run) == 1
    assert writes(calls) == []


def test_releases_claim_naming_branch_and_relabels(capsys):
    run, calls = make_run(["status:blocked"], CLAIM)
    assert issue_lock.cmd_unpark(ns(), run) == 0
    assert "f/1-x" in capsys.readouterr().out
    patch = [c for c in calls if c[0] == "api"]
    assert len(patch) == 1 and "un-parked; continue branch f/1-x" in " ".join(patch[0])
    edit = [c for c in calls if c[:2] == ["issue", "edit"]]
    assert len(edit) == 1 and "status:ready" in edit[0]


def test_no_claim_only_relabels(capsys):
    run, calls = make_run(["status:needs-owner"])
    assert issue_lock.cmd_unpark(ns(), run) == 0
    assert "no claim" in capsys.readouterr().out
    assert [c[:2] for c in writes(calls)] == [["issue", "edit"]]
