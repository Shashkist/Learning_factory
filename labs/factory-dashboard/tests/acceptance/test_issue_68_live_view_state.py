"""Exam for issue #68 (items 1, 2, 3 and the sticky "failed" state; items 4-6 are #481).

The reducer (scripts/factory_progress.py render()) is loaded by path and driven with events. The page cases
of this exam moved with the page to the factory package (D149, #571); only the reducer cases stay.

CONTRACT (decided from the issue text and the advice comment)

Item 1, stale hourglass. On an agent card each sub-step is one `[data-substep="<name>"]` element.
  * Its text shows the hourglass only while the sub-step is "live": its status is `start` AND it is the
    agent's latest sub-step AND the agent has been silent for less than 15 minutes (agent `last` vs
    live.json `generated_at`).
  * A sub-step that started and never got `done`, once the same agent has logged a later sub-step or
    `finished` (also on another station), or once the agent was silent for 15 min or more, shows no
    hourglass and shows the neutral dash "–" (en dash, U+2013) instead.
  * Sub-steps that are `done` keep the check mark. The fix may live in the reducer or in the page: the
    exam feeds the page with the real reducer's output and only looks at the page.
Item 2, no issue row without an issue. Events with no issue, or issue 0, create no entry in live.json
  `issues` and nothing "#0" is visible in #live, also when an older live.json that still holds a
  `{"issue": 0}` row is served. The agent that logged them still gets its card.
Item 3, workers of the current pass only. live.json `workers` holds only workers whose first event is not
  earlier than the pass start (`runner.pass_started`), judged by the event timestamp `at` and not by the
  position in the file (a late hook line of the previous pass can land after the new pass-start).
Sticky failed. Reducer agent state: a `fail` sets `failed`; a later `start` or `done` sub-step of the same
  agent puts it back to `running`; `failed` stays only while the failing sub-step is the latest one. A
  `finished`/`done` still gives `done`; `stopped` stays sticky (unchanged).
"""
# ruff: noqa: E501
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
COMPONENT = HERE.parents[1]
REPO = COMPONENT.parents[1]

NOW = datetime(2026, 11, 22, 10, 0, 0, tzinfo=UTC)
HOURGLASS = "⏳"
DASH = "–"


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def ago(minutes: float = 0) -> str:
    return iso(NOW - timedelta(minutes=minutes))


def _fp():
    spec = importlib.util.spec_from_file_location("factory_progress_68", REPO / "scripts" / "factory_progress.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.now = lambda: iso(NOW)
    return mod


def step(agent, issue, station, sub, status, at, role="manager", title="Some task", detail=""):
    return {"kind": "step", "at": at, "issue": issue, "title": title, "agent": agent, "role": role,
            "station": station, "substep": sub, "status": status, "detail": detail}


def start_pass(minutes_ago: float = 60) -> dict:
    return {"kind": "pass-start", "at": ago(minutes_ago), "pass_no": 5, "budget": 8}


@pytest.fixture(scope="module", autouse=True)
def _no_crew_dir():
    """The laptop's runner sets LF_CREW_DIR (crew view from local transcripts); the exam must not read it."""
    mp = pytest.MonkeyPatch()
    mp.delenv("LF_CREW_DIR", raising=False)
    yield
    mp.undo()


def render(events: list[dict]) -> dict:
    return _fp().render(events)


def agent(live: dict, aid: str) -> dict:
    got = [a for a in live["agents"] if a["id"] == aid]
    assert got, f"no agent {aid} in live.json"
    return got[0]


# ---- running the page --------------------------------------------------------------------------


def text(n) -> str:
    return n if isinstance(n, str) else "".join(text(c) for c in n["c"])


def walk(n):
    if isinstance(n, str):
        return
    yield n
    for c in n["c"]:
        yield from walk(c)


# ---- item 2: no issue row without an issue ------------------------------------------------------

def test_item2_events_without_an_issue_create_no_issue_row():
    live = render([
        start_pass(),
        step("manager", None, "ship", "manager pass", "start", ago(5), title=""),
        step("manager", 0, "ship", "manager pass 0", "start", ago(4), title="manager pass"),
        step("exam-4", 4, "exam", "read issue", "start", ago(3), role="exam-writer", title="Real task"),
    ])
    assert [i["issue"] for i in live["issues"]] == [4]
    assert agent(live, "manager")["substeps"], "the agent keeps its card and sub-steps"


# ---- item 3: workers of the current pass only ---------------------------------------------------

def _worker(wid, role, at, event="tool-start", **kw):
    return {"kind": "worker", "at": at, "worker": wid, "role": role, "event": event, **kw}


def test_item3_workers_from_an_earlier_pass_are_not_listed_even_if_their_line_lands_late():
    live = render([
        start_pass(30),
        # the previous pass's manager: its lines carry earlier timestamps but were appended after pass-start
        _worker("old-mgr", "manager", ago(90), tool="Bash", call="a"),
        _worker("old-mgr", "manager", ago(89), event="tool-end", call="a"),
        _worker("old-mgr", "manager", ago(31), event="idle"),
        _worker("new-mgr", "manager", ago(20), tool="Bash", call="b"),
    ])
    ids = [w["id"] for w in live["workers"]]
    assert ids == ["new-mgr"], ids


def test_item3_workers_that_started_in_the_current_pass_stay():
    live = render([
        start_pass(30),
        _worker("w-mgr", "manager", ago(25), tool="Read", call="a"),
        _worker("w-exam", "exam-writer", ago(5), event="waiting"),
    ])
    assert sorted(w["id"] for w in live["workers"]) == ["w-exam", "w-mgr"]


# ---- sticky failed ------------------------------------------------------------------------------

def _state(events) -> str:
    return agent(render([start_pass(), *events]), "manager")["state"]


def test_sticky_failed_stays_while_the_failing_substep_is_the_latest():
    assert _state([step("manager", 86, "check", "ci round 1", "start", ago(9)),
                   step("manager", 86, "check", "ci round 1", "fail", ago(8))]) == "failed"


def test_sticky_failed_fail_then_later_done_is_running_again():
    assert _state([step("manager", 86, "check", "ci round 1", "fail", ago(9)),
                   step("manager", 86, "check", "ci round 2", "done", ago(5))]) == "running"


def test_sticky_failed_fail_then_later_start_is_running_again():
    assert _state([step("manager", 86, "check", "ci round 1", "fail", ago(9)),
                   step("manager", 86, "check", "ci round 2", "start", ago(5))]) == "running"


def test_sticky_failed_a_second_failure_after_recovery_is_failed_again():
    assert _state([step("manager", 86, "check", "ci round 1", "fail", ago(9)),
                   step("manager", 86, "check", "ci round 2", "done", ago(7)),
                   step("manager", 86, "ship", "deploy", "fail", ago(2))]) == "failed"


def test_sticky_failed_finished_after_a_failure_is_done():
    assert _state([step("manager", 86, "check", "ci round 1", "fail", ago(9)),
                   step("manager", 86, "ship", "finished", "done", ago(2))]) == "done"


def test_sticky_failed_does_not_clear_stopped():
    assert _state([step("manager", 86, "check", "ci", "stopped", ago(9)),
                   step("manager", 86, "check", "later", "done", ago(2))]) == "stopped"
