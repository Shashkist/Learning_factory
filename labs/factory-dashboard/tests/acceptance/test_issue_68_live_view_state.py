"""Exam for issue #68 (items 1, 2, 3 and the sticky "failed" state; items 4-6 are #481).

The reducer (scripts/factory_progress.py render()) is loaded by path and driven with events; the page
(index.html + app.js) is run in node through the #51 fake browser (dom_harness_51.js). Node is required.

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
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
COMPONENT = HERE.parents[1]
REPO = COMPONENT.parents[1]
INDEX = COMPONENT / "index.html"
APP = COMPONENT / "app.js"
HARNESS = HERE / "dom_harness_51.js"

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

def run_page(tmp_path: Path, live: dict) -> dict:
    node = shutil.which("node")
    assert node, "node is required for the live-view exam (ubuntu-latest has it)"
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"now": iso(NOW), "steps": [{"now": iso(NOW), "live": live}]}), encoding="utf-8")
    p = subprocess.run([node, str(HARNESS), str(INDEX), str(APP), str(scenario)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    root = json.loads(p.stdout.strip().splitlines()[-1])["steps"][0]["live"]
    assert root is not None, "no element with id=live in index.html"
    return root


def text(n) -> str:
    return n if isinstance(n, str) else "".join(text(c) for c in n["c"])


def walk(n):
    if isinstance(n, str):
        return
    yield n
    for c in n["c"]:
        yield from walk(c)


def substep_text(root: dict, agent_id: str, name: str) -> str:
    cards = [n for n in walk(root) if n["a"].get("data-agent-id") == agent_id]
    assert cards, f"no agent card {agent_id}"
    subs = [n for c in cards for n in walk(c) if n["a"].get("data-substep") == name]
    assert subs, f"no [data-substep={name!r}] on the card of {agent_id}"
    return text(subs[0])


def assert_unfinished(t: str) -> None:
    assert HOURGLASS not in t, f"still shown as running: {t!r}"
    assert DASH in t, f"expected the neutral dash in {t!r}"


# ---- item 1: stale hourglass --------------------------------------------------------------------

def test_item1_older_unfinished_substep_is_not_running_once_a_later_one_exists(tmp_path):
    live = render([
        start_pass(),
        step("manager", 3, "plan", "pick", "start", ago(12)),
        step("manager", 3, "build", "merge", "start", ago(10)),
        step("manager", 3, "check", "smoke liveness", "start", ago(8)),
        step("manager", 3, "ship", "close", "done", ago(2)),
    ])
    root = run_page(tmp_path, live)
    for name in ("pick", "merge", "smoke liveness"):
        assert_unfinished(substep_text(root, "manager", name))
    assert "✅" in substep_text(root, "manager", "close")


def test_item1_latest_substep_keeps_the_hourglass_while_recent(tmp_path):
    live = render([
        start_pass(),
        step("manager", 3, "plan", "pick", "done", ago(8)),
        step("manager", 3, "build", "merge", "start", ago(2)),
    ])
    root = run_page(tmp_path, live)
    assert HOURGLASS in substep_text(root, "manager", "merge")
    assert "✅" in substep_text(root, "manager", "pick")


def test_item1_open_substep_is_closed_when_the_same_agent_logs_a_later_step_for_that_issue(tmp_path):
    """The lf-c7d42 case: `ship / deploy` stayed at start, then the manager went straight to `close`."""
    live = render([
        start_pass(),
        step("manager", 86, "ship", "deploy", "start", ago(6)),
        step("manager", 86, "ship", "close #86", "done", ago(1)),
    ])
    root = run_page(tmp_path, live)
    assert_unfinished(substep_text(root, "manager", "deploy"))
    assert "✅" in substep_text(root, "manager", "close #86")


def test_item1_open_substep_is_not_running_after_finished(tmp_path):
    live = render([
        start_pass(),
        step("reviewer-9", 9, "check", "verdict", "start", ago(5), role="reviewer"),
        step("reviewer-9", 9, "check", "finished", "done", ago(1), role="reviewer"),
    ])
    root = run_page(tmp_path, live)
    assert_unfinished(substep_text(root, "reviewer-9", "verdict"))


def test_item1_silent_for_more_than_15_minutes_is_not_running_even_if_latest(tmp_path):
    live = render([
        start_pass(),
        step("implementer-7", 7, "build", "tests", "done", ago(25), role="implementer"),
        step("implementer-7", 7, "build", "commit", "start", ago(20), role="implementer"),
    ])
    root = run_page(tmp_path, live)
    assert_unfinished(substep_text(root, "implementer-7", "commit"))


def test_item1_silence_is_judged_per_agent(tmp_path):
    """A busy manager does not keep another agent's old hourglass alive."""
    live = render([
        start_pass(),
        step("implementer-7", 7, "build", "commit", "start", ago(20), role="implementer"),
        step("manager", 3, "plan", "pick", "start", ago(1)),
    ])
    root = run_page(tmp_path, live)
    assert_unfinished(substep_text(root, "implementer-7", "commit"))
    assert HOURGLASS in substep_text(root, "manager", "pick")


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


def test_item2_no_zero_issue_visible_on_the_page(tmp_path):
    live = render([
        start_pass(),
        step("manager", 0, "ship", "manager pass", "start", ago(5), title="manager pass"),
        step("exam-4", 4, "exam", "read issue", "start", ago(3), role="exam-writer", title="Real task"),
    ])
    root = run_page(tmp_path, live)
    assert not re.search(r"#0(?!\d)", text(root)), "an issue 0 is visible"
    assert "#4" in text(root)


def test_item2_an_older_live_json_with_an_issue_zero_row_does_not_render_it(tmp_path):
    live = render([
        start_pass(),
        step("exam-4", 4, "exam", "read issue", "start", ago(3), role="exam-writer", title="Real task"),
    ])
    live["issues"].insert(0, {"issue": 0, "title": "manager pass", "station": "ship"})
    root = run_page(tmp_path, live)
    assert not re.search(r"#0(?!\d)", text(root))
    stations = "".join(text(n) for n in walk(root) if "data-live-station" in n["a"])
    assert "manager pass" not in stations


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


def test_item3_page_renders_without_workers(tmp_path):
    live = render([start_pass(), step("manager", 3, "plan", "pick", "start", ago(1))])
    live.pop("workers")
    root = run_page(tmp_path, live)
    assert any(n["a"].get("data-agent-id") == "manager" for n in walk(root))


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
