"""Exam for issue #527 (review of #526): an unscoped done/fail closes the step it started.

Same harness as the #504 exam (the reducer in scripts/factory_progress.py, fed events or the real CLI).

  1. ci wait starts on #3, merge starts on #86, ci wait is closed without --issue: #3 ci wait ends in
     lane #3 (done or failed) and #86 gets no ci wait row.
  2. Several open steps of the agent match station + sub-step: the latest one is closed.
  3. No open step matches: the D141 previous-step fallback still applies (the #504 behaviour).
  4. Another agent's open step with the same station + sub-step is never closed.
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
_spec = importlib.util.spec_from_file_location("exam504_ties_527", HERE / "test_issue_504_live_view_ties.py")
assert _spec and _spec.loader
t504 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t504)

ago, render, start_pass, base = t504.ago, t504.render, t504.start_pass, t504.base


def ev(agent, issue, station, sub, status, at, role="manager"):
    return {"kind": "step", "at": at, "issue": issue, "title": "", "agent": agent, "role": role,
            "station": station, "substep": sub, "status": status, "detail": ""}


def steps_of(live, agent, sub):
    return [s for s in base.agent(live, agent)["substeps"] if s["substep"] == sub]


def by_issue(live, agent, sub):
    return {s.get("issue"): s["status"] for s in steps_of(live, agent, sub)}


def _scenario(closing):
    return [start_pass(120),
            ev("manager", 3, "check", "ci wait", "start", ago(30)),
            ev("manager", 86, "ship", "merge", "start", ago(20)),
            ev("manager", None, "check", "ci wait", closing, ago(10))]


# ---- 1. the issue scenario ---------------------------------------------------------------------------------

@pytest.mark.parametrize("closing", ["done", "fail"])
def test_unscoped_ci_wait_done_or_fail_closes_the_step_in_issue_3(closing):
    live = render(_scenario(closing))
    assert by_issue(live, "manager", "ci wait").get(3) == closing, steps_of(live, "manager", "ci wait")


@pytest.mark.parametrize("closing", ["done", "fail"])
def test_unscoped_close_adds_no_ci_wait_row_to_issue_86(closing):
    live = render(_scenario(closing))
    assert 86 not in by_issue(live, "manager", "ci wait"), steps_of(live, "manager", "ci wait")
    assert len(steps_of(live, "manager", "ci wait")) == 1, steps_of(live, "manager", "ci wait")


def test_the_merge_step_in_86_is_left_open():
    live = render(_scenario("done"))
    assert by_issue(live, "manager", "merge") == {86: "start"}, steps_of(live, "manager", "merge")


def test_same_scenario_through_the_script(tmp_path):
    script = str(base.REPO / "scripts" / "factory_progress.py")
    env = {**os.environ, "LF_PROGRESS_FILE": str(tmp_path / "progress.jsonl")}
    env.pop("LF_CREW_DIR", None)

    def run(*args):
        p = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
        assert p.returncode == 0, p.stdout + p.stderr

    def add(station, sub, status, issue=None):
        extra = ["--issue", str(issue)] if issue else []
        run("add", *extra, "--agent", "manager", "--role", "manager", "--station", station, "--substep", sub, "--status", status)

    run("pass-start", "--pass-no", "1", "--budget", "4")
    add("check", "ci wait", "start", 3)
    add("ship", "merge", "start", 86)
    add("check", "ci wait", "done")
    run("render", "--out", str(tmp_path / "live.json"))
    live = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))
    assert by_issue(live, "manager", "ci wait") == {3: "done"}, steps_of(live, "manager", "ci wait")


# ---- 2. several open steps match: the latest is closed -----------------------------------------------------

def test_latest_matching_open_step_is_the_one_closed():
    live = render([start_pass(120),
                   ev("manager", 3, "check", "ci wait", "start", ago(30)),
                   ev("manager", 5, "check", "ci wait", "start", ago(25)),
                   ev("manager", 86, "ship", "merge", "start", ago(20)),
                   ev("manager", None, "check", "ci wait", "done", ago(10))])
    assert by_issue(live, "manager", "ci wait") == {3: "start", 5: "done"}, steps_of(live, "manager", "ci wait")


def test_an_already_closed_step_is_not_matched_again():
    live = render([start_pass(120),
                   ev("manager", 3, "check", "ci wait", "start", ago(30)),
                   ev("manager", 5, "check", "ci wait", "start", ago(25)),
                   ev("manager", 5, "check", "ci wait", "done", ago(22)),
                   ev("manager", 86, "ship", "merge", "start", ago(20)),
                   ev("manager", None, "check", "ci wait", "done", ago(10))])
    assert by_issue(live, "manager", "ci wait") == {3: "done", 5: "done"}, steps_of(live, "manager", "ci wait")


# ---- 3. no open match: the D141 fallback still applies ------------------------------------------------------

def test_without_an_open_match_an_unscoped_step_lands_in_the_previous_substeps_issue():
    live = render([start_pass(120),
                   ev("manager", 3, "plan", "pick #3", "done", ago(40)),
                   ev("manager", 86, "plan", "pick #86", "done", ago(20)),
                   ev("manager", None, "check", "verify", "start", ago(1))])
    assert by_issue(live, "manager", "verify") == {86: "start"}, steps_of(live, "manager", "verify")


def test_an_unscoped_done_with_nothing_open_to_close_lands_in_the_latest_issue():
    live = render([start_pass(120),
                   ev("manager", 3, "check", "ci wait", "done", ago(30)),
                   ev("manager", 86, "ship", "merge", "start", ago(20)),
                   ev("manager", None, "check", "ci wait", "done", ago(10))])
    assert by_issue(live, "manager", "ci wait") == {3: "done", 86: "done"}, steps_of(live, "manager", "ci wait")


# ---- 4. another agent step is never closed -------------------------------------------------------------------

def test_another_agents_open_step_is_not_closed_by_an_unscoped_done():
    live = render([start_pass(120),
                   ev("manager", 3, "check", "ci wait", "start", ago(30)),
                   ev("impl-90", 90, "check", "ci wait", "start", ago(5), role="implementer"),
                   ev("manager", 86, "ship", "merge", "start", ago(4)),
                   ev("manager", None, "check", "ci wait", "done", ago(1))])
    assert by_issue(live, "manager", "ci wait") == {3: "done"}, steps_of(live, "manager", "ci wait")
    assert by_issue(live, "impl-90", "ci wait") == {90: "start"}, steps_of(live, "impl-90", "ci wait")


def test_another_agents_matching_step_does_not_count_as_an_open_match():
    live = render([start_pass(120),
                   ev("impl-3", 3, "check", "ci wait", "start", ago(30), role="implementer"),
                   ev("manager", 86, "ship", "merge", "start", ago(20)),
                   ev("manager", None, "check", "ci wait", "done", ago(10))])
    assert by_issue(live, "impl-3", "ci wait") == {3: "start"}, steps_of(live, "impl-3", "ci wait")
    assert by_issue(live, "manager", "ci wait") == {86: "done"}, steps_of(live, "manager", "ci wait")
