#!/usr/bin/env python3
"""Deterministic merge gate (#84, D148): MERGE or HOLD from facts only.

Usage: merge_gate.py <pr> --session <s> [--repo owner/name] [--author login]
Prints `MERGE`, or one `HOLD: <reason>` line per reason. Exit 0 / 1. Read-only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ci"))

import issue_lock
from exam_integrity import _is_exam

MAX_REVIEW_ROUNDS = 2
MAX_CI_ROUNDS = 5
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
VERDICT = re.compile(r"^Review round (\d+): (APPROVE|CHANGES) at ([0-9a-fA-F]{7,40})\s*$")
CLOSES = re.compile(r"^\s*closes\s+#(\d+)\s*$", re.IGNORECASE | re.MULTILINE)
# Exam files for counting CI rounds only (D148 amendment 1b, 2): tooling exams and another
# repository's tests/acceptance/ besides D9's labs/*/tests/.
TOOLING_TEST = re.compile(r"^(scripts/(ci/)?test_[^/]*\.py|tests/acceptance/.+)$")


def _check_reasons(checks: list[dict]) -> list[str]:
    if not checks:
        return ["no checks have run on this PR"]
    reasons = []
    for c in checks:
        name = c.get("name") or c.get("context") or "?"
        state = (c.get("conclusion") or c.get("state") or "").upper()
        if c.get("status", "COMPLETED") != "COMPLETED" or state in ("", "PENDING", "EXPECTED"):
            reasons.append(f"{name} is pending")
        elif state not in PASSED or (name == "exam-integrity" and state != "SUCCESS"):
            reasons.append(f"{name} ended in {state.lower()}")
    if not any((c.get("name") or c.get("context")) == "exam-integrity" for c in checks):
        reasons.append("exam-integrity has not run")
    return reasons


def _verdicts(comments: list[dict], author: str) -> list[tuple[int, str, str]]:
    found = []
    for c in sorted(comments, key=lambda c: c.get("createdAt", "")):
        if (c.get("author") or {}).get("login") != author:
            continue
        m = VERDICT.match((c.get("body") or "").split("\n", 1)[0])
        if m:
            found.append((int(m.group(1)), m.group(2), m.group(3).lower()))
    return found


def main(argv: list[str], run=issue_lock._run) -> int:
    ap = argparse.ArgumentParser(prog="merge_gate")
    ap.add_argument("pr")
    ap.add_argument("--session", required=True)
    ap.add_argument("--repo")
    ap.add_argument("--author")
    args = ap.parse_args(argv)
    gh = issue_lock.scoped(run, args.repo)

    fields = ("author,body,headRefOid,isCrossRepository,commits,statusCheckRollup,comments,"
              "url,closingIssuesReferences")
    pr = json.loads(gh(["pr", "view", args.pr, "--json", fields]))
    head = pr["headRefOid"].lower()
    author = args.author or pr["author"]["login"]
    reasons = _check_reasons(pr.get("statusCheckRollup") or [])
    if pr.get("isCrossRepository") is True:
        reasons.append("cross-repository pull request")

    verdicts = _verdicts(pr.get("comments") or [], author)
    for_head = [v for v in verdicts if head.startswith(v[2])]
    if not for_head or for_head[-1][1] != "APPROVE":
        reasons.append(f"no APPROVE verdict for head {head[:7]}")
    rounds = max((v[0] for v in verdicts), default=0)
    if rounds > MAX_REVIEW_ROUNDS:
        reasons.append(f"review round {rounds} is over the limit of {MAX_REVIEW_ROUNDS}")

    code_commits = 0
    for commit in pr.get("commits") or []:
        files = json.loads(gh(["api", f"repos/{{owner}}/{{repo}}/commits/{commit['oid']}"]))
        names = [f["filename"] for f in files.get("files", [])]
        if not (names and all(_is_exam(n) or TOOLING_TEST.match(n) for n in names)):
            code_commits += 1
    if code_commits > MAX_CI_ROUNDS:
        reasons.append(f"ci round {code_commits} is over the limit of {MAX_CI_ROUNDS}")

    closed_set = {int(n) for n in CLOSES.findall(pr.get("body") or "")}
    # GitHub closes on any closing keyword anywhere in the body: every issue it will close
    # needs this session's claim, and one in another repository always holds.
    pr_repo = (pr.get("url") or "").split("/pull/")[0]
    for ref in pr.get("closingIssuesReferences") or []:
        ref_repo = (ref.get("url") or "").split("/issues/")[0]
        if pr_repo and ref_repo and ref_repo.lower() != pr_repo.lower():
            reasons.append(f"closes #{ref.get('number')} in another repository")
        else:
            closed_set.add(int(ref["number"]))
    closed = sorted(closed_set)
    if not closed:
        reasons.append("PR body has no `Closes #n` for the issue it ships")
    for n in closed:
        claim, _ = issue_lock.find_claim(n, gh)
        if not claim or claim.get("session") != args.session:
            reasons.append(f"claim for #{n} is not held by session {args.session}")

    for r in reasons:
        print("HOLD: " + r)
    if not reasons:
        print("MERGE")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
