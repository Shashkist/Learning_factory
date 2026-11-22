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
_DIGEST = re.compile(r"^docker://\S+@sha256:[0-9a-f]{64}$")
_TAG_COMMENT = re.compile(r"\s+#\s*v\d+\.\d+\.\d+\b")


def _unpinned(text: str) -> list[str]:
    """Third-party `uses:` refs that are not a full commit SHA with a `# vX.Y.Z` comment (#482).

    Local `./` workflows are allowed; `docker://` images only by `@sha256:` digest. Only
    block-style `uses:` keys are parsed (the form these workflows use), not quoted keys or flow maps.
    """
    bad = []
    for ref, rest in _USES.findall(text):
        ref = ref.strip("'\"")
        if ref.startswith("./") or _DIGEST.match(ref):
            continue
        if not _PINNED.match(ref) or not _TAG_COMMENT.match(rest):
            bad.append(ref)
    return bad


def _workflow_files() -> list[Path]:
    actions = WF.parent / "actions"
    return sorted([*WF.glob("*.y*ml"), *actions.glob("**/action.y*ml")])


def test_third_party_actions_are_pinned_by_sha():
    files = _workflow_files()
    assert files
    bad = {f.name: refs for f in files if (refs := _unpinned(f.read_text(encoding="utf-8")))}
    assert not bad, f"pin these by full commit SHA with a '# vX.Y.Z' comment: {bad}"


def test_pin_check_rejects_tags_branches_and_short_shas():
    sha = "11d5960a326750d5838078e36cf38b85af677262"
    assert _unpinned("      - uses: actions/checkout@v4\n") == ["actions/checkout@v4"]
    assert _unpinned("      - uses: actions/checkout@main\n") == ["actions/checkout@main"]
    assert _unpinned("      - uses: actions/checkout@11d5960\n") == ["actions/checkout@11d5960"]
    assert _unpinned(f"      - uses: actions/checkout@{sha}\n") == [f"actions/checkout@{sha}"]  # no tag comment
    assert _unpinned(f"      - uses: actions/checkout@{sha} # v4\n") == [f"actions/checkout@{sha}"]  # not vX.Y.Z
    assert _unpinned("      - uses: 'actions/checkout@v4'\n") == ["actions/checkout@v4"]
    assert _unpinned('      - uses: "actions/checkout@v4"\n') == ["actions/checkout@v4"]
    assert _unpinned("    uses: org/repo/.github/workflows/x.yml@v1\n") == ["org/repo/.github/workflows/x.yml@v1"]
    assert _unpinned("      - uses: docker://alpine:3.20\n") == ["docker://alpine:3.20"]
    assert _unpinned(f"      - uses: actions/checkout@{sha} # v4.4.0\n") == []
    assert _unpinned(f"      - uses: 'actions/checkout@{sha}' # v4.4.0\n") == []
    assert _unpinned(f"        uses: astral-sh/setup-uv@{sha}  # v5.4.2\n") == []
    assert _unpinned(f"    uses: org/repo/.github/workflows/x.yml@{sha} # v1.0.0\n") == []
    assert _unpinned("      - uses: docker://alpine@sha256:" + "a" * 64 + "\n") == []
    assert _unpinned("    uses: ./.github/workflows/smoke.yml\n") == []


def test_dependabot_keeps_action_pins_current():
    # #485, D138: SHA pins never update by themselves; Dependabot moves them every week.
    text = (WF.parent / "dependabot.yml").read_text(encoding="utf-8")
    assert re.search(r"^version:\s*2\s*$", text, re.MULTILINE)
    entry = re.search(r'package-ecosystem:\s*"?github-actions"?\n((?:[ ]{4,}.*\n)+)', text)
    assert entry, "dependabot.yml needs a github-actions entry"
    body = entry.group(1)
    assert re.search(r'directory:\s*"?/"?\s*$', body, re.MULTILINE)
    assert re.search(r'interval:\s*"?weekly"?\s*$', body, re.MULTILINE)
    assert re.search(r"^    cooldown:\n      default-days:[ ]*[1-9]\d*[ ]*$", body, re.MULTILINE), "wait for new releases"
    group = re.search(r"groups:\n((?:[ ]{6,}.*\n)+)", body)
    assert group, "minor and patch bumps come grouped"
    assert re.search(r'update-types:\n\s+-\s*"?minor"?\n\s+-\s*"?patch"?\n', group.group(1))
    assert "major" not in group.group(1), "a major bump comes as its own PR"
    # Nothing that silently turns the updates off.
    assert not re.search(r"open-pull-requests-limit:\s*0\b", body)
    assert not re.search(r"""dependency-name:\s*['"]?\*['"]?\s*$""", body, re.MULTILINE)
