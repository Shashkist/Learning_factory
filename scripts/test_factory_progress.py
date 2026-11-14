import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import factory_progress as fp  # noqa: E402


@pytest.fixture(autouse=True)
def events_file(tmp_path, monkeypatch):
    f = tmp_path / "progress.jsonl"
    monkeypatch.setenv("LF_PROGRESS_FILE", str(f))
    return f


def add(issue, agent, role, station, substep, status, detail=None, monkeypatch=None):
    args = ["add", "--issue", str(issue), "--agent", agent, "--role", role, "--station", station,
            "--substep", substep, "--status", status]
    if detail is not None:
        args += ["--detail-file", "-"]
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(detail))
    assert fp.main(args) == 0


def test_agents_and_substeps_follow_the_latest_status(monkeypatch):
    fp.main(["pass-start", "--pass-no", "3", "--budget", "10"])
    add(46, "impl-46", "implementer", "build", "unit tests", "start")
    add(46, "impl-46", "implementer", "build", "unit tests", "done", "12 passed", monkeypatch)
    add(46, "rev-46", "reviewer", "check", "round 1", "start")
    live = fp.render(fp.read_events())
    assert live["runner"]["state"] == "running" and live["runner"]["pass_no"] == 3
    impl = next(a for a in live["agents"] if a["id"] == "impl-46")
    assert impl["substeps"] == [dict(impl["substeps"][0], substep="unit tests", status="done", detail="12 passed")]
    assert live["issues"][0]["station"] == "check"
    assert [e["status"] for e in live["log"]] == ["start", "done", "start"]


def test_only_the_current_pass_is_rendered():
    fp.main(["pass-start"])
    add(1, "old", "implementer", "build", "x", "start")
    fp.main(["pass-end"])
    fp.main(["pass-start"])
    add(2, "new", "reviewer", "check", "y", "start")
    live = fp.render(fp.read_events())
    assert [a["id"] for a in live["agents"]] == ["new"]


def test_pass_end_makes_the_runner_idle_and_ends_running_agents():
    fp.main(["pass-start"])
    add(5, "impl-5", "implementer", "build", "code", "start")
    fp.main(["pass-end", "--result", "done"])
    live = fp.render(fp.read_events())
    assert live["runner"]["state"] == "idle" and live["agents"][0]["state"] == "ended"


def test_failures_mark_the_agent(monkeypatch):
    add(7, "impl-7", "implementer", "build", "ci", "fail", "pytest: 2 failed", monkeypatch)
    assert fp.render(fp.read_events())["agents"][0]["state"] == "failed"


def test_detail_is_cleaned_and_capped(monkeypatch):
    add(1, "a", "manager", "plan", "pick", "done", "line1\nline2\x1b[31m" + "x" * 500, monkeypatch)
    d = fp.render(fp.read_events())["log"][0]["detail"]
    assert "\n" not in d and "\x1b" not in d and len(d) <= fp.DETAIL_MAX


def test_bad_input_never_fails_the_caller(events_file, capsys):
    assert fp.main(["add", "--agent", "a", "--role", "boss", "--station", "plan", "--substep", "x", "--status", "start"]) == 0
    assert "factory_progress" in capsys.readouterr().err
    assert not events_file.exists()


def test_render_writes_valid_json(tmp_path):
    fp.main(["pass-start"])
    out = tmp_path / "live.json"
    fp.main(["render", "--out", str(out)])
    assert json.loads(out.read_text(encoding="utf-8"))["runner"]["state"] == "running"


def test_cli_runs_as_a_script(tmp_path):
    out = tmp_path / "l.json"
    r = subprocess.run([sys.executable, str(Path(fp.__file__)), "render", "--out", str(out)], capture_output=True)
    assert r.returncode == 0 and out.exists()


def test_runner_state_is_shown_between_passes():
    fp.main(["pass-start"])
    fp.main(["pass-end"])
    fp.main(["runner-state", "--state", "paused", "--note", "PAUSE file present"])
    r = fp.render(fp.read_events())["runner"]
    assert r["state"] == "paused" and r["note"] == "PAUSE file present"
