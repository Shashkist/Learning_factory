#!/usr/bin/env python3
"""Build snapshot.json for the factory dashboard (stdlib only).

Without --fixtures the data comes from the `gh` CLI (GH_TOKEN from the environment).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

CLAIM_MARKER = "<!-- pulsesign-issue-claim"
CLAIM_END = "-->"
DAYS = 14
WORKFLOWS = ("CI", "Deploy", "Smoke")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_claim(body: str) -> dict[str, Any] | None:
    """The JSON inside a claim comment, or None if the comment is not a claim."""
    if CLAIM_MARKER not in body:
        return None
    try:
        parsed = json.loads(body.split(CLAIM_MARKER, 1)[1].split(CLAIM_END, 1)[0])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def current_claim(issue: int, comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first claim comment that is not released wins (as scripts/issue_lock.py)."""
    for comment in comments:
        parsed = parse_claim(comment.get("body", ""))
        if parsed is not None and "released" not in parsed:
            return {
                "issue": issue,
                "session": parsed.get("session"),
                "step": parsed.get("step"),
                "branch": parsed.get("branch"),
                "heartbeat": parsed.get("heartbeat"),
            }
    return None


def label_values(issue: dict[str, Any], prefix: str) -> list[str]:
    names = [lb["name"] for lb in issue["labels"]]
    return [n[len(prefix):] for n in names if n.startswith(prefix)]


def count_issues(issues: list[dict[str, Any]]) -> dict[str, Any]:
    open_issues = [i for i in issues if i["state"] == "OPEN"]
    out: dict[str, Any] = {
        "open": len(open_issues),
        "closed": len(issues) - len(open_issues),
        "by_status": {},
        "by_phase": {},
        "by_priority": {},
    }
    for key, prefix in (
        ("by_status", "status:"),
        ("by_phase", "phase:"),
        ("by_priority", "priority:"),
    ):
        for issue in open_issues:
            for value in label_values(issue, prefix):
                out[key][value] = out[key].get(value, 0) + 1
    return out


def queue(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open ready issues, highest priority first, then by number."""
    ready = []
    for i in issues:
        if i["state"] == "OPEN" and "ready" in label_values(i, "status:"):
            prio = (label_values(i, "priority:") or ["p9"])[0]
            ready.append(
                {"number": i["number"], "title": i["title"], "url": i["url"], "priority": prio}
            )
    return sorted(ready, key=lambda r: (r["priority"], r["number"]))


def section(text: str, heading: str) -> str:
    """Body of a `## <heading>` section, stripped; empty if absent."""
    match = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def parse_decisions(text: str) -> list[dict[str, str]]:
    return [
        {"id": m.group(1), "title": m.group(2).strip()}
        for m in re.finditer(r"^## (D\d+) · (.+)$", text, re.M)
    ]


def parse_owner_open(text: str) -> list[dict[str, str]]:
    return [
        {"id": m.group(1), "title": m.group(2).strip().removesuffix(".")}
        for m in re.finditer(r"^- \[ \] \*\*(O\d+) · (.+?)\*\*", text, re.M)
    ]


def throughput(issues: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    today = now.astimezone(UTC).date()
    first = today - timedelta(days=DAYS - 1)
    per_day = [0] * DAYS
    hours: list[float] = []
    incidents = 0
    for issue in issues:
        if issue["state"] == "OPEN" and "incident" in label_values(issue, "type:"):
            incidents += 1
        if issue["state"] != "CLOSED" or not issue.get("closedAt"):
            continue
        if issue.get("stateReason", "COMPLETED") not in ("COMPLETED", None):
            continue  # not planned / duplicate: superseded work is not throughput
        closed = parse_time(issue["closedAt"])
        offset = (closed.astimezone(UTC).date() - first).days
        if 0 <= offset < DAYS:
            per_day[offset] += 1
            hours.append((closed - parse_time(issue["createdAt"])).total_seconds() / 3600)
    return {
        "closed_per_day": per_day,
        "median_lead_time_hours": statistics.median(hours) if hours else 0,
        "open_incidents": incidents,
    }


def pr_summary(prs: list[dict[str, Any]]) -> dict[str, Any]:
    merged = sorted(
        (p for p in prs if p["state"] == "MERGED"), key=lambda p: p["mergedAt"], reverse=True
    )
    return {
        "open": [
            {"number": p["number"], "title": p["title"], "url": p["url"]}
            for p in prs
            if p["state"] == "OPEN"
        ],
        "merged": [
            {"number": p["number"], "title": p["title"], "url": p["url"],
             "merged_at": p["mergedAt"]}
            for p in merged
        ],
    }


def latest_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        if run.get("headBranch") != "main":
            continue
        name = run["workflowName"]
        if name not in latest or run["createdAt"] > latest[name]["createdAt"]:
            latest[name] = run
    return {
        n: {"status": r["status"], "conclusion": r["conclusion"], "url": r["url"]}
        for n, r in latest.items()
    }


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def gh_json(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError("gh " + " ".join(args[:2]) + " failed: " + proc.stderr.strip())
    return json.loads(proc.stdout)


class Source:
    """Where the raw GitHub data comes from: recorded fixtures or the gh CLI."""

    def __init__(self, fixtures: Path | None) -> None:
        self.fixtures = fixtures

    def _fixture(self, name: str) -> Any:
        assert self.fixtures is not None
        path = self.fixtures / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def issues(self) -> list[dict[str, Any]]:
        if self.fixtures:
            return cast(list[dict[str, Any]], self._fixture("issues.json") or [])
        return cast(list[dict[str, Any]], gh_json([
            "issue", "list", "--state", "all", "--limit", "1000", "--json",
            "number,title,state,stateReason,labels,createdAt,closedAt,url",
        ]))

    def prs(self) -> list[dict[str, Any]]:
        if self.fixtures:
            return cast(list[dict[str, Any]], self._fixture("prs.json") or [])
        return cast(list[dict[str, Any]], gh_json([
            "pr", "list", "--state", "all", "--limit", "100", "--json",
            "number,title,state,url,mergedAt,headRefName,isDraft",
        ]))

    def runs(self) -> list[dict[str, Any]]:
        if self.fixtures:
            return cast(list[dict[str, Any]], self._fixture("runs.json") or [])
        # One query per workflow, so a busy workflow cannot push the others out of the window.
        found: list[dict[str, Any]] = []
        for name in WORKFLOWS:
            try:
                found += cast(list[dict[str, Any]], gh_json([
                    "run", "list", "--branch", "main", "--workflow", name, "--limit", "1",
                    "--json", "databaseId,workflowName,status,conclusion,headBranch,createdAt,url",
                ]))
            except RuntimeError as err:
                if "could not find any workflows" not in str(err).lower():
                    # Degrade (the light shows n/a) but say why: auth, rate limit, network.
                    print(f"warning: run lookup for {name!r} failed: {err}", file=sys.stderr)
                # else: the workflow does not exist yet, so it has no run
        return found

    def comments(self, number: int) -> list[dict[str, Any]]:
        if self.fixtures:
            data = self._fixture(f"comments-{number}.json")
        else:
            data = gh_json(["issue", "view", str(number), "--json", "comments"])
        return cast(list[dict[str, Any]], (data or {}).get("comments", []))


def build(
    source: Source, repo_root: Path, now: datetime, public: bool = False
) -> dict[str, Any]:
    issues = source.issues()
    claim = None
    for status in ("in-progress", "in-review"):
        for issue in issues:
            if issue["state"] == "OPEN" and status in label_values(issue, "status:"):
                claim = claim or current_claim(issue["number"], source.comments(issue["number"]))
        if claim:
            break
    factory = repo_root / "docs" / "factory"
    state = read_text(factory / "STATE.md")
    snap: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "issues": count_issues(issues),
        "queue": queue(issues),
        "claim": claim,
        "prs": pr_summary(source.prs()),
        "runs": latest_runs(source.runs()),
        "server": None,
        "state": {
            "now": section(state, "Now"),
            "next": section(state, "Next (in order)"),
            "health": section(state, "Health"),
        },
        "decisions": parse_decisions(read_text(factory / "DECISIONS.md")),
        "owner_open": parse_owner_open(read_text(factory / "OWNER.md")),
        "throughput": throughput(issues, now),
    }
    if public:
        del snap["state"]  # STATE.md free text is not rendered, so it is not published
    return snap


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixtures")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--now")
    ap.add_argument("--public", action="store_true", help="omit the STATE.md text")
    args = ap.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(UTC)
    source = Source(Path(args.fixtures) if args.fixtures else None)
    snap = build(source, Path(args.repo_root), now, args.public)
    Path(args.out).write_text(
        json.dumps(snap, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
