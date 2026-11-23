"""Exam for issue #556: pick order follow-ups (review of #553, #544, D143, D144).

Observable behaviour only: `scripts/factory_pick.py` run as a CLI with a fake `gh`, the claim
state `scripts/issue_lock.py` reads through a fake `gh`, and the text of the factory skill.

Contract pinned here:
  - parse_depends(body, repo): a link `[#3](https://github.com/OWNER/NAME/issues/3)` or a plain
    issue URL resolves to the repository in the URL; a bare `#n` still resolves to `repo`.
  - "Depends on" counts at the start of a sentence, a list item or bold text; negated prose
    ("no longer depends on #12") is not a dependency.
  - an issue without a `priority:` label prints `-` as its priority and sorts after p3.
  - issue_lock.claimed(repos, run=...) returns the claim rows (dicts with `repo` and `number`);
    it agrees with `issue_lock status --json` for the same fake claim state.
  - SKILL.md names the per-repository exam path and ship evidence.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "scripts"
SKILL = ROOT / ".claude" / "skills" / "factory" / "SKILL.md"

HERE = "Shashkist/Learning_factory"
OTHER = "Shashkist/factory"
REPOS_YAML = f"- repo: {HERE}\n  default: true\n- repo: {OTHER}\n  default: false\n"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.delenv("LF_CREW_DIR", raising=False)
    f = tmp_path / "repos.yaml"
    f.write_text(REPOS_YAML, encoding="utf-8")
    monkeypatch.setenv("LF_REPOS_FILE", str(f))


def load(name, tag):
    """Load a script by path (it imports its sibling issue_lock, so scripts/ is on sys.path)."""
    path = SCRIPTS / f"{name}.py"
    assert path.exists(), f"scripts/{name}.py must exist"
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"{name}_exam556_{tag}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Gh:
    """Fake gh. `issues`: repo -> list of (number, labels, body).
    `states`: (repo, n) -> OPEN/CLOSED.
    `claims`: repo -> {number: claim dict} (claim comments; also listed under the claim label)."""

    def __init__(self, issues, states=None, claims=None):
        self.issues, self.states, self.claims = issues, states or {}, claims or {}
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        repo = argv[argv.index("-R") + 1] if "-R" in argv else HERE
        if argv[:2] == ["issue", "list"]:
            if "--label" in argv:
                return json.dumps([{"number": n, "title": f"claimed {n}"}
                                   for n in self.claims.get(repo, {})])
            if "body" in argv[argv.index("--json") + 1]:
                return json.dumps([
                    {"number": n, "title": f"T{n}", "createdAt": f"2026-11-{n:02d}T00:00:00Z",
                     "labels": [{"name": lab} for lab in labels], "body": body}
                    for n, labels, body in self.issues.get(repo, [])
                ])
            return "[]"
        if argv[:2] == ["issue", "view"]:
            n = int(argv[2])
            if "comments" in argv[argv.index("--json") + 1]:
                claim = self.claims.get(repo, {}).get(n)
                comments = []
                if claim:
                    body = ("<!-- pulsesign-issue-claim\n" + json.dumps(claim) + "\n-->\nClaimed")
                    comments = [{"body": body,
                                 "url": f"https://github.com/{repo}/issues/{n}#issuecomment-{n}1"}]
                return json.dumps({"comments": comments})
            return json.dumps({"state": self.states.get((repo, n), "OPEN")})
        return ""


def run_pick(gh, capsys, args=()):
    pick = load("factory_pick", "cli")
    code = pick.main(list(args), run=gh)
    out = capsys.readouterr()
    rows = [line.split("\t") for line in out.out.splitlines() if line.strip()]
    return code, rows


def ready(*extra):
    return ["status:ready", *extra]


# ---------------------------------------------------------------- 1. dependency as a link

LINK = "Depends on [#3](https://github.com/Shashkist/factory/issues/3)"
URL = "Depends on https://github.com/Shashkist/factory/issues/3"


@pytest.mark.parametrize(
    "body", [LINK, URL, "Goal.\n\n" + LINK + "\n"], ids=["link", "url", "link-late"])
def test_dependency_link_or_url_resolves_to_the_repository_in_the_url(body):
    parse = load("factory_pick", "parse").parse_depends
    assert parse(body, HERE) == [(OTHER, 3)]
    assert parse(body, OTHER) == [(OTHER, 3)]


def test_bare_number_still_resolves_to_the_issue_own_repository():
    parse = load("factory_pick", "parse").parse_depends
    assert parse("Depends on #3", HERE) == [(HERE, 3)]
    assert parse("Depends on #3 and https://github.com/Shashkist/factory/issues/4", HERE) == [
        (HERE, 3), (OTHER, 4)]


@pytest.mark.parametrize("body", [LINK, URL], ids=["link", "url"])
def test_issue_depending_on_open_issue_of_other_repository_by_link_is_left_out(body, capsys):
    gh = Gh({HERE: [(1, ready("priority:p1"), body), (2, ready("priority:p3"), "")]},
            states={(OTHER, 3): "OPEN", (HERE, 3): "CLOSED"})
    code, rows = run_pick(gh, capsys)
    assert [r[0] for r in rows] == [f"{HERE}#2"]
    views = [c for c in gh.calls if c[:2] == ["issue", "view"] and "state" in c[-1]]
    assert views and all("-R" in c and c[c.index("-R") + 1] == OTHER for c in views)


@pytest.mark.parametrize("body", [LINK, URL], ids=["link", "url"])
def test_issue_depending_on_closed_issue_of_other_repository_by_link_is_picked(body, capsys):
    # #3 of the issue's own repository is open; only the factory#3 named in the URL counts.
    gh = Gh({HERE: [(1, ready("priority:p1"), body)]},
            states={(OTHER, 3): "CLOSED", (HERE, 3): "OPEN"})
    code, rows = run_pick(gh, capsys)
    assert [r[0] for r in rows] == [f"{HERE}#1"]


# ---------------------------------------------------------------- 2. negated prose

def test_negated_prose_is_not_a_dependency():
    parse = load("factory_pick", "neg").parse_depends
    assert parse("This no longer depends on #12 being merged.", HERE) == []
    assert parse("Not depends on #12", HERE) == []
    assert parse("It never depends on #12.", HERE) == []


@pytest.mark.parametrize("body", [
    "Depends on #12",
    "Goal.\nDepends on #12\n",
    "- Depends on #12",
    "* Depends on #12",
    "**Depends on #12**",
    "**Depends on:** #12",
    "Goal text. Depends on #12.",
], ids=["start", "own-line", "dash-item", "star-item", "bold", "bold-colon", "sentence-start"])
def test_depends_at_line_start_list_item_or_bold_still_counts(body):
    assert load("factory_pick", "pos").parse_depends(body, HERE) == [(HERE, 12)]


def test_issue_with_negated_prose_is_picked_even_if_the_mentioned_issue_is_open(capsys):
    gh = Gh({HERE: [(1, ready("priority:p2"), "This no longer depends on #12 being merged.")]},
            states={(HERE, 12): "OPEN"})
    code, rows = run_pick(gh, capsys)
    assert [r[0] for r in rows] == [f"{HERE}#1"]


# ---------------------------------------------------------------- 3. priority dash

def test_issue_without_priority_label_prints_a_dash(capsys):
    gh = Gh({HERE: [(1, ready("type:feature"), ""), (2, ready("priority:p3"), "")]})
    code, rows = run_pick(gh, capsys)
    assert code == 0
    assert [(r[0], r[1]) for r in rows] == [(f"{HERE}#2", "p3"), (f"{HERE}#1", "-")]


def test_issue_without_priority_label_sorts_after_p3():
    pick = load("factory_pick", "prio").pick
    a = {"repo": HERE, "number": 1, "labels": ["status:ready"], "created": "2026-11-01T00:00:00Z"}
    b = {"repo": OTHER, "number": 2, "labels": ["status:ready", "priority:p3"],
         "created": "2026-11-20T00:00:00Z"}
    assert pick([a, b], []) == [b, a]


# ---------------------------------------------------------------- 5. public claimed() helper

def _claim(session):
    return {"session": session, "branch": "f/x", "heartbeat": "2999-01-01T00:00:00+00:00",
            "claimed": "2999-01-01T00:00:00+00:00", "step": "building"}


def test_issue_lock_exposes_public_claimed_helper_that_agrees_with_status(capsys):
    lock = load("issue_lock", "claims")
    assert callable(getattr(lock, "claimed", None)), "issue_lock.claimed(repos) must exist"
    claims = {HERE: {5: _claim("s1")}, OTHER: {7: _claim("s2")}}
    repos = [HERE, OTHER]

    rows = lock.claimed(repos, run=Gh({}, claims=claims))
    got = sorted((r["repo"], r["number"]) for r in rows)
    assert got == sorted([(HERE, 5), (OTHER, 7)])

    args = SimpleNamespace(repos=repos, default_repo=HERE, clones={}, repo=None, json=True)
    assert lock.cmd_status(args, run=Gh({}, claims=claims)) == 0
    cli = json.loads(capsys.readouterr().out)
    assert sorted((r["repo"], r["number"]) for r in cli) == got


def test_claimed_helper_returns_nothing_when_nothing_is_claimed():
    lock = load("issue_lock", "none")
    assert list(lock.claimed([HERE, OTHER], run=Gh({}))) == []


def test_pick_leaves_out_issues_claimed_in_the_fake_claim_state(capsys):
    gh = Gh({HERE: [(5, ready("priority:p1"), ""), (6, ready("priority:p2"), "")]},
            claims={HERE: {5: _claim("s1")}})
    code, rows = run_pick(gh, capsys)
    assert [r[0] for r in rows] == [f"{HERE}#6"]


# ---------------------------------------------------------------- 6. skill text

def skill():
    return SKILL.read_text(encoding="utf-8")


def section(text, number):
    m = re.search(rf"^## {number} .*?(?=^## \d|\Z)", text, re.S | re.M)
    assert m, f"SKILL.md must have section {number}"
    return m.group(0)


def test_skill_names_the_other_repository_exam_path():
    text = skill()
    assert "labs/**/tests/acceptance/" in text
    bare = re.compile(r"(?<!labs/\*\*/)tests/acceptance/(?!\*)")
    lines = [ln for ln in text.splitlines()
             if bare.search(ln) and re.search(r"\bother\b|another", ln, re.I)]
    assert lines, "name the other repository's own `tests/acceptance/` next to the labs/** path"
    exam = section(text, 5)
    assert "labs/**/tests/acceptance/" in exam and re.search(r"this repository", exam, re.I)


def test_skill_names_this_repository_for_labs_deploy_and_smoke_and_main_ci_for_the_other():
    text = skill()
    ship = section(text, 6)
    smoke = next((p for p in ship.split("\n\n") if "labs.shashkist.com" in p), "")
    assert re.search(r"this repository", smoke, re.I), "the smoke check is for this repository"
    close = section(text, 7)
    assert re.search(r"this repository", close, re.I), "Deploy evidence is for this repository"
    assert "Deploy:" in close
    assert re.search(r"main[ -]ci", close, re.I), "the other repository closes with its main CI run"


def test_skill_no_longer_says_issues_in_different_repositories_are_always_independent():
    text = skill()
    assert not re.search(r"different repositories are always independent", text, re.I)
    par = next((ln for ln in text.splitlines() if "different repositories" in ln.lower()), "")
    assert par, "keep a sentence about issues in different repositories"
    assert re.search(r"never overlap", par, re.I)
    assert "Depends on" in par, "a cross-repository `Depends on` still orders them"
