# ruff: noqa: E501
"""Exam for issue #18 (factory dashboard): the workflow cases that survive the removal of the builder.

The builder and page cases (snapshot counts, claim, PRs, runs, STATE/DECISIONS/OWNER parsing, throughput,
page size, theme, noindex) moved to Shashkist/factory (D149, #571). What stays reads
.github/workflows/factory-dashboard.yml only: schedule, push and dispatch triggers, and the build + artifact step.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]


# ---- the workflow ----------------------------------------------------------

def _workflow() -> dict:
    path = REPO / ".github" / "workflows" / "factory-dashboard.yml"
    assert path.is_file(), "missing .github/workflows/factory-dashboard.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    # PyYAML reads the bare key `on` as boolean True
    return wf.get("on", wf.get(True))


def test_workflow_runs_every_15_minutes():
    # D71 (#155) supersedes D12's 3 h: the repository is public, Actions minutes are free.
    crons = [s["cron"] for s in _triggers(_workflow())["schedule"]]
    assert "7,22,37,52 * * * *" in crons


def test_schedule_avoids_the_busy_quarter_hours():
    # D73 (#155): same 15-minute cadence, but never at minute 0, 15, 30 or 45.
    crons = [s["cron"] for s in _triggers(_workflow())["schedule"]]
    assert crons
    for cron in crons:
        assert re.fullmatch(r"\d+(,\d+)*", cron.split()[0]), cron
        minutes = {int(m) for m in cron.split()[0].split(",")}
        assert len(minutes) == 4
        assert not minutes & {0, 15, 30, 45}


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
    # D149 (#568): the workflow runs the factory package, not build_snapshot.py.
    assert any("factory_tools.dashboard build" in s.get("run", "") for s in steps)
    assert any(s.get("uses", "").startswith("actions/upload-artifact") for s in steps)
