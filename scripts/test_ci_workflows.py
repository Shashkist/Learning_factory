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


_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", re.MULTILINE)
_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _unpinned(text: str) -> list[str]:
    """Third-party `uses:` refs that are not a full commit SHA with a tag comment (#482)."""
    bad = []
    for ref, rest in _USES.findall(text):
        ref = ref.strip("'\"")
        if ref.startswith(("./", "docker://")):
            continue
        if not _PINNED.match(ref) or not re.match(r"\s+#\s*v\d", rest):
            bad.append(ref)
    return bad


def test_third_party_actions_are_pinned_by_sha():
    files = sorted(WF.glob("*.yml"))
    assert files
    bad = {f.name: refs for f in files if (refs := _unpinned(f.read_text(encoding="utf-8")))}
    assert not bad, f"pin these by full commit SHA with a '# vX.Y.Z' comment: {bad}"


def test_pin_check_rejects_tags_branches_and_short_shas():
    sha = "11d5960a326750d5838078e36cf38b85af677262"
    assert _unpinned("      - uses: actions/checkout@v4\n") == ["actions/checkout@v4"]
    assert _unpinned("      - uses: actions/checkout@main\n") == ["actions/checkout@main"]
    assert _unpinned("      - uses: actions/checkout@11d5960\n") == ["actions/checkout@11d5960"]
    assert _unpinned(f"      - uses: actions/checkout@{sha}\n") == [f"actions/checkout@{sha}"]  # no tag comment
    assert _unpinned(f"      - uses: actions/checkout@{sha} # v4.4.0\n") == []
    assert _unpinned(f"        uses: astral-sh/setup-uv@{sha}  # v5.4.2\n") == []
    assert _unpinned("    uses: ./.github/workflows/smoke.yml\n") == []
