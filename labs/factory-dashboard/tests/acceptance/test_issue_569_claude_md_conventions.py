"""Exam for issue #569: project facts from .claude/agents/*.md also live in CLAUDE.md.

Reads CLAUDE.md and the four role files only. Each fact's key phrase must appear inside the
"Conventions for exams and reviews" section of CLAUDE.md (not just anywhere in the file), and
must still be in the role file it came from (copied, not moved). The seven top rules stay.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CLAUDE_MD = REPO / "CLAUDE.md"
AGENTS = REPO / ".claude" / "agents"
HEADING = "Conventions for exams and reviews"


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+|\n\s*\n|\n\s*[-*]\s+", text) if s.strip()]


def _literal(*phrases: str):
    return lambda text: all(p in text for p in phrases)


def _sentence_with(*parts: str):
    pats = [re.compile(p) for p in parts]
    return lambda text: any(all(p.search(s) for p in pats) for s in _sentences(text))


# (case id, predicate, role files that must also satisfy it)
FACTS = [
    ("pytest_exam_file_name", _literal("test_issue_<n>_<slug>.py"), ["exam-writer.md"]),
    ("playwright_exam_file_name", _literal("issue-<n>-<slug>.spec.ts"), ["exam-writer.md"]),
    ("docker_marker", _literal("@pytest.mark.docker"), ["exam-writer.md"]),
    (
        "docker_gvisor_tests_run_only_in_ci",
        _sentence_with(r"Docker", r"gVisor", r"\bCI\b"),
        ["exam-writer.md"],
    ),
    ("manager_unit_test_location", _literal("labs/manager/tests/unit/"), ["implementer.md"]),
    ("web_unit_test_location", _literal("labs/web/src/"), ["implementer.md"]),
    (
        "tests_dirs_and_conftest_count_as_exam",
        _sentence_with(r"labs/\*/tests/", r"conftest", r"\bexam\b"),
        ["implementer.md"],
    ),
    (
        "lab_containers_never_reach_host_containers_internet",
        _sentence_with(r"\bhost\b", r"other containers", r"\binternet\b"),
        ["implementer.md", "reviewer.md"],
    ),
    (
        "memory_and_disk_come_back_after_session",
        _sentence_with(r"\bmemory\b", r"\bdisk\b", r"session"),
        ["reviewer.md"],
    ),
    ("library_ttyd", _literal("ttyd"), ["researcher.md"]),
    ("library_code_server", _literal("code-server"), ["researcher.md"]),
    ("library_gvisor", _literal("gVisor"), ["researcher.md"]),
    ("library_fastapi", _literal("FastAPI"), ["researcher.md"]),
    ("library_xterm_js", _literal("xterm.js"), ["researcher.md"]),
]


def _section(text: str) -> str:
    lines = text.splitlines()
    start = None
    level = 0
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m and HEADING in m.group(2):
            start, level = i, len(m.group(1))
            break
    assert start is not None, f'CLAUDE.md has no heading containing "{HEADING}"'
    body = []
    for line in lines[start + 1:]:
        m = re.match(r"^(#{1,6})\s", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    return "\n".join(body)


def test_section_heading_exists():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert _section(text).strip(), f'the "{HEADING}" section is empty'


@pytest.mark.parametrize("name,pred,roles", FACTS, ids=[f[0] for f in FACTS])
def test_fact_in_claude_md_section(name, pred, roles):
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"))
    assert pred(section), f"fact {name} not found inside the conventions section of CLAUDE.md"


@pytest.mark.parametrize("name,pred,roles", FACTS, ids=[f[0] for f in FACTS])
def test_fact_still_in_role_file(name, pred, roles):
    for role in roles:
        text = (AGENTS / role).read_text(encoding="utf-8")
        assert pred(text), f"fact {name} is no longer in .claude/agents/{role} (copy, do not move)"


def test_top_rules_one_to_seven_kept():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    m = re.search(r"^##\s+Rules that are never broken\s*$(.*?)(?=^##\s)", text, re.S | re.M)
    assert m, 'CLAUDE.md lost the "Rules that are never broken" section'
    numbers = re.findall(r"^(\d+)\.\s", m.group(1), re.M)
    assert numbers == [str(n) for n in range(1, 8)], f"rules are {numbers}, expected 1 to 7"
