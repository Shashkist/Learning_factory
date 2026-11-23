"""Exam for issue #544: pick order across repositories (slice 3 of #536, D143).

Pure functions only: no network, no `gh`. The exam pins this contract in scripts/factory_pick.py:

  pick(issues, in_flight) -> list
      issues     list of dicts, one per OPEN issue of any listed repository:
                   {"repo": "Shashkist/factory", "number": 7,
                    "labels": ["status:ready", "priority:p1", "type:feature"],   # label names
                    "created": "2026-11-20T10:00:00Z",                           # ISO 8601 UTC
                    "depends": [{"repo": "Shashkist/factory", "number": 3, "state": "open"}]}
                 `state` is "open" or "closed" (lower case). `depends` may be missing or empty.
      in_flight  iterable of (repo, number) tuples already claimed or being worked. Those issues are
                 left out of the result. Same number in another repository is NOT excluded.
      returns    the picked issue dicts themselves (same objects, not copies), best first.

  Only issues labelled `status:ready` are picked. Order: `type:incident` + `priority:p0` first (any
  repository), then priority:p1 > p2 > p3 across repositories; an issue with any open dependency is
  left out; within equal priority the oldest `created` comes first.

  parse_depends(body, repo) -> list of (repo, number) tuples, in order of appearance, no duplicates.
      "Depends on #3" resolves to (repo, 3), where `repo` is the repository the issue lives in.
      "Depends on Shashkist/factory#3" resolves to ("Shashkist/factory", 3).
      A body without a dependency line returns [].

Criteria: 1 order (p0 incident, p1 across repos, open dependency, oldest), 2 dependency parsing,
3 skill and FACTORY.md text, 4 this file.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "factory_pick.py"
SKILL = ROOT / ".claude" / "skills" / "factory" / "SKILL.md"
FACTORY_MD = ROOT / "docs" / "factory" / "FACTORY.md"

HERE = "Shashkist/Learning_factory"
OTHER = "Shashkist/factory"


@pytest.fixture(autouse=True)
def _no_crew_dir(monkeypatch):
    monkeypatch.delenv("LF_CREW_DIR", raising=False)


def load():
    spec = importlib.util.spec_from_file_location("factory_pick_exam544", SCRIPT)
    assert spec and spec.loader, "scripts/factory_pick.py must exist"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def issue(repo, number, prio="p2", day=1, extra=(), status="status:ready", depends=None):
    d = {
        "repo": repo,
        "number": number,
        "labels": [status, f"priority:{prio}", "type:feature", *extra],
        "created": f"2026-11-{day:02d}T10:00:00Z",
    }
    if depends is not None:
        d["depends"] = depends
    return d


def order(result):
    return [(i["repo"], i["number"]) for i in result]


def test_p0_incident_of_either_repository_comes_first():
    pick = load().pick
    for inc_repo in (HERE, OTHER):
        other = OTHER if inc_repo == HERE else HERE
        issues = [
            issue(other, 1, "p1", day=1),
            issue(inc_repo, 2, "p0", day=20, extra=["type:incident"]),
            issue(other, 3, "p1", day=2),
        ]
        assert order(pick(issues, []))[0] == (inc_repo, 2)


def test_p1_of_other_repository_comes_before_p2_here():
    pick = load().pick
    issues = [issue(HERE, 10, "p2", day=1), issue(OTHER, 11, "p1", day=15)]
    assert order(pick(issues, [])) == [(OTHER, 11), (HERE, 10)]


def test_priority_order_p1_p2_p3_across_repositories():
    pick = load().pick
    issues = [
        issue(HERE, 1, "p3", day=1),
        issue(OTHER, 2, "p2", day=2),
        issue(HERE, 3, "p1", day=3),
        issue(OTHER, 4, "p3", day=4),
        issue(HERE, 5, "p2", day=5),
        issue(OTHER, 6, "p1", day=6),
    ]
    assert order(pick(issues, [])) == [
        (HERE, 3), (OTHER, 6), (OTHER, 2), (HERE, 5), (HERE, 1), (OTHER, 4),
    ]


def test_issue_with_open_dependency_is_left_out():
    pick = load().pick
    blocked = issue(HERE, 1, "p1", day=1, depends=[{"repo": OTHER, "number": 3, "state": "open"}])
    free = issue(HERE, 2, "p3", day=2)
    assert order(pick([blocked, free], [])) == [(HERE, 2)]


def test_issue_with_only_closed_dependencies_is_picked():
    pick = load().pick
    ok = issue(HERE, 1, "p1", depends=[{"repo": OTHER, "number": 3, "state": "closed"}])
    mixed = issue(HERE, 2, "p1", depends=[
        {"repo": HERE, "number": 4, "state": "closed"},
        {"repo": OTHER, "number": 5, "state": "open"},
    ])
    assert order(pick([ok, mixed], [])) == [(HERE, 1)]


def test_equal_priority_goes_oldest_first_across_repositories():
    pick = load().pick
    issues = [
        issue(HERE, 1, "p2", day=9),
        issue(OTHER, 2, "p2", day=3),
        issue(HERE, 3, "p2", day=5),
    ]
    assert order(pick(issues, [])) == [(OTHER, 2), (HERE, 3), (HERE, 1)]


def test_only_status_ready_issues_are_picked():
    pick = load().pick
    issues = [
        issue(HERE, 1, "p1", status="status:in-progress"),
        issue(HERE, 2, "p1", status="status:needs-owner"),
        issue(OTHER, 3, "p1", status="status:blocked"),
        issue(OTHER, 4, "p3"),
    ]
    assert order(pick(issues, [])) == [(OTHER, 4)]


def test_in_flight_issues_are_excluded_by_repository_and_number():
    pick = load().pick
    issues = [issue(HERE, 5, "p1"), issue(OTHER, 5, "p1", day=2)]
    assert order(pick(issues, [(HERE, 5)])) == [(OTHER, 5)]
    assert pick(issues, {(HERE, 5), (OTHER, 5)}) == []


def test_pick_returns_the_input_objects_and_does_not_mutate_input():
    pick = load().pick
    a, b = issue(HERE, 1, "p2", day=2), issue(OTHER, 2, "p1", day=1)
    issues = [a, b]
    result = pick(issues, [])
    assert result[0] is b and result[1] is a
    assert issues == [a, b]


def test_empty_input_gives_empty_list():
    assert load().pick([], []) == []


def test_depends_on_number_resolves_against_the_issue_own_repository():
    parse = load().parse_depends
    assert parse("Goal.\n\nDepends on #3", OTHER) == [(OTHER, 3)]
    assert parse("Depends on #3", HERE) == [(HERE, 3)]


def test_depends_on_qualified_reference_resolves_against_that_repository():
    parse = load().parse_depends
    assert parse("Depends on Shashkist/factory#3", HERE) == [(OTHER, 3)]
    assert parse("Depends on Shashkist/Learning_factory#12", OTHER) == [(HERE, 12)]


def test_depends_mixed_and_multiple_references_keep_order_without_duplicates():
    parse = load().parse_depends
    body = "Text.\nDepends on #3 and Shashkist/factory#4\nDepends on #3\n"
    assert parse(body, HERE) == [(HERE, 3), (OTHER, 4)]


def test_body_without_dependency_gives_empty_list():
    parse = load().parse_depends
    assert parse("Fixes the thing, see #9 for background.", HERE) == []
    assert parse("", HERE) == []


@pytest.mark.parametrize("path", [SKILL, FACTORY_MD], ids=["skill", "FACTORY.md"])
def test_skill_and_factory_md_tell_to_use_factory_pick_with_repo(path):
    text = path.read_text(encoding="utf-8")
    assert "factory_pick.py" in text, f"{path.name} must mention factory_pick.py"
    assert "--repo" in text, f"{path.name} must mention --repo"
