"""Exam for issue #504 (review of #499): same-second ties, unscoped steps, and the issue shown on the card.

Only the reducer and script cases (scripts/factory_progress.py) are kept; the page cases (items 1 and 5, and
the page side of item 2) moved with the page to the factory package (D149, #571).

  2. A step logged without --issue follows the issue of the agent's previous sub-step, in the stored
     state (factory_progress.py).
"""
# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("exam68_lanes_504", HERE / "test_issue_68_live_view_lanes.py")
assert _spec and _spec.loader
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ago, render, start_pass, step, text, walk, base = m.ago, m.render, m.start_pass, m.step, m.text, m.walk, m.base


# ---- 2. a step without --issue follows the previous sub-step's issue --------------------------------------

def _journey():
    return [
        start_pass(120),
        step("manager", 3, "plan", "pick #3", "done", ago(40), title="Task 3"),
        step("manager", 86, "plan", "pick #86", "done", ago(20), title="Task 86"),
    ]


def _unscoped_step(issue):
    return {"kind": "step", "at": ago(1), "issue": issue, "title": "", "agent": "manager", "role": "manager",
            "station": "check", "substep": "verify", "status": "start", "detail": ""}


def test_unscoped_step_in_stored_state_gets_the_previous_substeps_issue():
    for missing in (None, 0):
        live = render([*_journey(), _unscoped_step(missing)])
        subs = {s["substep"]: s for s in base.agent(live, "manager")["substeps"]}
        assert subs["verify"].get("issue") == 86, (missing, subs["verify"])


def test_unscoped_step_follows_the_previous_step_not_the_agents_first_issue_via_the_script(tmp_path):
    script = str(base.REPO / "scripts" / "factory_progress.py")
    env = {**os.environ, "LF_PROGRESS_FILE": str(tmp_path / "progress.jsonl")}
    env.pop("LF_CREW_DIR", None)

    def run(*args):
        p = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
        assert p.returncode == 0, p.stdout + p.stderr

    run("pass-start", "--pass-no", "1", "--budget", "4")
    run("add", "--issue", "3", "--agent", "manager", "--role", "manager", "--station", "plan", "--substep", "pick", "--status", "done", "--title", "Task 3")
    run("add", "--issue", "86", "--agent", "manager", "--role", "manager", "--station", "plan", "--substep", "decide", "--status", "done", "--title", "Task 86")
    run("add", "--agent", "manager", "--role", "manager", "--station", "check", "--substep", "verify", "--status", "start")
    run("render", "--out", str(tmp_path / "live.json"))
    live = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))
    subs = {x["substep"]: x for x in base.agent(live, "manager")["substeps"]}
    assert subs["verify"].get("issue") == 86, subs


# ---- 2b. an unscoped step also moves the lane's station (review round 1 of the #504 PR) -------------------


def test_unscoped_step_moves_the_issue_row_station_in_the_script_output(tmp_path):
    script = str(base.REPO / "scripts" / "factory_progress.py")
    env = {**os.environ, "LF_PROGRESS_FILE": str(tmp_path / "progress.jsonl")}
    env.pop("LF_CREW_DIR", None)

    def run(*args):
        p = subprocess.run([sys.executable, script, *args], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
        assert p.returncode == 0, p.stdout + p.stderr

    run("pass-start", "--pass-no", "1", "--budget", "4")
    run("add", "--issue", "3", "--agent", "manager", "--role", "manager", "--station", "plan", "--substep", "pick", "--status", "done", "--title", "Task 3")
    run("add", "--issue", "86", "--agent", "manager", "--role", "manager", "--station", "plan", "--substep", "pick", "--status", "done", "--title", "Task 86")
    run("add", "--agent", "manager", "--role", "manager", "--station", "check", "--substep", "verify", "--status", "start")
    run("render", "--out", str(tmp_path / "live.json"))
    live = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))
    rows = {i["issue"]: i for i in live["issues"]}
    assert rows[86]["station"] == "check", rows
    assert rows[3]["station"] == "plan", rows
    assert rows[86]["title"] == "Task 86", rows


def test_unscoped_step_moves_the_issue_row_station_in_stored_state():
    live = render([*_journey(), _unscoped_step(None)])
    rows = {i["issue"]: i for i in live["issues"]}
    assert rows[86]["station"] == "check", rows
    assert rows[3]["station"] == "plan", rows
