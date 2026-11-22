"""Exam for issue #487 (review of #484): stale waits, silent agents and per-step lane issues.

Same harness as the #68 exams (reducer output fed to the page in node, see dom_harness_68.js).

  1. A stale `wait` is not "now": a manager `check/ci wait` 50 min ago followed by `ship/close #86 done`
     48 min ago leaves no `now: ci` on the lane line, no "running" in the check detail and no `now: ci` on
     the card, and #86 is shipped. `tests` start, `ci` wait, `tests` start again gives `now: tests`.
  2. A silent agent (latest step `running`, last heard from 15 min or more before `generated_at`) does not
     make its lane "working": `[data-lane]` has data-running="0" and its header does not say "working".
  4. Lane steps belong to the issue they were logged for: an agent whose first event was on #3 and who
     later logs ship steps with `--issue 86` has them in lane #86 and not in lane #3. In the reducer each
     sub-step keeps its own `issue`.
"""
# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("exam68_lanes", HERE / "test_issue_68_live_view_lanes.py")
assert _spec and _spec.loader
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

HOURGLASS, ago, render, start_pass, step, text, walk = m.HOURGLASS, m.ago, m.render, m.start_pass, m.step, m.text, m.walk
base = m.base


def other():
    return step("exam-4", 4, "exam", "read issue", "start", ago(1), role="exam-writer", title="Other")


def stale_wait():
    return [
        start_pass(120),
        step("manager", 86, "check", "ci", "wait", ago(50), title="Task 86"),
        step("manager", 86, "ship", "close #86", "done", ago(48), title="Task 86"),
        other(),
    ]


def lane_head_text(root, issue: int) -> str:
    got = m.lanes(root, issue)
    assert got, f"no lane for #{issue}"
    return text(got[0])


# ---- 1. stale wait -----------------------------------------------------------------------------------

def test_stale_wait_lane_line_does_not_say_now_ci(tmp_path):
    root = m.run_page(tmp_path, render(stale_wait()))
    if m.lanes(root, 86):
        assert "now: ci" not in m.lane_now_text(root, 86)


def test_stale_wait_check_detail_shows_no_running(tmp_path):
    # another agent keeps #86's lane open so that the check station can be opened
    live = render([*stale_wait(), step("implementer-86", 86, "build", "tests", "start", ago(1), role="implementer", title="Task 86")])
    root = m.run_page(tmp_path, live, clicks=[{"lane": 86, "st": "check"}])
    m.assert_detail_not_running(root, 86, "check", "ci")


def test_stale_wait_card_does_not_say_now_ci(tmp_path):
    root = m.run_page(tmp_path, render(stale_wait()))
    assert "now: ci" not in m.card_now_text(root, "manager")
    assert "ci" not in m.card_now_text(root, "manager").replace("close", "")


def test_stale_wait_task_86_ships(tmp_path):
    root = m.run_page(tmp_path, render(stale_wait()))
    done = " ".join(text(n) for n in m.by_class(root, "lane-done"))
    assert "#86" in done, f"#86 not listed as shipped: {done!r}"
    assert not m.lanes(root, 86)


def _tests_ci_tests():
    return [
        start_pass(),
        step("manager", 86, "build", "tests", "start", ago(10), title="Task 86"),
        step("manager", 86, "check", "ci", "wait", ago(5), title="Task 86"),
        step("manager", 86, "build", "tests", "start", ago(1), title="Task 86"),
    ]


def test_tests_ci_wait_tests_again_lane_says_now_tests_not_ci(tmp_path):
    t = m.lane_now_text(m.run_page(tmp_path, render(_tests_ci_tests())), 86)
    assert t.startswith("now: ") and "tests" in t and "ci" not in t, t


def test_tests_ci_wait_tests_again_card_says_now_tests_not_ci(tmp_path):
    t = m.card_now_text(m.run_page(tmp_path, render(_tests_ci_tests())), "manager")
    assert "tests" in t and "ci" not in t, t


# ---- 2. silent agent ---------------------------------------------------------------------------------

def _silent(minutes: float):
    return [
        start_pass(120),
        step("implementer-7", 7, "build", "commit", "start", ago(minutes), role="implementer", title="Task 7"),
    ]


def test_silent_agent_does_not_make_the_lane_working(tmp_path):
    root = m.run_page(tmp_path, render(_silent(30)))
    lane = m.lanes(root, 7)[0]
    assert lane["a"].get("data-running") == "0", lane["a"]
    assert "working" not in lane_head_text(root, 7)


def test_agent_silent_for_exactly_fifteen_minutes_does_not_make_the_lane_working(tmp_path):
    root = m.run_page(tmp_path, render(_silent(15)))
    assert m.lanes(root, 7)[0]["a"].get("data-running") == "0"


def test_recently_heard_agent_makes_the_lane_working(tmp_path):
    root = m.run_page(tmp_path, render(_silent(2)))
    lane = m.lanes(root, 7)[0]
    assert lane["a"].get("data-running") == "1"
    assert "working" in lane_head_text(root, 7)


# ---- 4. lane steps by their own issue -----------------------------------------------------------------

def _two_issues():
    return [
        start_pass(120),
        step("manager", 3, "plan", "pick", "done", ago(30), title="Task 3"),
        step("manager", 86, "ship", "deploy", "start", ago(1), title="Task 86"),
    ]


def test_each_substep_keeps_its_own_issue_in_the_stored_data():
    live = render(_two_issues())
    subs = {s["substep"]: s for s in base.agent(live, "manager")["substeps"]}
    assert subs["pick"].get("issue") == 3, subs["pick"]
    assert subs["deploy"].get("issue") == 86, subs["deploy"]


def test_progress_script_keeps_each_substeps_issue_in_live_json(tmp_path):
    import os
    import subprocess
    import sys
    script = str(base.REPO / "scripts" / "factory_progress.py")
    env = {**os.environ, "LF_PROGRESS_FILE": str(tmp_path / "progress.jsonl")}
    env.pop("LF_CREW_DIR", None)

    def run(*args):
        p = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
        assert p.returncode == 0, p.stdout + p.stderr

    run("pass-start", "--pass-no", "1", "--budget", "4")
    for issue, sub, st in ((3, "pick", "plan"), (86, "deploy", "ship")):
        run("add", "--issue", str(issue), "--agent", "manager", "--role", "manager", "--station", st,
            "--substep", sub, "--status", "done", "--title", f"Task {issue}")
    run("render", "--out", str(tmp_path / "live.json"))
    live = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))
    subs = {x["substep"]: x for x in base.agent(live, "manager")["substeps"]}
    assert subs["pick"].get("issue") == 3 and subs["deploy"].get("issue") == 86, subs


def test_ship_steps_of_a_manager_that_started_on_another_issue_show_in_their_own_lane(tmp_path):
    live = render([*_two_issues(), other()])
    root = m.run_page(tmp_path, live)
    t86 = m.lane_now_text(root, 86)
    assert t86.startswith("now: ") and "deploy" in t86, t86
    if m.lanes(root, 3):
        assert "deploy" not in m.lane_now_text(root, 3)
        assert HOURGLASS not in text(m.lanes(root, 3)[0])
