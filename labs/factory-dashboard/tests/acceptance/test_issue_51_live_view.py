"""Exam for issue #51: the Live section of the factory dashboard.

Only the cases that do not need the page stay (D149, #571): the shape of the live.json the real
scripts/factory_progress.py render() publishes, and the nginx location for live.json. The page cases
(index.html, app.js, styles.css, run in node) went with the page to the factory package.
The CONTRACT below is kept as the record of the original exam.

CONTRACT (the builder works from this; nothing else about the markup is tested)

index.html
  * `<section id="live">` is the FIRST <section> inside <main>, with a heading whose text is "Live".
  * the page's own JS fills it; anything that must be invisible uses the `hidden` ATTRIBUTE
    (a class alone does not count). No inline `style` attribute (the CSP is default-src 'self'),
    no 'unsafe-inline' added to the CSP.
app.js
  * keeps fetching snapshot.json; fetches live.json with `cache: "no-store"` and a `?t=<number>`
    cache-buster, once at load and then every 3000 ms (setInterval or chained setTimeout);
    the buster differs between polls.
  * no innerHTML / outerHTML / insertAdjacentHTML / document.write anywhere (text via textContent).
  * a response that is empty, not JSON, not an object with `schema: 1`, an HTTP error or a network
    error leaves the rendered Live section byte-identical; the next good response replaces it.
  * "laptop offline or asleep" is in VISIBLE text (no `hidden` ancestor) iff
    now - generated_at > 2 minutes, re-evaluated on every poll, also when the poll fails.
Inside #live, after the first good response:
  * runner bar: visible text names the state (running | idle | paused | budget), for running also
    "pass 3" and the budget "8" (case-insensitive, e.g. "Pass 3 of 8"), and the elapsed time since
    pass_started as "<n> min" (e.g. "10 min").
  * five `[data-live-station]` elements, in this order, values plan exam build check ship; each has
    its name as visible text (Plan, Exam, ...). The issues of live.json `issues` ("#<n>" and
    title) and the agent cards sit inside the element of their station.
  * agent card: `[data-agent-id="<id>"]` with `data-state="<agent state>"`; its text has the role,
    "#<issue>", the issue title, the elapsed time since `started` ("<n> min"), and the progress
    "<done>/<total>" over its substeps. `[data-current-substep]` inside it holds the name of the
    current sub-step (the latest one that is start or wait, else the last). Sub-step list:
    one `[data-substep="<name>"]` per sub-step with `data-status="<status>"`; its text holds the
    icon: start hourglass, done check mark, fail cross, wait pause, skip skip-forward, stopped stop.
    Finished agents stay (data-state="done").
  * worker card: `[data-worker-id="<id>"]` with `data-state="<busy|thinking|waiting|idle|finished>"`;
    text has the role, the current tool category when there is one, and every key of `tools` with
    its count as text (the sparkline may be an SVG with <title>s). `styles.css` has a @keyframes rule
    and mentions `busy` (the pulsing dot).
  * log: one `[data-log-row]` per event, newest first, at most 60; the text has agent, station,
    sub-step and detail, in that order.
  * no "undefined", "null" or "NaN" in the visible text, for any valid live.json (also without
    workers, or with all lists empty).
styles.css
  * rules whose selector mentions `live` use the design tokens (var(--...)), no colour literals.
nginx (labs/deploy/nginx/factory.conf, installed by install-block.sh)
  * `location = /live.json` with `Cache-Control "no-cache"` and the X-Robots-Tag header
    (add_header in a location replaces the server-level ones).
"""
# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
COMPONENT = HERE.parents[1]
REPO = COMPONENT.parents[1]
NGINX_CONF = REPO / "labs" / "deploy" / "nginx" / "factory.conf"
INSTALLER = REPO / "labs" / "deploy" / "nginx" / "install-block.sh"

NOW = datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def ago(minutes: float = 0, seconds: float = 0) -> str:
    return iso(NOW - timedelta(minutes=minutes, seconds=seconds))


@pytest.fixture(scope="module", autouse=True)
def _no_crew_dir():
    """The laptop's runner sets LF_CREW_DIR (crew view from local transcripts); the exam must not read it."""
    mp = pytest.MonkeyPatch()
    mp.delenv("LF_CREW_DIR", raising=False)
    yield
    mp.undo()


# ---- fixtures from the real renderer -----------------------------------------------------------

def _load_progress():
    spec = importlib.util.spec_from_file_location("factory_progress_51", REPO / "scripts" / "factory_progress.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def step(agent, role, issue, title, station, sub, status, at, detail=""):
    return {"kind": "step", "at": at, "issue": issue, "title": title, "agent": agent, "role": role,
            "station": station, "substep": sub, "status": status, "detail": detail}


def worker_events():
    ev = []
    for i in range(12):  # twelve Bash calls, the last one still open
        ev.append({"kind": "worker", "at": ago(9, 50 - i), "worker": "w-mgr", "role": "manager",
                   "event": "tool-start", "tool": "Bash", "call": f"c{i}"})
        if i < 11:
            ev.append({"kind": "worker", "at": ago(9, 49 - i), "worker": "w-mgr", "role": "manager",
                       "event": "tool-end", "tool": "Bash", "call": f"c{i}"})
    for i in range(3):
        ev.append({"kind": "worker", "at": ago(3, 30 - i), "worker": "w-mgr", "role": "manager",
                   "event": "tool-start", "tool": "Read", "call": f"r{i}"})
        ev.append({"kind": "worker", "at": ago(3, 29 - i), "worker": "w-mgr", "role": "manager",
                   "event": "tool-end", "tool": "Read", "call": f"r{i}"})
    ev.append({"kind": "worker", "at": ago(2), "worker": "w-mgr", "role": "manager",
               "event": "tool-start", "tool": "Bash", "call": "last"})
    ev += [
        {"kind": "worker", "at": ago(2), "worker": "w-exam", "role": "exam-writer", "event": "tool-start",
         "tool": "Edit", "call": "e1"},
        {"kind": "worker", "at": ago(1), "worker": "w-exam", "role": "exam-writer", "event": "tool-end",
         "tool": "Edit", "call": "e1"},
        {"kind": "worker", "at": ago(1), "worker": "w-impl", "role": "implementer", "event": "waiting"},
        {"kind": "worker", "at": ago(1), "worker": "w-rev", "role": "reviewer", "event": "stopped"},
    ]
    return ev


def build_live(log_steps: int = 0, hostile: bool = False) -> dict:
    fp = _load_progress()
    fp.now = lambda: iso(NOW)  # render() stamps generated_at with this
    t51 = "<script>window.pwned=1</script>" if hostile else "Dashboard: live view"
    sub = "<marquee>tests</marquee>" if hostile else "tests"
    det = '<img src=x onerror="window.pwned=1">' if hostile else "writing the exam"
    ev = [{"kind": "pass-start", "at": ago(10), "pass_no": 3, "budget": 8}]
    ev += [
        step("exam-writer-51", "exam-writer", 51, t51, "exam", "read issue", "start", ago(8)),
        step("exam-writer-51", "exam-writer", 51, t51, "exam", "read issue", "done", ago(7)),
        step("exam-writer-51", "exam-writer", 51, t51, "exam", "fixtures", "start", ago(6)),
        step("exam-writer-51", "exam-writer", 51, t51, "exam", "fixtures", "done", ago(5)),
        step("exam-writer-51", "exam-writer", 51, t51, "exam", sub, "start", ago(4), det),
    ]
    for s_, st in [("one", "start"), ("two", "done"), ("three", "fail"), ("four", "wait"),
                   ("five", "skip"), ("six", "stopped")]:
        ev.append(step("implementer-50", "implementer", 50, "Bootstrap safety", "build", s_, st, ago(3)))
    ev += [
        step("reviewer-49", "reviewer", 49, "Session hardening", "check", "review", "done", ago(2)),
        step("reviewer-49", "reviewer", 49, "Session hardening", "check", "finished", "done", ago(1)),
    ]
    for i in range(log_steps):
        ev.append(step("manager", "manager", None, "", "plan", f"tick {i:03d}", "done", ago(0, 50 - i % 50),
                       f"event-{i + 1:03d}"))
    ev += worker_events()
    return fp.render(ev)


# ---- tree helpers (nodes are {t, a, c} or str) -------------------------------------------------

def text(n) -> str:
    return n if isinstance(n, str) else "".join(text(c) for c in n["c"])


def vtext(n) -> str:
    """Text a person can see: nothing under a `hidden` attribute."""
    if isinstance(n, str):
        return n
    if "hidden" in n["a"]:
        return ""
    return "".join(vtext(c) for c in n["c"])


def walk(n):
    if isinstance(n, str):
        return
    yield n
    for c in n["c"]:
        yield from walk(c)


@pytest.fixture(scope="module")
def good_live() -> dict:
    return build_live(log_steps=0)


# ---- fixtures are sane (the exam cannot pass on bad data) --------------------------------------

def test_fixture_has_the_published_shape(good_live):
    assert good_live["schema"] == 1 and good_live["generated_at"] == iso(NOW)
    assert good_live["runner"]["state"] == "running" and good_live["runner"]["pass_no"] == 3
    assert {a["id"]: a["state"] for a in good_live["agents"]} == {
        "exam-writer-51": "running", "implementer-50": "stopped", "reviewer-49": "done"}
    assert {w["id"]: w["state"] for w in good_live["workers"]} == {
        "w-mgr": "busy", "w-exam": "thinking", "w-impl": "waiting", "w-rev": "finished"}
    mgr = next(w for w in good_live["workers"] if w["id"] == "w-mgr")
    assert mgr["tools"] == {"Bash": 13, "Read": 3} and mgr["tool"] == "Bash"


# ---- nginx -------------------------------------------------------------------------------------

def _location(conf: str, path: str) -> str:
    m = re.search(r"location\s*=\s*" + re.escape(path) + r"\s*\{([^}]*)\}", conf)
    assert m, f"no `location = {path}` block"
    return m.group(1)


def test_factory_block_serves_live_json_with_no_cache():
    body = _location(NGINX_CONF.read_text(encoding="utf-8"), "/live.json")
    assert re.search(r'add_header\s+Cache-Control\s+"no-cache"\s+always\s*;', body)
    assert re.search(r'add_header\s+X-Robots-Tag\s+"noindex"\s+always\s*;', body)


def test_snapshot_json_keeps_its_no_cache_location():
    body = _location(NGINX_CONF.read_text(encoding="utf-8"), "/snapshot.json")
    assert "no-cache" in body


def test_installer_puts_the_live_json_location_into_the_shared_conf(tmp_path):
    bash = shutil.which("bash")
    assert bash
    docker = tmp_path / "fake-docker"
    docker.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    other = "server {\n    server_name other.example.org;\n}\n"
    old = "# BEGIN learning-factory:factory\nserver { server_name factory.example.org; }\n# END learning-factory:factory\n"
    conf = tmp_path / "default.conf"
    conf.write_text(other + old, encoding="utf-8", newline="\n")
    env = {k: v for k, v in os.environ.items()}
    env.update(LF_NGINX_CONF=str(conf), LF_DOCKER=str(docker))
    p = subprocess.run([bash, str(INSTALLER), "factory", str(NGINX_CONF)],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    out = conf.read_text(encoding="utf-8")
    assert out.startswith(other), "the other site's block must stay byte-identical"
    block = out[out.index("# BEGIN learning-factory:factory"):]
    assert "location = /live.json" in block
    assert re.search(r'Cache-Control\s+"no-cache"', _location(block, "/live.json"))
