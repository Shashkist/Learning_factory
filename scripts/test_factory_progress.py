import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import _hook_io  # noqa: E402
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


def hook(payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(payload)))
    assert fp.main(["hook"]) == 0


def test_hooks_show_workers_busy_with_a_tool_category(monkeypatch):
    fp.main(["pass-start"])
    hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "tool_use_id": "t1",
          "tool_input": {"command": "cat /root/.env"}}, monkeypatch)
    hook({"hook_event_name": "PreToolUse", "session_id": "s1", "agent_id": "a7", "agent_type": "implementer",
          "tool_name": "mcp__github__create_pr", "tool_use_id": "t2"}, monkeypatch)
    live = fp.render(fp.read_events())
    w = {x["role"]: x for x in live["workers"]}
    assert w["manager"]["state"] == "busy" and w["manager"]["tool"] == "Bash"
    assert w["implementer"]["tool"] == "MCP"
    assert "cat" not in json.dumps(live) and ".env" not in json.dumps(live) and "github__" not in json.dumps(live)
    hook({"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash", "tool_use_id": "t1"}, monkeypatch)
    w = {x["role"]: x for x in fp.render(fp.read_events())["workers"]}
    assert w["manager"]["state"] == "thinking" and w["manager"]["tool"] is None and w["manager"]["tools"] == {"Bash": 1}


def test_waiting_and_finished_workers(monkeypatch):
    hook({"hook_event_name": "Notification", "session_id": "s1", "message": "Claude needs your permission"}, monkeypatch)
    hook({"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1", "agent_type": "reviewer"}, monkeypatch)
    states = {x["role"]: x["state"] for x in fp.render(fp.read_events())["workers"]}
    assert states == {"manager": "waiting", "reviewer": "finished"}


def test_hooks_record_nothing_outside_factory_runs(monkeypatch, events_file):
    monkeypatch.delenv("LF_PROGRESS_FILE")
    monkeypatch.setattr(fp, "events_path", lambda: events_file)
    hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash"}, monkeypatch)
    assert not events_file.exists()


def test_public_text_is_scrubbed(monkeypatch):
    add(3, "impl-3", "implementer", "build", "deploy", "fail",
        "PR https://github.com/Shashkist/Learning_factory/pull/9 ssh root@207.154.193.238 failed, see " + "C:" + chr(92) + "Users" + chr(92) + "x" + chr(92) + "log and https://evil.example/a mail me@x.com "
        "key abcdefghijklmnopqrstuvwxyz0123456789ABCD", monkeypatch)
    d = fp.render(fp.read_events())["log"][0]["detail"]
    for leak in ("207.154", "C:" + chr(92) + "Users", "evil.example", "me@x.com", "abcdefghijklmnop"):
        assert leak not in d
    assert "github.com/Shashkist/Learning_factory/pull/9" in d


def test_live_json_has_schema_and_no_unknown_fields(monkeypatch):
    fp.main(["pass-start"])
    add(1, "m", "manager", "plan", "pick #1", "done", "x", monkeypatch)
    live = fp.render(fp.read_events())
    assert live["schema"] == 1 and set(live) == {"schema", "generated_at", "updated", "runner", "issues", "agents", "workers", "crew", "log"}


def test_publish_skips_unchanged_content_and_respects_the_ssh_rate_limit(monkeypatch, tmp_path):
    calls, clock = [], [1000.0]
    monkeypatch.setattr(fp.subprocess, "run", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(fp.time, "time", lambda: clock[0])
    ns = type("A", (), {"key": str(tmp_path / "k"), "host": "h"})
    fp.main(["pass-start"])
    assert fp.cmd_publish(ns) is True
    add(1, "m", "manager", "plan", "pick", "start", monkeypatch=monkeypatch)
    clock[0] += 5
    assert fp.cmd_publish(ns) is False      # changed, but within MIN_GAP_S of the last push
    clock[0] += fp.MIN_GAP_S
    assert fp.cmd_publish(ns) is True       # changed and the gap has passed
    clock[0] += fp.MIN_GAP_S
    assert fp.cmd_publish(ns) is False      # unchanged within the heartbeat
    clock[0] += fp.HEARTBEAT_S
    assert fp.cmd_publish(ns) is True       # heartbeat
    assert len(calls) == 3


def test_publish_backs_off_after_a_failed_push(monkeypatch, tmp_path):
    calls, clock = [], [1000.0]
    def boom(*a, **k):
        calls.append(1)
        raise fp.subprocess.TimeoutExpired("ssh", 30)
    monkeypatch.setattr(fp.subprocess, "run", boom)
    monkeypatch.setattr(fp.time, "time", lambda: clock[0])
    ns = type("A", (), {"key": str(tmp_path / "k"), "host": "h"})
    fp.main(["pass-start"])
    with pytest.raises(fp.subprocess.TimeoutExpired):
        fp.cmd_publish(ns)
    clock[0] += fp.BACKOFF_S - 1
    assert fp.cmd_publish(ns) is False      # retrying now would renew the firewall block
    clock[0] += 2
    with pytest.raises(fp.subprocess.TimeoutExpired):
        fp.cmd_publish(ns)
    assert len(calls) == 2


def test_publish_force_pushes_the_final_state_inside_the_gap(monkeypatch, tmp_path):
    calls, clock = [], [1000.0]
    monkeypatch.setattr(fp.subprocess, "run", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(fp.time, "time", lambda: clock[0])
    ns = type("A", (), {"key": str(tmp_path / "k"), "host": "h", "force": False, "min_age": None})
    fp.main(["pass-start"])
    assert fp.cmd_publish(ns) is True
    fp.main(["pass-end"])
    clock[0] += 3
    assert fp.cmd_publish(ns) is False      # the normal gap would drop the pass-end
    ns.force = True
    assert fp.cmd_publish(ns) is True       # the runner's final push is forced
    assert len(calls) == 2


def test_publish_min_age_is_a_heartbeat_between_passes(monkeypatch, tmp_path):
    calls, clock = [], [1000.0]
    monkeypatch.setattr(fp.subprocess, "run", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(fp.time, "time", lambda: clock[0])
    ns = type("A", (), {"key": str(tmp_path / "k"), "host": "h", "force": False, "min_age": 50.0})
    fp.main(["pass-end"])
    assert fp.cmd_publish(ns) is True
    clock[0] += 30
    assert fp.cmd_publish(ns) is False      # pushed 30 s ago (by a pass or a heartbeat): nothing to do
    clock[0] += 25
    assert fp.cmd_publish(ns) is True       # 55 s: unchanged content, still pushed so the page stays "online"
    assert len(calls) == 2


def test_publish_cli_accepts_force_and_min_age(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(fp, "cmd_publish", lambda a: seen.append((a.force, a.min_age)))
    fp.main(["publish", "--key", "k", "--host", "h", "--force"])
    fp.main(["publish", "--key", "k", "--host", "h", "--min-age", "50"])
    assert seen == [(True, None), (False, 50.0)]


def test_hook_exits_when_stdin_never_closes(tmp_path):
    env = {**os.environ, "LF_PROGRESS_FILE": str(tmp_path / "ev.jsonl"), "LF_HOOK_STDIN_TIMEOUT": "1"}
    p = subprocess.Popen([sys.executable, str(Path(fp.__file__)), "hook"], stdin=subprocess.PIPE, env=env)
    t0 = time.time()
    try:
        assert p.wait(timeout=5) == 0
    finally:
        p.kill()
        p.stdin.close()
    assert time.time() - t0 < 5
    assert not (tmp_path / "ev.jsonl").exists()


def test_read_stdin_returns_payload_and_none_on_timeout(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("abc"))
    assert _hook_io.read_stdin(1) == "abc"

    class Never:
        def read(self):
            threading.Event().wait(3)
            return ""
    monkeypatch.setattr(sys, "stdin", Never())
    assert _hook_io.read_stdin(0.2) is None


def test_throwaway_red_check_249():
    # Throwaway: proves CI runs scripts/ tests (#249). Dropped in the next commit.
    assert False
