"""Pick order across the repositories in docs/factory/repos.yaml (#544, D143).

    python scripts/factory_pick.py [--repo OWNER/NAME ...] [--in-flight OWNER/NAME#N ...]

Prints the `status:ready` issues, best first, one per line:
`<repo>#<n>\t<priority>\t<title>`. Claimed issues (issue_lock) and the ones
named with --in-flight are left out. The logic is in `pick` and `parse_depends`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from types import SimpleNamespace

import issue_lock

_DEPENDS_LINE = re.compile(r"^\s*depends on\b(.*)$", re.IGNORECASE | re.MULTILINE)
_REF = re.compile(r"(?:([\w.-]+/[\w.-]+))?#(\d+)")


def parse_depends(body: str, repo: str) -> list[tuple[str, int]]:
    """References on `Depends on` lines, in order, without duplicates. A bare
    `#n` is an issue of `repo`."""
    out: list[tuple[str, int]] = []
    for line in _DEPENDS_LINE.findall(body or ""):
        for other, number in _REF.findall(line):
            ref = (other or repo, int(number))
            if ref not in out:
                out.append(ref)
    return out


def _priority(issue: dict) -> int:
    labels = set(issue.get("labels", []))
    if "type:incident" in labels and "priority:p0" in labels:
        return 0
    for rank in (1, 2, 3):
        if f"priority:p{rank}" in labels:
            return rank
    return 4


def pick(issues: list[dict], in_flight) -> list[dict]:
    """The pickable issues, best first: ready, not in flight, no open dependency;
    p0 incident, then p1 > p2 > p3, then oldest."""
    busy = set(in_flight)
    ready = [
        i for i in issues
        if "status:ready" in i.get("labels", [])
        and (i["repo"], i["number"]) not in busy
        and not any(d["state"] == "open" for d in i.get("depends") or [])
    ]
    return sorted(ready, key=lambda i: (_priority(i), i["created"]))


def _list_issues(repo: str, run, states: dict) -> list[dict]:
    rows = json.loads(run(["issue", "list", "-R", repo, "--state", "open", "--limit", "200",
                           "--json", "number,title,labels,createdAt,body"]))
    out = []
    for r in rows:
        depends = []
        for dep_repo, dep_n in parse_depends(r.get("body") or "", repo):
            if (dep_repo, dep_n) not in states:
                view = json.loads(run(["issue", "view", "-R", dep_repo, str(dep_n),
                                       "--json", "state"]))
                states[(dep_repo, dep_n)] = view["state"].lower()
            depends.append({"repo": dep_repo, "number": dep_n, "state": states[(dep_repo, dep_n)]})
        out.append({"repo": repo, "number": r["number"], "title": r.get("title", ""),
                    "labels": [lab["name"] for lab in r.get("labels", [])],
                    "created": r["createdAt"], "depends": depends})
    return out


def _parse_in_flight(values: list[str]) -> set[tuple[str, int]]:
    out = set()
    for v in values:
        repo, _, number = v.rpartition("#")
        out.add((repo or issue_lock.HERE_REPO, int(number)))
    return out


def main(argv: list[str] | None = None, run=issue_lock._run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", action="append", help="owner/name; repeatable (default: every repo in repos.yaml)")
    ap.add_argument("--in-flight", action="append", default=[], metavar="REPO#N",
                    help="also treat this issue as in flight (repeatable)")
    args = ap.parse_args(argv)
    repos, default, clones = issue_lock.read_repos()
    chosen = args.repo or repos
    busy = _parse_in_flight(args.in_flight)
    lock_args = SimpleNamespace(repos=chosen, default_repo=default, clones=clones, repo=None)
    failed: list = []
    busy |= {(r["repo"], r["number"]) for r in issue_lock._claimed_issues(run, lock_args, failed)}
    issues, states = [], {}
    for repo in chosen:
        try:
            issues += _list_issues(repo, run, states)
        except (RuntimeError, ValueError, KeyError) as e:
            print(f"cannot read {repo}: {e}", file=sys.stderr)
            failed.append(repo)
    for i in pick(issues, busy):
        prio = "p0" if _priority(i) == 0 else f"p{_priority(i)}"
        print(f"{i['repo']}#{i['number']}\t{prio}\t{i['title']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
