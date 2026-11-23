"""Exam for issue #543 (slice 2 of #536, D143): progress lanes and the live dashboard across repositories.

Observable contract, from the issue only:
  * factory_progress.py add --repo owner/name records the event with its repository; lanes are keyed
    by (repository, issue). --issue 1 with and without --repo Shashkist/factory make two separate lanes.
    Without --repo everything works as before.
  * The page (index.html + app.js, run in node through the #68 fake browser) shows the lane of another
    repository as factory#<n> (short repository name, then #<n>); a lane of this repository still shows #<n>.
    The page is fed with the real reducer output, so the exam does not depend on how live.json names the field.
  * Redaction (public text of live.json) keeps links under https://github.com/Shashkist/factory/ and still
    removes every other foreign URL.
Events are written by the real CLI into a temp events file (LF_PROGRESS_FILE); their timestamps are then moved
onto the fixed clock of the page harness. No network, no machine environment (LF_CREW_DIR is unset).
"""
# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("exam543_lanes", HERE / "test_issue_68_live_view_lanes.py")
assert _spec and _spec.loader
lanes_exam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lanes_exam)
base = lanes_exam.base

SCRIPT = base.REPO / "scripts" / "factory_progress.py"
FOREIGN = "Shashkist/factory"
OK_URL = "https://github.com/Shashkist/factory/pull/1"
BAD_URLS = ["https://github.com/someone/else/pull/1", "https://example.com/x"]


def cli(events_file: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("LF_CREW_DIR", "LF_PROGRESS_FILE")}
    env["LF_PROGRESS_FILE"] = str(events_file)
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", env=env, cwd=base.REPO, timeout=60)


def add(events_file: Path, issue: int, *, repo: str | None = None, agent="manager", role="manager",
        station="build", substep="tests", status="start", title="", detail: str | None = None):
    args = ["add", "--issue", str(issue), "--agent", agent, "--role", role, "--station", station,
            "--substep", substep, "--status", status, "--title", title]
    if repo:
        args += ["--repo", repo]
    if detail is not None:
        args += ["--detail-file", "-"]
    p = cli(events_file, *args, stdin=detail or "")
    assert p.returncode == 0, p.stdout + p.stderr
    return p


def events(events_file: Path) -> list[dict]:
    assert events_file.exists(), "add wrote no events file"
    return [json.loads(x) for x in events_file.read_text(encoding="utf-8").splitlines() if x.strip()]


def live_from(events_file: Path) -> dict:
    """Render the events the CLI wrote, with timestamps moved onto the fixed clock of the page harness."""
    evs = events(events_file)
    for i, e in enumerate(evs):
        e["at"] = base.ago(30 - i * 0.1)
    return base.render([base.start_pass(45), *evs])


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("LF_CREW_DIR", raising=False)
    monkeypatch.delenv("LF_PROGRESS_FILE", raising=False)


@pytest.fixture
def ef(tmp_path) -> Path:
    return tmp_path / "progress.jsonl"


def lane_heads(root) -> list[str]:
    return [base.text(n).strip() for n in lanes_exam.by_class(root, "lane-head")]


# ---- criterion 1: two separate lanes ---------------------------------------------------------------

def test_add_accepts_repo_and_records_the_event(ef):
    add(ef, 1, repo=FOREIGN, title="Foreign task")
    got = [e for e in events(ef) if e.get("kind") == "step"]
    assert len(got) == 1 and got[0]["issue"] == 1
    assert FOREIGN in json.dumps(got[0]), "the event does not record its repository"


def test_add_without_repo_is_unchanged(ef):
    add(ef, 1, title="Local task")
    live = live_from(ef)
    assert [i["issue"] for i in live["issues"]] == [1]
    assert [a["issue"] for a in live["agents"]] == [1]


def test_same_issue_number_in_two_repositories_makes_two_separate_lanes(ef):
    add(ef, 1, repo=FOREIGN, agent="implementer-f", role="implementer", title="Foreign task")
    add(ef, 1, agent="implementer-l", role="implementer", title="Local task")
    live = live_from(ef)
    assert len(live["issues"]) == 2, live["issues"]
    assert sorted(i["title"] for i in live["issues"]) == ["Foreign task", "Local task"]


def test_one_agent_working_in_both_repositories_still_makes_two_lanes(ef):
    add(ef, 1, repo=FOREIGN, substep="foreign step", title="Foreign task")
    add(ef, 1, substep="local step", title="Local task")
    live = live_from(ef)
    assert len(live["issues"]) == 2, live["issues"]


# ---- criterion 2: the page -----------------------------------------------------------------------

def test_page_shows_the_other_repository_lane_as_factory_hash_1_and_this_one_as_hash_1(ef, tmp_path):
    add(ef, 1, repo=FOREIGN, agent="implementer-f", role="implementer", title="Foreign task")
    add(ef, 1, agent="implementer-l", role="implementer", title="Local task")
    heads = lane_heads(lanes_exam.run_page(tmp_path, live_from(ef)))
    assert len(heads) == 2, heads
    foreign = [h for h in heads if "Foreign task" in h]
    local = [h for h in heads if "Local task" in h]
    assert len(foreign) == 1 and foreign[0].startswith("factory#1"), heads
    assert len(local) == 1 and local[0].startswith("#1") and not local[0].startswith("factory"), heads


def test_page_lane_of_this_repository_alone_is_unchanged(ef, tmp_path):
    add(ef, 7, agent="implementer-l", role="implementer", title="Local task")
    heads = lane_heads(lanes_exam.run_page(tmp_path, live_from(ef)))
    assert len(heads) == 1 and heads[0].startswith("#7 Local task"), heads


def test_page_does_not_prefix_the_other_repository_with_the_owner(ef, tmp_path):
    add(ef, 2, repo=FOREIGN, agent="implementer-f", role="implementer", title="Foreign task")
    heads = lane_heads(lanes_exam.run_page(tmp_path, live_from(ef)))
    assert len(heads) == 1 and heads[0].startswith("factory#2") and "Shashkist" not in heads[0], heads


# ---- criterion 3: redaction ---------------------------------------------------------------------------

def test_redaction_keeps_links_under_shashkist_factory(ef):
    add(ef, 1, repo=FOREIGN, detail=f"merged {OK_URL}")
    assert OK_URL in json.dumps(live_from(ef))


def test_redaction_removes_other_foreign_urls(ef):
    add(ef, 1, repo=FOREIGN, detail=f"see {BAD_URLS[0]} and {BAD_URLS[1]}")
    blob = json.dumps(live_from(ef))
    for u in BAD_URLS:
        assert u not in blob, f"{u} reached live.json"


def test_redaction_in_one_text_keeps_the_allowed_link_and_removes_the_others(ef):
    add(ef, 1, repo=FOREIGN, detail=f"{OK_URL} {BAD_URLS[0]} {BAD_URLS[1]}")
    blob = json.dumps(live_from(ef))
    assert OK_URL in blob
    for u in BAD_URLS:
        assert u not in blob


def test_redaction_does_not_allow_a_lookalike_repository_name(ef):
    lookalike = "https://github.com/Shashkist/factory-evil/pull/1"
    add(ef, 1, repo=FOREIGN, detail=f"see {lookalike}")
    assert lookalike not in json.dumps(live_from(ef))


def test_redaction_in_titles_follows_the_same_rule(ef):
    add(ef, 1, repo=FOREIGN, title=f"{OK_URL} {BAD_URLS[1]}")
    blob = json.dumps(live_from(ef))
    assert OK_URL in blob and BAD_URLS[1] not in blob


def test_redaction_still_keeps_this_repository_links(ef):
    ok = "https://github.com/Shashkist/Learning_factory/pull/1"
    add(ef, 1, detail=f"merged {ok}")
    assert ok in json.dumps(live_from(ef))
