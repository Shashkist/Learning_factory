"""Unit tests for factory_pick (#544): the CLI with gh faked, and edge cases of pick."""

import json

import factory_pick
import issue_lock

HERE = issue_lock.HERE_REPO
OTHER = "Shashkist/factory"


def test_pick_without_priority_label_goes_last():
    a = {"repo": HERE, "number": 1, "labels": ["status:ready"], "created": "2026-11-01T00:00:00Z"}
    b = {"repo": HERE, "number": 2, "labels": ["status:ready", "priority:p3"],
         "created": "2026-11-09T00:00:00Z"}
    assert factory_pick.pick([a, b], []) == [b, a]


def test_parse_depends_ignores_non_dependency_lines():
    assert factory_pick.parse_depends("See Shashkist/factory#9\nDepends on #2", HERE) == [(HERE, 2)]


class Gh:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        repo = argv[argv.index("-R") + 1] if "-R" in argv else HERE
        if argv[:2] == ["issue", "list"]:
            if repo == HERE:
                rows = [
                    {"number": 1, "title": "Blocked", "createdAt": "2026-11-01T00:00:00Z",
                     "labels": [{"name": "status:ready"}, {"name": "priority:p1"}],
                     "body": "Depends on Shashkist/factory#7"},
                    {"number": 2, "title": "Claimed", "createdAt": "2026-11-01T00:00:00Z",
                     "labels": [{"name": "status:ready"}, {"name": "priority:p1"}], "body": ""},
                ]
            else:
                rows = [{"number": 3, "title": "Free", "createdAt": "2026-11-02T00:00:00Z",
                         "labels": [{"name": "status:ready"}, {"name": "priority:p2"}],
                         "body": "Depends on #7"}]
            return json.dumps(rows)
        if argv[:2] == ["issue", "view"]:
            return json.dumps({"state": "OPEN" if argv[argv.index("-R") + 1] == HERE else "CLOSED"})
        return ""


def test_cli_prints_order_and_caches_dependency_lookups(monkeypatch, capsys, tmp_path):
    repos = tmp_path / "repos.yaml"
    repos.write_text(
        f"- repo: {HERE}\n  default: true\n- repo: {OTHER}\n  default: false\n", encoding="utf-8"
    )
    monkeypatch.setenv("LF_REPOS_FILE", str(repos))
    monkeypatch.setattr(issue_lock, "_claimed_issues", lambda run, args, failed: [])
    gh = Gh()
    assert factory_pick.main(["--in-flight", f"{HERE}#2"], run=gh) == 0
    # #1 depends on factory#7 (closed), so it is free; #2 is in flight; #3 depends on #7 of factory.
    assert capsys.readouterr().out.splitlines() == [
        f"{HERE}#1\tp1\tBlocked",
        f"{OTHER}#3\tp2\tFree",
    ]
    views = [c for c in gh.calls if c[:2] == ["issue", "view"]]
    assert len(views) == 1 and "-R" in views[0]


def test_cli_excludes_claimed_issues_and_reports_unreadable_repo(monkeypatch, capsys, tmp_path):
    repos = tmp_path / "repos.yaml"
    repos.write_text(
        f"- repo: {HERE}\n  default: true\n- repo: {OTHER}\n  default: false\n", encoding="utf-8"
    )
    monkeypatch.setenv("LF_REPOS_FILE", str(repos))
    monkeypatch.setattr(issue_lock, "_claimed_issues",
                        lambda run, args, failed: [{"repo": HERE, "number": 1}])
    gh = Gh()

    def run(argv):
        if "-R" in argv and argv[argv.index("-R") + 1] == OTHER and argv[0] == "issue" and argv[1] == "list":
            raise RuntimeError("boom")
        return gh(argv)

    assert factory_pick.main([], run=run) == 1
    out = capsys.readouterr()
    assert out.out.strip() == f"{HERE}#2\tp1\tClaimed"
    assert OTHER in out.err
