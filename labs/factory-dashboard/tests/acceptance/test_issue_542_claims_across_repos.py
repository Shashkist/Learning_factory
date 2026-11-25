"""Exam for issue #542: issue_lock claims across repositories (slice 1 of #536, D143).

Behaviour only. `gh` is faked at the subprocess boundary (`subprocess.run` inside scripts/issue_lock.py),
with one in-memory tracker per repository, so there is no network and no real GitHub. A `gh` call that
carries `-R owner/name` talks to that repository; a call without `-R` talks to this repository
(Shashkist/Learning_factory), exactly as the real CLI does from the checkout.

HOW THE EXAM POINTS AT A repos.yaml: the issue names no mechanism, so this exam ASSUMES the environment
variable `LF_REPOS_FILE` (path of the repos file; default `docs/factory/repos.yaml` of the repository the
script lives in). It also unsets LF_CREW_DIR so nothing depends on the machine.

Criteria covered:
  1. claim 5 --repo Shashkist/factory and a claim on this repository's #5 coexist; -R only on the first.
  2. status and stale list both claims with their repository (text, and a `repo` key in --json); a
     repository that cannot be read prints one line and the others still show.
  3. calls without --repo produce exactly today's gh argument lists.
  4. a missing or unreadable repos.yaml falls back to this repository only, with one warning line.
"""
# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "issue_lock.py"

HERE_REPO = "Shashkist/Learning_factory"
OTHER_REPO = "Shashkist/factory"

REPOS_YAML = f"""\
- repo: {HERE_REPO}
  default: true
  clone: C:\\projects\\Learning_factory
  ship: deploy-smoke
- repo: {OTHER_REPO}
  default: false
  clone: C:\\projects\\factory
  ship: main-ci
"""


def load_script():
    spec = importlib.util.spec_from_file_location("issue_lock_exam542", SCRIPT)
    assert spec and spec.loader, "scripts/issue_lock.py must exist"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Tracker:
    """In-memory GitHub: per repository, issues with labels and comments."""

    def __init__(self, repos, broken=()):
        self.repos = {r: {5: {"title": f"Five in {r}", "labels": {"status:ready"}, "comments": []}} for r in repos}
        self.broken = set(broken)
        self.calls: list[tuple[str, list[str]]] = []  # (repo the call reached, argv without "gh")
        self._cid = 1000

    @staticmethod
    def split(argv):
        """(repo from -R or None, argv without the -R pair)."""
        repo, out, i = None, [], 0
        while i < len(argv):
            if argv[i] in ("-R", "--repo") and i + 1 < len(argv):
                repo = argv[i + 1]
                i += 2
                continue
            out.append(argv[i])
            i += 1
        return repo, out

    def run(self, cmd, **_kw):
        argv = list(cmd[1:])
        assert cmd[0] == "gh", cmd
        repo, plain = self.split(argv)
        if repo is None:
            for a in plain:  # `gh api repos/owner/name/...` may name the repository in the path
                if a.startswith("repos/") and not a.startswith("repos/{owner}"):
                    repo = "/".join(a.split("/")[1:3])
        reached = repo or HERE_REPO
        self.calls.append((reached, argv))
        if reached in self.broken:
            return subprocess.CompletedProcess(cmd, 1, "", "HTTP 404: Not Found")
        issues = self.repos[reached]
        out = ""
        if plain[:2] == ["issue", "view"]:
            out = json.dumps({"comments": issues[int(plain[2])]["comments"]})
        elif plain[:2] == ["issue", "list"]:
            now = datetime.now(UTC).replace(microsecond=0).isoformat()
            rows = [
                {"number": n, "title": i["title"], "updatedAt": now}
                for n, i in issues.items()
                if "--label" not in plain or "status:in-progress" in i["labels"]
            ]
            out = json.dumps(rows)
        elif plain[:2] == ["issue", "comment"]:
            self._cid += 1
            body = plain[plain.index("--body") + 1]
            url = f"https://github.com/{reached}/issues/{plain[2]}#issuecomment-{self._cid}"
            issues[int(plain[2])]["comments"].append({"body": body, "url": url})
        elif plain[:1] == ["api"]:
            cid = next(a for a in plain if "/comments/" in a).rsplit("/", 1)[1]
            body = next(a for a in plain if a.startswith("body="))[5:]
            for i in issues.values():
                for c in i["comments"]:
                    if c["url"].endswith("issuecomment-" + cid):
                        c["body"] = body
        elif plain[:2] == ["issue", "edit"]:
            labels = issues[int(plain[2])]["labels"]
            for flag, val in zip(plain, plain[1:], strict=False):
                if flag == "--add-label":
                    labels.add(val)
                if flag == "--remove-label":
                    labels.discard(val)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def reached(self, repo):
        return [a for r, a in self.calls if r == repo]


class World:
    pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("LF_CREW_DIR", raising=False)
    repos_file = tmp_path / "repos.yaml"
    repos_file.write_text(REPOS_YAML, encoding="utf-8")
    monkeypatch.setenv("LF_REPOS_FILE", str(repos_file))
    tracker = Tracker([HERE_REPO, OTHER_REPO])
    monkeypatch.setattr(subprocess, "run", tracker.run)
    w = World()
    w.mod, w.tracker, w.repos_file = load_script(), tracker, repos_file
    return w


def two_claims(w):
    assert w.mod.main(["claim", "5", "--session", "sess-fac", "--branch", "f/5-fac", "--repo", OTHER_REPO]) == 0
    assert w.mod.main(["claim", "5", "--session", "sess-lf", "--branch", "f/5-lf"]) == 0


def has_r(argv):
    return "-R" in argv or "--repo" in argv


# ---- 1. coexisting claims ------------------------------------------------------------------------

def test_claim_on_same_number_in_two_repositories_coexist(world):
    two_claims(world)
    t = world.tracker
    assert len(t.repos[OTHER_REPO][5]["comments"]) == 1
    assert len(t.repos[HERE_REPO][5]["comments"]) == 1
    assert "sess-fac" in t.repos[OTHER_REPO][5]["comments"][0]["body"]
    assert "sess-lf" in t.repos[HERE_REPO][5]["comments"][0]["body"]
    assert "status:in-progress" in t.repos[OTHER_REPO][5]["labels"]
    assert "status:in-progress" in t.repos[HERE_REPO][5]["labels"]


def test_every_gh_call_of_the_other_repository_claim_has_dash_R(world):
    assert world.mod.main(["claim", "5", "--session", "sess-fac", "--branch", "f/5-fac", "--repo", OTHER_REPO]) == 0
    assert world.tracker.calls, "claim made no gh calls"
    for repo, argv in world.tracker.calls:
        assert repo == OTHER_REPO, argv
        assert has_r(argv), argv


def test_claim_without_repo_has_no_dash_R_on_any_call(world):
    assert world.mod.main(["claim", "5", "--session", "sess-lf", "--branch", "f/5-lf"]) == 0
    assert world.tracker.calls
    assert not any(has_r(a) for _, a in world.tracker.calls), world.tracker.calls


def test_same_repository_same_number_is_still_refused(world, capsys):
    two_claims(world)
    rc = world.mod.main(["claim", "5", "--session", "sess-x", "--branch", "f/x", "--repo", OTHER_REPO])
    assert rc == 1
    assert "sess-fac" in capsys.readouterr().err


def test_heartbeat_and_release_go_to_the_named_repository(world):
    two_claims(world)
    world.tracker.calls.clear()
    assert world.mod.main(["heartbeat", "5", "--session", "sess-fac", "--step", "x", "--repo", OTHER_REPO]) == 0
    assert world.mod.main(["release", "5", "--session", "sess-fac", "--repo", OTHER_REPO]) == 0
    assert world.tracker.calls
    assert all(r == OTHER_REPO for r, _ in world.tracker.calls)
    assert "released" in world.tracker.repos[OTHER_REPO][5]["comments"][0]["body"]
    assert "released" not in world.tracker.repos[HERE_REPO][5]["comments"][0]["body"]


# ---- 2. status and stale show the repository -----------------------------------------------------

def test_status_lists_both_claims_with_their_repository(world, capsys):
    two_claims(world)
    capsys.readouterr()
    assert world.mod.main(["status"]) == 0
    lines = capsys.readouterr().out.splitlines()
    fac = [ln for ln in lines if "sess-fac" in ln]
    lf = [ln for ln in lines if "sess-lf" in ln]
    assert len(fac) == 1 and OTHER_REPO in fac[0], lines
    assert len(lf) == 1 and HERE_REPO in lf[0], lines
    assert OTHER_REPO not in lf[0]


def test_status_json_has_a_repo_key_per_row(world, capsys):
    two_claims(world)
    capsys.readouterr()
    assert world.mod.main(["status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    by_session = {r["claim"]["session"]: r for r in rows}
    assert set(by_session) == {"sess-fac", "sess-lf"}
    assert by_session["sess-fac"]["repo"] == OTHER_REPO
    assert by_session["sess-lf"]["repo"] == HERE_REPO
    assert all(r["number"] == 5 for r in rows)


def test_stale_lists_both_claims_with_their_repository(world, capsys):
    two_claims(world)
    capsys.readouterr()
    assert world.mod.main(["stale", "--older-than", "0"]) == 0
    out = capsys.readouterr().out
    blocks = [b for b in out.split("\n#") if b.strip()]
    fac = [b for b in blocks if "sess-fac" in b]
    lf = [b for b in blocks if "sess-lf" in b]
    assert len(fac) == 1 and OTHER_REPO in fac[0], out
    assert len(lf) == 1 and HERE_REPO in lf[0], out
    assert OTHER_REPO not in lf[0], out


def test_status_reads_every_repository_in_the_file(world):
    assert world.mod.main(["status"]) == 0
    assert world.tracker.reached(HERE_REPO), "this repository was not read"
    assert world.tracker.reached(OTHER_REPO), "the second repository was not read"
    assert all(has_r(a) for a in world.tracker.reached(OTHER_REPO))
    assert not any(has_r(a) for a in world.tracker.reached(HERE_REPO))


def test_unreadable_repository_prints_one_line_and_the_others_still_show(world, capsys):
    assert world.mod.main(["claim", "5", "--session", "sess-lf", "--branch", "f/5-lf"]) == 0
    world.tracker.broken.add(OTHER_REPO)
    capsys.readouterr()
    assert world.mod.main(["status"]) == 0
    cap = capsys.readouterr()
    both = (cap.out + cap.err).splitlines()
    assert any("sess-lf" in ln and HERE_REPO in ln for ln in both), both
    assert len([ln for ln in both if OTHER_REPO in ln]) == 1, both


def test_unreadable_repository_in_stale_prints_one_line_and_the_others_still_show(world, capsys):
    assert world.mod.main(["claim", "5", "--session", "sess-lf", "--branch", "f/5-lf"]) == 0
    world.tracker.broken.add(OTHER_REPO)
    capsys.readouterr()
    assert world.mod.main(["stale", "--older-than", "0"]) == 0
    cap = capsys.readouterr()
    both = (cap.out + cap.err).splitlines()
    assert "sess-lf" in cap.out
    assert len([ln for ln in both if OTHER_REPO in ln]) == 1, both


# ---- 3. no --repo: exactly today's gh calls ------------------------------------------------------

def strip_body(calls):
    """Claim comment bodies carry timestamps; keep the position, drop the text."""
    out = []
    for a in calls:
        a = list(a)
        if "--body" in a:
            a[a.index("--body") + 1] = "<body>"
        out.append(a)
    return out


TODAY_CLAIM = [
    ["issue", "view", "5", "--json", "comments"],
    ["issue", "comment", "5", "--body", "<body>"],
    ["issue", "view", "5", "--json", "comments"],
    ["issue", "edit", "5", "--add-label", "status:in-progress", "--remove-label", "status:ready"],
]
TODAY_STATUS = [
    ["issue", "list", "--label", "status:in-progress", "--state", "open", "--json", "number,title", "-L", "100"],
    ["issue", "list", "--label", "status:in-review", "--state", "open", "--json", "number,title", "-L", "100"],
    ["issue", "list", "--label", "status:needs-owner", "--state", "open", "--json", "number,title", "-L", "100"],
    ["issue", "list", "--state", "open", "--json", "number,title,updatedAt", "-L", "100"],
    ["issue", "view", "5", "--json", "comments"],
]


def test_claim_without_repo_makes_exactly_todays_gh_calls(world):
    assert world.mod.main(["claim", "5", "--session", "sess-lf", "--branch", "f/5-lf"]) == 0
    assert strip_body([a for _, a in world.tracker.calls]) == TODAY_CLAIM


@pytest.mark.parametrize("variant", ["two-repos", "missing-file"])
def test_claim_without_repo_is_the_same_whatever_repos_yaml_says(world, variant):
    if variant == "missing-file":
        world.repos_file.unlink()
    assert world.mod.main(["claim", "5", "--session", "sess-lf", "--branch", "f/5-lf"]) == 0
    assert strip_body([a for _, a in world.tracker.calls]) == TODAY_CLAIM


def test_status_on_this_repository_alone_makes_todays_gh_calls(world):
    world.repos_file.write_text(
        f"- repo: {HERE_REPO}\n  default: true\n  clone: C:\\projects\\Learning_factory\n  ship: deploy-smoke\n",
        encoding="utf-8",
    )
    assert world.mod.main(["status"]) == 0
    assert strip_body([a for _, a in world.tracker.calls]) == TODAY_STATUS


# ---- 4. missing or unreadable repos.yaml ---------------------------------------------------------

@pytest.mark.parametrize("how", ["missing", "garbage", "empty"])
def test_missing_or_unreadable_repos_yaml_falls_back_to_this_repository_with_one_warning(world, capsys, how):
    if how == "missing":
        world.repos_file.unlink()
    elif how == "garbage":
        world.repos_file.write_text("::: [unclosed\n\t- {", encoding="utf-8")
    else:
        world.repos_file.write_text("", encoding="utf-8")
    assert world.mod.main(["claim", "5", "--session", "sess-lf", "--branch", "f/5-lf"]) == 0
    cap = capsys.readouterr()
    lines = [ln for ln in (cap.out + cap.err).splitlines() if ln.strip()]
    assert any("claimed by sess-lf" in ln for ln in lines), lines
    assert len([ln for ln in lines if "repos.yaml" in ln]) == 1, lines
    assert len(lines) == 2, lines
    assert not any(has_r(a) for _, a in world.tracker.calls)
    assert not world.tracker.reached(OTHER_REPO)


@pytest.mark.parametrize("how", ["missing", "garbage"])
def test_status_with_no_usable_repos_yaml_reads_this_repository_only_and_warns_once(world, capsys, how):
    assert world.mod.main(["claim", "5", "--session", "sess-lf", "--branch", "f/5-lf"]) == 0
    if how == "missing":
        world.repos_file.unlink()
    else:
        world.repos_file.write_text("::: [unclosed\n\t- {", encoding="utf-8")
    world.tracker.calls.clear()
    capsys.readouterr()
    assert world.mod.main(["status"]) == 0
    cap = capsys.readouterr()
    lines = (cap.out + cap.err).splitlines()
    assert len([ln for ln in lines if "repos.yaml" in ln]) == 1, lines
    assert any("sess-lf" in ln for ln in lines), lines
    assert not any(has_r(a) for _, a in world.tracker.calls)
    assert not world.tracker.reached(OTHER_REPO)


# ---- the repos file the issue asks for -----------------------------------------------------------

def test_repository_repos_yaml_lists_this_repository_as_default_and_the_factory_repository():
    import yaml

    path = ROOT / "docs" / "factory" / "repos.yaml"
    assert path.is_file(), "docs/factory/repos.yaml is missing"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = data["repos"] if isinstance(data, dict) else data
    by_repo = {e["repo"]: e for e in entries}
    assert [e["repo"] for e in entries if e.get("default") is True] == [HERE_REPO]
    assert by_repo[HERE_REPO]["ship"] == "deploy-smoke"
    assert by_repo[OTHER_REPO]["ship"] == "main-ci"
    assert by_repo[OTHER_REPO]["clone"] == "C:\\projects\\factory"
    assert all(e.get("clone") for e in entries)
