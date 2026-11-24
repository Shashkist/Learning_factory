"""Exam for issue #487 (review of #484): stale waits, silent agents and per-step lane issues.

Only the reducer cases (scripts/factory_progress.py) are kept here; the page cases (items 1 and 2, and the
page side of item 4) moved with the page to the factory package (D149, #571).

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
