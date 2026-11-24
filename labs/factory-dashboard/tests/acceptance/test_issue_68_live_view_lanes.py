"""Exam for issue #68, round 2: reducer cases that survive the removal of the page (D149, #571).

The swimlane page cases moved with the page to the factory package. What stays tests the reducer in
scripts/factory_progress.py: an issue given as the string "0" creates no issue row; a current-pass worker is
kept when one of its hook lines stamped before `pass_started` lands after the pass-start line, and that old
line does not count (workers are built only from events with `at >= pass_started`).
"""
# ruff: noqa: E501
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("exam68_base", HERE / "test_issue_68_live_view_state.py")
assert _spec and _spec.loader
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

HOURGLASS, NOW, ago, render, start_pass, step, text, walk = (
    base.HOURGLASS, base.NOW, base.ago, base.render, base.start_pass, base.step, base.text, base.walk)


# ---- reducer -----------------------------------------------------------------------------------------

def test_issue_given_as_the_string_zero_creates_no_issue_row():
    live = render([
        start_pass(),
        step("manager", "0", "ship", "manager pass", "start", ago(5), title="manager pass"),
        step("exam-4", 4, "exam", "read issue", "start", ago(3), role="exam-writer", title="Real task"),
    ])
    assert [i["issue"] for i in live["issues"]] == [4]
    assert base.agent(live, "manager")["substeps"]


def _worker(wid, role, at, event="tool-start", **kw):
    return {"kind": "worker", "at": at, "worker": wid, "role": role, "event": event, **kw}


def test_current_worker_is_kept_when_an_old_hook_line_of_it_lands_after_pass_start():
    live = render([
        start_pass(30),
        _worker("w-mgr", "manager", ago(31), tool="Grep", call="z"),   # stamped before the pass, written after pass-start
        _worker("w-mgr", "manager", ago(20), tool="Read", call="a"),
        _worker("w-mgr", "manager", ago(19), event="tool-end", call="a"),
    ])
    got = [w for w in live["workers"] if w["id"] == "w-mgr"]
    assert got, "the live worker of the current pass was dropped"
    assert got[0]["tools"] == {"Read": 1}, f"the pre-pass line counted: {got[0]['tools']}"
    assert got[0]["started"] == ago(20)
