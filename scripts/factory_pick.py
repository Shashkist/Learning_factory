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
from pathlib import Path

# Loaded by path from other folders too (the dashboard exam): find the sibling module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import issue_lock

# "Depends on" counts at the start of a line, list item or bold text, or of a sentence;
# prose such as "no longer depends on #12" does not.
_DEPENDS = re.compile(
    r"(?:^[ \t]*(?:[-*+][ \t]+)?\**[ \t]*|[.!?;][ \t]+)depends on\b[\s*:]*([^\n]*)",
    re.IGNORECASE | re.MULTILINE,
)
_SENTENCE_END = re.compile(r"[.;](?:\s|$)|;")
_URL = r"https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)"
# A markdown link or a plain issue URL (repository from the URL), else `[owner/name]#n`.
_REF = re.compile(rf"\[[^\]]*\]\({_URL}\)|{_URL}|(?:([\w.-]+/[\w.-]+))?#(\d+)")


def parse_depends(body: str, repo: str) -> list[tuple[str, int]]:
    """References after `Depends on` (anywhere in a line, up to the end of that
    sentence; `none` means no dependency), in order, without duplicates. A bare
    `#n` is an issue of `repo`; a link or URL names its own repository."""
    out: list[tuple[str, int]] = []
    for line in _DEPENDS.findall(body or ""):
        line = _SENTENCE_END.split(line, maxsplit=1)[0]
        if line.strip().lower().startswith("none"):
            continue
        for l_repo, l_n, u_repo, u_n, other, number in _REF.findall(line):
            ref = (l_repo or u_repo or other or repo, int(l_n or u_n or number))
            if ref not in out:
                out.append(ref)
    return out


def _priority(issue: dict) -> int:
    labels = set(issue.get("labels", []))
    if "priority:p0" in labels:
        return 0 if "type:incident" in labels else 1
    for rank in (1, 2, 3):
        if f"priority:p{rank}" in labels:
            return rank + 1
    return 5  # no priority label: after p3


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


def _list_issues(repo: str, run, states: dict, known: list[str] | None = None) -> list[dict]:
    rows = json.loads(run(["issue", "list", "-R", repo, "--state", "open", "--limit", "200",
                           "--json", "number,title,labels,createdAt,body"]))
    out = []
    for r in rows:
        depends = []
        for dep_repo, dep_n in parse_depends(r.get("body") or "", repo):
            dep_repo = next((k for k in known or [] if k.lower() == dep_repo.lower()), dep_repo)
            if (dep_repo, dep_n) not in states:
                try:
                    view = json.loads(run(["issue", "view", str(dep_n), "-R", dep_repo,
                                           "--json", "state"]))
                    states[(dep_repo, dep_n)] = view["state"].lower()
                except (RuntimeError, ValueError, KeyError):
                    print(f"warning: cannot read {dep_repo}#{dep_n}; treated as open",
                          file=sys.stderr)
                    states[(dep_repo, dep_n)] = "open"
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
    repos, _default, _clones = issue_lock.read_repos()
    chosen = args.repo or repos
    busy = _parse_in_flight(args.in_flight)
    failed: list = []
    busy_failed: list = []
    busy |= {(r["repo"], r["number"]) for r in issue_lock.claimed(chosen, run, busy_failed)}
    failed += busy_failed
    issues, states = [], {}
    for repo in chosen:
        try:
            issues += _list_issues(repo, run, states, repos)
        except (RuntimeError, ValueError, KeyError) as e:
            print(f"cannot read {repo}: {e}", file=sys.stderr)
            failed.append(repo)
    # A repository whose claims could not be read is not treated as "none claimed".
    issues = [i for i in issues if i["repo"] not in busy_failed]
    for i in pick(issues, busy):
        rank = _priority(i)
        prio = "-" if rank == 5 else "p0" if rank <= 1 else f"p{rank - 1}"
        print(f"{i['repo']}#{i['number']}\t{prio}\t{i['title']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
