"""Pins the minute-saving workflow settings (#98). Text based: no PyYAML in the tooling job."""

import re
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _read(name: str) -> str:
    return (WF / name).read_text(encoding="utf-8")


def test_ci_cancels_superseded_pull_request_runs_only():
    text = _read("ci.yml")
    block = re.search(r"^concurrency:\n((?:[ ]+.*\n)+)", text, re.M)
    assert block, "ci.yml needs a workflow-level concurrency block"
    body = block.group(1)
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in body


def test_ci_main_push_groups_are_per_commit():
    body = re.search(r"^concurrency:\n((?:[ ]+.*\n)+)", _read("ci.yml"), re.M).group(1)
    group = re.search(r"group:\s*(.+)", body).group(1)
    assert "github.event.pull_request.number" in group
    assert group.rstrip().endswith("|| github.sha }}")
    assert "github.ref" not in group


def test_dashboard_push_trigger_is_path_filtered():
    text = _read("factory-dashboard.yml")
    push = re.search(r"^  push:\n((?:    .*\n)+)", text, re.M).group(1)
    assert "branches: [main]" in push
    paths = re.findall(r'^      - "(.+)"$', push, re.M)
    assert paths == [
        "docs/factory/**",
        "labs/factory-dashboard/**",
        ".github/workflows/factory-dashboard.yml",
    ]


def test_dashboard_schedule_and_dispatch_unchanged():
    text = _read("factory-dashboard.yml")
    assert '- cron: "7,22,37,52 * * * *"' in text
    assert re.search(r"^  workflow_dispatch:", text, re.M)
