"""Exam for #592: `status` and `stale` must see claims on issues that were
relabelled status:in-review (or status:needs-owner) and are no longer recent.

The fake gh honours --label like the real one: only issues carrying the label
come back, and several --label flags (or a comma list) are ANDed."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
_spec = importlib.util.spec_from_file_location("issue_lock", ROOT / "scripts" / "issue_lock.py")
issue_lock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(issue_lock)

HERE = issue_lock.HERE_REPO
OTHER = "Shashkist/factory"


def iso(minutes_ago):
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).replace(
        microsecond=0
    ).isoformat()


def claim_comment(heartbeat_min, released=False):
    claim = {"session": "lf-test", "branch": "f/9-x", "worktree": "C:/wt",
             "claimed": iso(400), "heartbeat": iso(heartbeat_min), "step": "in review"}
    if released:
        claim["released"] = iso(1)
    return {"body": issue_lock.render(claim),
            "url": "https://github.com/o/r/issues/9#issuecomment-1"}


def issue(number, labels, updated_min, heartbeat_min=5, released=False, title=None):
    return {"number": number, "title": title or "Issue " + str(number),
            "labels": labels, "updatedAt": iso(updated_min),
            "comments": [claim_comment(heartbeat_min, released)]}


class Gh:
    """repos maps repository -> list of issue dicts."""

    def __init__(self, repos):
        self.repos = repos

    def __call__(self, argv):
        repo = argv[argv.index("-R") + 1] if "-R" in argv else HERE
        issues = self.repos.get(repo, [])
        if argv[:2] == ["issue", "view"]:
            num = int(argv[2])
            found = next(i for i in issues if i["number"] == num)
            return json.dumps({"comments": found["comments"]})
        if argv[:2] == ["issue", "list"]:
            wanted = []
            for n, a in enumerate(argv):
                if a in ("--label", "-l") and n + 1 < len(argv):
                    wanted += argv[n + 1].split(",")
            out = [
                {"number": i["number"], "title": i["title"], "updatedAt": i["updatedAt"],
                 "labels": [{"name": x} for x in i["labels"]]}
                for i in issues
                if all(w in i["labels"] for w in wanted)
            ]
            return json.dumps(out)
        return ""


def ns(**over):
    base = {"json": False, "older_than": 90.0, "repos": [HERE], "default_repo": HERE,
            "clones": {}, "repo": None}
    base.update(over)
    return SimpleNamespace(**base)


def status_numbers(gh, repos):
    rows = issue_lock.claimed(repos, gh)
    return sorted((r["repo"], r["number"]) for r in rows)


def test_in_review_claim_outside_recent_window_shows_in_status(capsys):
    gh = Gh({HERE: [issue(9, ["status:in-review"], 180)]})
    assert issue_lock.cmd_status(ns(), gh) == 0
    out = capsys.readouterr().out
    assert "lf-test" in out and "No issues are claimed" not in out


def test_in_review_claim_with_old_heartbeat_shows_in_stale(capsys):
    gh = Gh({HERE: [issue(9, ["status:in-review"], 180, heartbeat_min=120)]})
    assert issue_lock.cmd_stale(ns(), gh) == 0
    out = capsys.readouterr().out
    assert "#9 Issue 9" in out and "f/9-x" in out


def test_in_review_claim_with_live_heartbeat_is_not_stale(capsys):
    gh = Gh({HERE: [issue(9, ["status:in-review"], 180, heartbeat_min=5)]})
    assert issue_lock.cmd_stale(ns(), gh) == 0
    assert "#9" not in capsys.readouterr().out


def test_needs_owner_claim_shows_in_status(capsys):
    gh = Gh({HERE: [issue(9, ["status:needs-owner"], 180)]})
    assert issue_lock.cmd_status(ns(), gh) == 0
    assert "lf-test" in capsys.readouterr().out


def test_needs_owner_claim_with_old_heartbeat_shows_in_stale(capsys):
    gh = Gh({HERE: [issue(9, ["status:needs-owner"], 180, heartbeat_min=120)]})
    assert issue_lock.cmd_stale(ns(), gh) == 0
    assert "#9 Issue 9" in capsys.readouterr().out


def test_in_review_claim_in_non_default_repository_shows_in_status(capsys):
    gh = Gh({HERE: [], OTHER: [issue(47, ["status:in-review"], 180, title="Other")]})
    assert issue_lock.cmd_status(ns(repos=[HERE, OTHER]), gh) == 0
    out = capsys.readouterr().out
    assert OTHER in out and "lf-test" in out
    assert status_numbers(gh, [HERE, OTHER]) == [(OTHER, 47)]


def test_in_review_claim_in_non_default_repository_shows_in_stale(capsys):
    gh = Gh({HERE: [], OTHER: [issue(47, ["status:in-review"], 180, heartbeat_min=120,
                                      title="Other")]})
    assert issue_lock.cmd_stale(ns(repos=[HERE, OTHER]), gh) == 0
    out = capsys.readouterr().out
    assert "#47 Other" in out and OTHER in out


def test_released_claim_on_in_review_issue_does_not_show(capsys):
    gh = Gh({HERE: [issue(9, ["status:in-review"], 180, released=True)]})
    assert issue_lock.cmd_status(ns(), gh) == 0
    assert "No issues are claimed" in capsys.readouterr().out
    assert issue_lock.claimed([HERE], gh) == []


def test_in_progress_claim_still_shows(capsys):
    gh = Gh({HERE: [issue(9, ["status:in-progress"], 180)]})
    assert issue_lock.cmd_status(ns(), gh) == 0
    assert "lf-test" in capsys.readouterr().out


def test_recently_updated_unlabelled_claim_still_shows(capsys):
    gh = Gh({HERE: [issue(9, [], 1)]})
    assert issue_lock.cmd_status(ns(), gh) == 0
    assert "lf-test" in capsys.readouterr().out


def test_old_unlabelled_issue_with_claim_stays_out(capsys):
    gh = Gh({HERE: [issue(9, ["type:bug"], 180)]})
    assert issue_lock.cmd_status(ns(), gh) == 0
    assert "No issues are claimed" in capsys.readouterr().out


def test_issue_carrying_two_status_labels_is_listed_once(capsys):
    gh = Gh({HERE: [issue(9, ["status:in-progress", "status:in-review"], 180)]})
    assert [r["number"] for r in issue_lock.claimed([HERE], gh)] == [9]
