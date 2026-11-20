"""Pins the minute-saving workflow settings (#98). Text based: no PyYAML in the tooling job."""

import re
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _read(name: str) -> str:
    return (WF / name).read_text(encoding="utf-8")


def _ci_concurrency() -> str:
    block = re.search(r"^concurrency:\n((?:[ ]+.*\n)+)", _read("ci.yml"), re.MULTILINE)
    assert block, "ci.yml needs a workflow-level concurrency block"
    return block.group(1)


def test_ci_cancels_superseded_pull_request_runs_only():
    body = _ci_concurrency()
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in body


def test_ci_main_push_groups_are_per_commit():
    group = re.search(r"group:\s*(.+)", _ci_concurrency())
    assert group, "the ci.yml concurrency block needs a group"
    value = group.group(1).rstrip()
    assert "github.event.pull_request.number" in value
    assert value.endswith("|| github.sha }}")
    assert "github.ref" not in value


def test_dashboard_push_trigger_is_path_filtered():
    text = _read("factory-dashboard.yml")
    push = re.search(r"^  push:\n((?:    .*\n)+)", text, re.MULTILINE)
    assert push, "factory-dashboard.yml needs a push trigger"
    assert "branches: [main]" in push.group(1)
    paths = re.findall(r'^      - "(.+)"$', push.group(1), re.MULTILINE)
    assert paths == [
        "docs/factory/**",
        "labs/factory-dashboard/**",
        ".github/workflows/factory-dashboard.yml",
    ]


def test_dashboard_schedule_and_dispatch_unchanged():
    text = _read("factory-dashboard.yml")
    assert '- cron: "7,22,37,52 * * * *"' in text
    assert re.search(r"^  workflow_dispatch:", text, re.MULTILINE)
