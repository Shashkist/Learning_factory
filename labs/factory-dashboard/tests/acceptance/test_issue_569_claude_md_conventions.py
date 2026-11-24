"""Exam for issue #569: project facts from .claude/agents/*.md also live in CLAUDE.md.

Reads CLAUDE.md only. Each fact must appear, with its meaning words, inside one sentence of
the "Conventions for exams and reviews" section (not just anywhere in the file). Rules 1 to 7
stay under "Rules that are never broken", and lab isolation is a numbered rule there too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CLAUDE_MD = REPO / "CLAUDE.md"
HEADING = "Conventions for exams and reviews"


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+|\n\s*\n|\n\s*[-*]\s+", text) if s.strip()]


def _literal(*phrases: str):
    return lambda text: all(p in text for p in phrases)


def _sentence_with(*parts: str):
    pats = [re.compile(p) for p in parts]
    return lambda text: any(all(p.search(s) for p in pats) for s in _sentences(text))


def _library(lib: str):
    """The library is named inside the sentence that tells researchers to check docs."""
    return _sentence_with(re.escape(lib), r"[Rr]esearch", r"\bdocs\b")


# (case id, predicate). Each predicate runs on a single sentence or bullet of the section
# and pins the meaning words, not only the keywords.
FACTS = [
    ("pytest_exam_file_name", _literal("test_issue_<n>_<slug>.py")),
    ("playwright_exam_file_name", _literal("issue-<n>-<slug>.spec.ts")),
    (
        "exam_acceptance_location",
        _sentence_with(r"labs/<component>/tests/acceptance/", r"[Ee]xam files? live"),
    ),
    ("docker_marker", _literal("@pytest.mark.docker")),
    (
        "docker_gvisor_tests_run_in_ci_not_in_cloud_sessions",
        _sentence_with(r"Docker", r"gVisor", r"run in CI, not in cloud sessions"),
    ),
    ("manager_unit_test_location", _literal("labs/manager/tests/unit/")),
    (
        "web_unit_tests_next_to_code_as_test_ts",
        _sentence_with(r"labs/web/src/\*\*/\*\.test\.ts\(x\)", r"next to the code"),
    ),
    (
        "any_other_tests_path_and_config_files_count_as_exam",
        _sentence_with(
            r"Any other path under\s+`?labs/\*/tests/",
            r"conftest\.py",
            r"pytest\.ini",
            r"tox\.ini",
            r"setup\.cfg",
            r"counts as exam",
        ),
    ),
    (
        "lab_containers_never_reach_host_containers_internet",
        _sentence_with(
            r"[Ll]ab containers never reach", r"\bhost\b", r"other containers", r"\binternet\b"
        ),
    ),
    (
        "reviewer_checks_memory_and_disk_come_back_after_session_ends",
        _sentence_with(
            r"reviewer checks", r"\bmemory\b", r"\bdisk\b", r"come back", r"session ends"
        ),
    ),
    (
        "no_ruff_format_on_older_files_ci_does_not_check",
        _sentence_with(r"\bCI\b", r"(not|n't|never)", r"ruff format", r"older files"),
    ),
    (
        "kodekloud_observed_behaviour_is_in_spec_section_2",
        _sentence_with(r"KodeKloud", r"observed", r"spec", r"(§|section\s*)2"),
    ),
    ("library_ttyd_in_research_sentence", _library("ttyd")),
    ("library_code_server_in_research_sentence", _library("code-server")),
    ("library_gvisor_in_research_sentence", _library("gVisor")),
    ("library_fastapi_in_research_sentence", _library("FastAPI")),
    ("library_xterm_js_in_research_sentence", _library("xterm.js")),
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


@pytest.mark.parametrize("name,pred", FACTS, ids=[f[0] for f in FACTS])
def test_fact_in_claude_md_section(name, pred):
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"))
    assert pred(section), f"fact {name} not found inside the conventions section of CLAUDE.md"


def _rules(text: str) -> dict[int, str]:
    m = re.search(r"^##\s+Rules that are never broken\s*$(.*?)(?=^##\s)", text, re.S | re.M)
    assert m, 'CLAUDE.md lost the "Rules that are never broken" section'
    return {int(n): body for n, body in re.findall(r"^(\d+)\.\s+(.*)$", m.group(1), re.M)}


RULE_TITLES = {
    1: "The owner's other services come first.",
    2: "Secrets are installed by the owner, never by an agent.",
    3: "No attribution.",
    4: "Never blocked.",
    5: "Evidence before assertions.",
    6: "Finishing means closing the issue in the same breath:",
    7: "Student data is minimal:",
}


@pytest.mark.parametrize("n,title", sorted(RULE_TITLES.items()))
def test_top_rule_kept(n, title):
    rules = _rules(CLAUDE_MD.read_text(encoding="utf-8"))
    assert n in rules, f"rule {n} is gone from the rules that are never broken"
    assert rules[n].startswith(f"**{title}"), f"rule {n} lost its title: {title}"


def test_lab_isolation_is_a_numbered_rule():
    rules = _rules(CLAUDE_MD.read_text(encoding="utf-8"))
    ok = [
        n
        for n, body in rules.items()
        if n >= 8
        and re.search(r"[Ll]ab containers never reach", body)
        and re.search(r"\bhost\b", body)
        and "other containers" in body
        and re.search(r"\binternet\b", body)
        and "or a secret" in body
    ]
    assert ok, (
        "no numbered rule (8 or later) under the rules that are never broken says lab "
        "containers never reach the host, other containers, the internet or a secret"
    )
