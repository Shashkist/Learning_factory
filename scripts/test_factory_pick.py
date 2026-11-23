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


def test_parse_depends_link_and_url_name_their_repository():
    link = "Depends on [#3](https://github.com/Shashkist/factory/issues/3), #4"
    assert factory_pick.parse_depends(link, HERE) == [(OTHER, 3), (HERE, 4)]
    assert factory_pick.parse_depends("Depends on https://github.com/a/b/pull/5", HERE) == [("a/b", 5)]


def test_parse_depends_negated_prose_is_not_a_dependency():
    assert factory_pick.parse_depends("It no longer depends on #12.", HERE) == []
    assert factory_pick.parse_depends("- Depends on #12", HERE) == [(HERE, 12)]
    assert factory_pick.parse_depends("Goal. Depends on #12.", HERE) == [(HERE, 12)]


def test_cli_prints_dash_without_priority_label(monkeypatch, capsys):
    monkeypatch.setattr(issue_lock, "read_repos", lambda: ([HERE], HERE, {}))
    monkeypatch.setattr(issue_lock, "claimed", lambda repos, run, failed: [])
    run = lambda argv: json.dumps([{"number": 1, "title": "T", "createdAt": "2026-11-01T00:00:00Z",
                                    "labels": [{"name": "status:ready"}], "body": ""}])
    assert factory_pick.main([], run=run) == 0
    assert capsys.readouterr().out == f"{HERE}#1\t-\tT\n"


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
    monkeypatch.setattr(issue_lock, "claimed", lambda repos, run, failed: [])
    gh = Gh()
    assert factory_pick.main(["--in-flight", f"{HERE}#2"], run=gh) == 0
    # #1 depends on factory#7 (closed), so it is free; #2 is in flight; #3 depends on #7 of factory.
    assert capsys.readouterr().out.splitlines() == [
        f"{HERE}#1\tp1\tBlocked",
        f"{OTHER}#3\tp2\tFree",
    ]
    views = [c for c in gh.calls if c[:2] == ["issue", "view"]]
    assert len(views) == 1 and views[0][views[0].index("-R") + 1] == OTHER


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


def test_parse_depends_mid_line_bold_bullet_and_colon():
    parse = factory_pick.parse_depends
    assert parse("Part of the v0 epic #8. Spec: x. Depends on #4, #2, #3, #5, #6.", OTHER) == [
        (OTHER, n) for n in (4, 2, 3, 5, 6)]
    assert parse("**Depends on** #3", HERE) == [(HERE, 3)]
    assert parse("- Depends on #3", HERE) == [(HERE, 3)]
    assert parse("Depends on: #3", HERE) == [(HERE, 3)]


def test_parse_depends_stops_at_sentence_end_and_none():
    parse = factory_pick.parse_depends
    assert parse("Depends on: none (#543 merged). Spec: #536, D143.", HERE) == []
    assert parse("Depends on #3; see #9", HERE) == [(HERE, 3)]
    assert parse("Depends on #3\nSee #9", HERE) == [(HERE, 3)]


def test_non_incident_p0_ranks_after_incident_p0_before_p1():
    def mk(n, labels):
        return {"repo": HERE, "number": n, "labels": ["status:ready", *labels],
                "created": "2026-11-01T00:00:00Z"}
    inc = mk(1, ["priority:p0", "type:incident"])
    p0, p1, p3 = mk(2, ["priority:p0"]), mk(3, ["priority:p1"]), mk(4, ["priority:p3"])
    assert factory_pick.pick([p3, p1, p0, inc], []) == [inc, p0, p1, p3]


REPOS_YAML = f"- repo: {HERE}\n  default: true\n- repo: {OTHER}\n  default: false\n"


class GhDeps:
    def __init__(self, body, fail_view=True):
        self.body, self.fail_view = body, fail_view
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        repo = argv[argv.index("-R") + 1] if "-R" in argv else HERE
        if argv[:2] == ["issue", "list"]:
            if repo != HERE:
                return "[]"
            return json.dumps([{"number": 1, "title": "T", "createdAt": "2026-11-01T00:00:00Z",
                                "labels": [{"name": "status:ready"}, {"name": "priority:p2"}],
                                "body": self.body}])
        if self.fail_view:
            raise RuntimeError("gh failed")
        return json.dumps({"state": "CLOSED"})


def _setup(monkeypatch, tmp_path, claimed):
    f = tmp_path / "repos.yaml"
    f.write_text(REPOS_YAML, encoding="utf-8")
    monkeypatch.setenv("LF_REPOS_FILE", str(f))
    monkeypatch.setattr(issue_lock, "_claimed_issues", claimed)


def test_failed_dependency_lookup_is_open_with_one_warning(monkeypatch, capsys, tmp_path):
    _setup(monkeypatch, tmp_path, lambda run, args, failed: [])
    assert factory_pick.main([], run=GhDeps("Depends on Nope/nope#9")) == 0
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.count("Nope/nope#9") == 1


def test_dependency_repository_matches_repos_yaml_ignoring_case(monkeypatch, capsys, tmp_path):
    _setup(monkeypatch, tmp_path, lambda run, args, failed: [])
    gh = GhDeps("Depends on shashkist/FACTORY#2", fail_view=False)
    factory_pick.main([], run=gh)
    assert capsys.readouterr().out.startswith(f"{HERE}#1")
    views = [c for c in gh.calls if c[:2] == ["issue", "view"]]
    assert len(views) == 1 and views[0][views[0].index("-R") + 1] == OTHER


def test_parse_depends_url_forms():
    for dep in (
        "https://www.github.com/Shashkist/factory/issues/3",
        "http://github.com/Shashkist/factory/issues/3",
        "[#3](https://www.github.com/Shashkist/factory/issues/3)",
        "[#3](https://github.com/Shashkist/factory/issues/3/)",
        "[#3](https://github.com/Shashkist/factory/issues/3#issuecomment-99)",
    ):
        assert factory_pick.parse_depends("Depends on " + dep, HERE) == [(OTHER, 3)], dep


def test_parse_depends_task_list_item():
    for body in ("- [ ] Depends on #12", "- [x] Depends on #12", "* [X] Depends on #12"):
        assert factory_pick.parse_depends(body, HERE) == [(HERE, 12)], body


def test_repository_with_unreadable_claims_is_left_out(monkeypatch, capsys, tmp_path):
    def claimed(run, args, failed):
        failed.append(HERE)
        return []
    _setup(monkeypatch, tmp_path, claimed)
    assert factory_pick.main([], run=GhDeps("")) == 1
    assert capsys.readouterr().out == ""
