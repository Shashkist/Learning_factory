"""Exam for issue #18: factory dashboard (snapshot.json and the static page).

INTERFACE (the builder works from this)

CLI, stdlib only, Python 3.12:

    python build_snapshot.py --out <path> [--fixtures <dir>] [--repo-root <dir>] [--now <ISO8601>]

* --out        file to write (UTF-8 JSON). Its parent directory exists.
* --fixtures   directory of recorded `gh` JSON. When given, `gh` is NOT called;
               every input is read from these files:
    issues.json         `gh issue list --state all --limit 1000
                         --json number,title,state,labels,createdAt,closedAt,url`
                        -> list of {number, title, state ("OPEN"|"CLOSED"),
                        labels: [{name}], createdAt, closedAt (null if open), url}
    prs.json            `gh pr list --state all --limit 100
                         --json number,title,state,url,mergedAt,headRefName,isDraft`
                        -> list; state is OPEN | MERGED | CLOSED
    runs.json           `gh run list --limit 100
                         --json databaseId,workflowName,status,conclusion,headBranch,createdAt,url`
                        -> list; conclusion is "" while a run is in progress
    comments-<n>.json   `gh issue view <n> --json comments` -> {"comments": [...]}
                        read for open issues labelled status:in-progress first, then (only if
                        none yields a claim) status:in-review
                        (comment bodies use the claim marker of scripts/issue_lock.py)
* --repo-root  directory holding docs/factory/{STATE,DECISIONS,OWNER}.md (parsed).
* --now        fixed clock, e.g. 2026-11-14T12:00:00Z (default: the real now).

OUTPUT JSON (top level keys)

    generated_at   the --now instant as an ISO 8601 string (parses to the same instant)
    issues         {open, closed, by_status, by_phase, by_priority}
                   counts over OPEN issues only; by_status keys are the part after
                   "status:" ("ready", "in-progress", ...), by_phase keys the part after
                   "phase:" ("1", "2"), by_priority the part after "priority:" ("p1"...)
                   open/closed: number of open / closed issues
    claim          null, or {issue, session, step, branch, heartbeat}: the live claim
                   (marker `<!-- pulsesign-issue-claim`). Same rule as scripts/issue_lock.py
                   find_claim: among an issue's claim comments (file order) the FIRST whose
                   JSON has no "released" key wins (a released one is a claim comment
                   overwritten with {"released","by","reason"}). Looked up on open
                   status:in-progress issues first, then status:in-review; none -> null.
    prs           {open: [{number, title, url}],
                    merged: [{number, title, url, merged_at}]}  merged newest first,
                   closed-unmerged PRs appear nowhere
    runs           {<workflowName>: {status, conclusion, url}} the most recent run
                   (by createdAt) of each workflow among runs with headBranch == "main"
                   only, keyed by workflow name ("CI", "Deploy", "Smoke"); a workflow
                   with no main run is absent or null
    server         null when no server data exists (always null for now; key present)
    state          {now, next, health}: the body text (stripped) of the `## Now`,
                   `## Next (in order)` and `## Health` sections of STATE.md
    decisions      [{id: "D11", title: "Owner: the manager may push"}, ...] in file order,
                   from `## D<n> (middle dot) <title>` lines
    owner_open     [{id: "O4", title: "(optional) Second-opinion reviewer"}, ...] from the
                   unchecked `- [ ] **O<n> (middle dot) <title>.**` items (trailing "." of
                   the bold title dropped); checked items are omitted
    throughput     {closed_per_day: [14 ints, oldest first, the last one is the UTC date of
                    --now], median_lead_time_hours: number (median of closedAt - createdAt
                    over issues closed inside that 14-day window), open_incidents: int
                    (open issues labelled type:incident)}
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

COMPONENT = Path(__file__).resolve().parents[2]
REPO = COMPONENT.parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "issue18"
SCRIPT = COMPONENT / "build_snapshot.py"
NOW = "2026-11-14T12:00:00Z"


def run_snapshot(tmp_path: Path, fixtures: Path = FIXTURES, now: str = NOW) -> dict:
    out = tmp_path / "snapshot.json"
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--out", str(out),
            "--fixtures", str(fixtures),
            "--repo-root", str(FIXTURES / "repo"),
            "--now", now,
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text(encoding="utf-8"))


# ---- snapshot.json ---------------------------------------------------------

def test_snapshot_has_generated_at_equal_to_now(tmp_path):
    snap = run_snapshot(tmp_path)
    got = datetime.fromisoformat(snap["generated_at"].replace("Z", "+00:00"))
    assert got == datetime.fromisoformat("2026-11-14T12:00:00+00:00")


def test_issues_counted_by_status(tmp_path):
    issues = run_snapshot(tmp_path)["issues"]
    assert issues["open"] == 8
    assert issues["closed"] == 8
    assert issues["by_status"] == {
        "ready": 4, "in-progress": 1, "in-review": 1, "blocked": 1, "needs-owner": 1,
    }


def test_issues_counted_by_phase(tmp_path):
    assert run_snapshot(tmp_path)["issues"]["by_phase"] == {"1": 5, "2": 3}


def test_issues_counted_by_priority(tmp_path):
    assert run_snapshot(tmp_path)["issues"]["by_priority"] == {"p1": 4, "p2": 3, "p3": 1}


def _fx(tmp_path: Path) -> Path:
    fx = tmp_path / "fx"
    shutil.copytree(FIXTURES, fx)
    return fx


def _comment(i: int, n: int, body: str) -> dict:
    return {
        "id": f"IC_{i}", "author": {"login": "someone"}, "body": body,
        "createdAt": "2026-11-14T08:00:00Z",
        "url": f"https://github.com/example/factory/issues/{n}#issuecomment-{i}",
    }


def _live(session: str, step: str, heartbeat: str) -> str:
    data = {"branch": "f/18-factory-dashboard", "claimed": "2026-11-14T07:00:00+00:00",
            "heartbeat": heartbeat, "session": session, "step": step, "worktree": ""}
    return ("<!-- pulsesign-issue-claim\n" + json.dumps(data, indent=2, sort_keys=True)
            + f"\n-->\n**Claimed by `{session}`**\n")


def _released(by: str, reason: str) -> str:
    data = {"released": "2026-11-14T09:00:00+00:00", "by": by, "reason": reason}
    return ("<!-- pulsesign-issue-claim\n" + json.dumps(data)
            + f"\n-->\nReleased by `{by}` - {reason}")


def _write_comments(fx: Path, n: int, comments: list[dict]) -> None:
    (fx / f"comments-{n}.json").write_text(
        json.dumps({"comments": comments}), encoding="utf-8")


def test_race_loser_released_comment_before_live_winner_reports_the_winner(tmp_path):
    fx = _fx(tmp_path)
    _write_comments(fx, 18, [
        _comment(1, 18, _released("lf-loser", "lost the claim race")),
        _comment(2, 18, _live("lf-winner", "reading the issue", "2026-11-14T11:00:00+00:00")),
    ])
    claim = run_snapshot(tmp_path, fixtures=fx)["claim"]
    assert claim["session"] == "lf-winner"
    assert claim["step"] == "reading the issue"


def test_first_live_claim_wins_over_a_later_live_claim(tmp_path):
    fx = _fx(tmp_path)
    _write_comments(fx, 18, [
        _comment(1, 18, _live("lf-first", "building", "2026-11-14T10:00:00+00:00")),
        _comment(2, 18, _live("lf-second", "building", "2026-11-14T11:59:00+00:00")),
    ])
    assert run_snapshot(tmp_path, fixtures=fx)["claim"]["session"] == "lf-first"


def test_in_progress_claim_wins_over_in_review_claim(tmp_path):
    fx = _fx(tmp_path)
    shutil.copy(fx / "comments-16-live.json", fx / "comments-16.json")
    claim = run_snapshot(tmp_path, fixtures=fx)["claim"]
    assert claim["issue"] == 18
    assert claim["session"] == "lf-k7m2"


def test_claim_falls_back_to_in_review_issue_when_none_is_in_progress(tmp_path):
    fx = _fx(tmp_path)
    issues = json.loads((fx / "issues.json").read_text(encoding="utf-8"))
    for issue in issues:
        if issue["number"] == 18:  # no issue is status:in-progress any more
            issue["labels"] = [
                {"name": "status:ready"} if lb["name"] == "status:in-progress" else lb
                for lb in issue["labels"]
            ]
    (fx / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    shutil.copy(fx / "comments-16-live.json", fx / "comments-16.json")
    claim = run_snapshot(tmp_path, fixtures=fx)["claim"]
    assert claim["issue"] == 16
    assert claim["session"] == "lf-r9x1"
    assert claim["step"] == "waiting for review"
    assert claim["branch"] == "f/16-in-review-one"
    assert claim["heartbeat"] == "2026-11-14T11:45:00+00:00"


def test_server_key_is_present_and_null_without_server_data(tmp_path):
    snap = run_snapshot(tmp_path)
    assert "server" in snap
    assert snap["server"] is None


def test_current_claim_is_the_first_live_claim_comment(tmp_path):
    # fixture: #18 has a released comment, a plain comment, then one live claim
    claim = run_snapshot(tmp_path)["claim"]
    assert claim["issue"] == 18
    assert claim["session"] == "lf-k7m2"
    assert claim["step"] == "writing the exam"
    assert claim["branch"] == "f/18-factory-dashboard"
    assert claim["heartbeat"] == "2026-11-14T11:30:00+00:00"


def test_released_claim_means_no_current_claim(tmp_path):
    fx = _fx(tmp_path)
    shutil.copy(fx / "comments-18-released.json", fx / "comments-18.json")
    assert run_snapshot(tmp_path, fixtures=fx)["claim"] is None


def test_open_prs_listed(tmp_path):
    opened = run_snapshot(tmp_path)["prs"]["open"]
    assert [p["number"] for p in opened] == [24]
    assert opened[0]["title"] == "Open one"
    assert opened[0]["url"] == "https://github.com/example/factory/pull/24"


def test_merged_prs_listed_newest_first_and_closed_unmerged_omitted(tmp_path):
    prs = run_snapshot(tmp_path)["prs"]
    assert [p["number"] for p in prs["merged"]] == [23, 22, 21]
    assert prs["merged"][0]["merged_at"] == "2026-11-14T09:00:00Z"
    assert prs["merged"][0]["title"] == "Merged newest"
    assert 25 not in [p["number"] for p in prs["open"] + prs["merged"]]


def test_latest_ci_run_by_workflow_name(tmp_path):
    ci = run_snapshot(tmp_path)["runs"]["CI"]
    assert (ci["status"], ci["conclusion"]) == ("completed", "success")
    assert ci["url"].endswith("/runs/102")


def test_runs_use_only_main_branch_runs(tmp_path):
    # fixture: run 107 is a newer failing CI run on a feature branch; it must be ignored
    ci = run_snapshot(tmp_path)["runs"]["CI"]
    assert (ci["status"], ci["conclusion"]) == ("completed", "success")
    assert ci["url"].endswith("/runs/102")


def test_latest_deploy_run_by_workflow_name(tmp_path):
    deploy = run_snapshot(tmp_path)["runs"]["Deploy"]
    assert deploy["status"] == "in_progress"
    assert deploy["url"].endswith("/runs/103")


def test_latest_smoke_run_by_workflow_name(tmp_path):
    smoke = run_snapshot(tmp_path)["runs"]["Smoke"]
    assert (smoke["status"], smoke["conclusion"]) == ("completed", "failure")
    assert smoke["url"].endswith("/runs/105")


def test_state_md_now_next_health_sections_as_text(tmp_path):
    state = run_snapshot(tmp_path)["state"]
    assert "#18 Dashboard work" in state["now"]
    assert "exam, then build" in state["now"]
    assert "Publish the dashboard" not in state["now"]
    assert "#19 Publish the dashboard." in state["next"]
    assert "#2 lab image." in state["next"]
    assert "Nothing today" not in state["next"]
    assert "`main`: CI green." in state["health"]
    assert "4 vCPU / 7.8 GB" in state["health"]
    assert state["now"] == state["now"].strip()


def test_decisions_md_titles_listed_in_file_order(tmp_path):
    assert run_snapshot(tmp_path)["decisions"] == [
        {"id": "D1", "title": "Factory runtime: laptop"},
        {"id": "D2", "title": "Every server change goes through Actions"},
        {"id": "D11", "title": "Owner: the manager may push"},
    ]


def test_owner_md_lists_only_open_items(tmp_path):
    assert run_snapshot(tmp_path)["owner_open"] == [
        {"id": "O4", "title": "(optional) Second-opinion reviewer"},
        {"id": "O6", "title": "Long-lived token"},
    ]


def test_throughput_closed_per_day_for_14_days_ending_at_now(tmp_path):
    days = run_snapshot(tmp_path)["throughput"]["closed_per_day"]
    # 2026-11-01 .. 2026-11-14; an issue closed on 2026-10-31 is outside the window
    assert days == [1, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 1, 2]


def test_throughput_window_follows_now(tmp_path):
    days = run_snapshot(tmp_path, now="2026-11-11T00:00:00Z")["throughput"]["closed_per_day"]
    # 2026-10-29 .. 2026-11-11: #8 on 10-31, #1 on 11-01, three on 11-10
    assert days == [0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0]


def test_throughput_median_lead_time_hours(tmp_path):
    # window durations: 24, 6, 24, 4, 10, 2, 12 -> median 10
    assert run_snapshot(tmp_path)["throughput"]["median_lead_time_hours"] == 10


def test_throughput_open_incident_count(tmp_path):
    assert run_snapshot(tmp_path)["throughput"]["open_incidents"] == 1


# ---- the static page (file level, no browser) ------------------------------

def _shipped_files() -> list[Path]:
    skip = {"tests", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
    out = []
    for p in COMPONENT.rglob("*"):
        rel = p.relative_to(COMPONENT)
        if not p.is_file() or rel.parts[0] in skip:
            continue
        if (p.name == "index.html" and len(rel.parts) == 1) or p.suffix in {".css", ".js"}:
            out.append(p)
    return out


def _page() -> str:
    return (COMPONENT / "index.html").read_text(encoding="utf-8")


def _shipped_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _shipped_files())


def test_index_html_exists():
    assert (COMPONENT / "index.html").is_file()


def test_page_ships_under_150_kb():
    assert (COMPONENT / "index.html").is_file()
    total = sum(p.stat().st_size for p in _shipped_files())
    assert total < 150 * 1024, f"{total} bytes"


def test_page_reads_snapshot_json():
    assert "snapshot.json" in _shipped_text()


def test_page_refetches_snapshot_periodically():
    text = _shipped_text()
    assert "setInterval" in text
    assert "snapshot.json" in text


def test_page_names_the_five_stations_in_order():
    page = _page()
    positions = [page.find(name) for name in ["Plan", "Exam", "Build", "Check", "Ship"]]
    assert all(p >= 0 for p in positions), positions
    assert positions == sorted(positions)


def test_page_uses_data_theme_and_has_a_theme_toggle():
    assert "data-theme" in _shipped_text()
    assert re.search(r"<button[^>]*theme", _page(), re.I), (
        "no <button> with 'theme' in its attributes"
    )


def test_page_references_poppins():
    assert "poppins" in _shipped_text().lower()


def test_page_has_viewport_meta():
    assert re.search(r'<meta[^>]+name=["\']viewport["\']', _page(), re.I)


def test_page_is_noindex():
    page = _page()
    assert re.search(r'<meta[^>]+name=["\']robots["\'][^>]*content=["\']noindex', page, re.I) \
        or re.search(r'<meta[^>]+content=["\']noindex[^>]*name=["\']robots["\']', page, re.I)


# ---- the workflow ----------------------------------------------------------

def _workflow() -> dict:
    path = REPO / ".github" / "workflows" / "factory-dashboard.yml"
    assert path.is_file(), "missing .github/workflows/factory-dashboard.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    # PyYAML reads the bare key `on` as boolean True
    return wf.get("on", wf.get(True))


def test_workflow_runs_every_3_hours():
    crons = [s["cron"] for s in _triggers(_workflow())["schedule"]]
    assert "0 */3 * * *" in crons


def test_workflow_runs_on_push_to_main():
    assert "main" in _triggers(_workflow())["push"]["branches"]


def test_workflow_can_be_dispatched_by_hand():
    assert "workflow_dispatch" in _triggers(_workflow())


def test_workflow_has_no_issue_or_pull_request_triggers():
    triggers = _triggers(_workflow())
    assert "issues" not in triggers
    assert "pull_request" not in triggers


def test_workflow_builds_the_snapshot_and_uploads_an_artifact():
    wf = _workflow()
    steps = [s for job in wf["jobs"].values() for s in job["steps"]]
    assert any("build_snapshot.py" in s.get("run", "") for s in steps)
    assert any(s.get("uses", "").startswith("actions/upload-artifact") for s in steps)
