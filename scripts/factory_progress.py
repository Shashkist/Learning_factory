"""Live progress of the factory: who is doing what, right now.

Every agent (manager, exam-writer, implementer, reviewer, researcher, scribe) reports each
sub-step as one JSON line. `render` turns the current pass's events into live.json, which the
dashboard draws as stations, agents and sub-steps. `publish` renders and pushes it to the
server with a key that can only write that one file.

  python scripts/factory_progress.py add --issue 46 --agent impl-46 --role implementer \
      --station build --substep "unit tests" --status start [--detail-file -]
  python scripts/factory_progress.py pass-start | pass-end [--result done]
  python scripts/factory_progress.py render --out live.json
  python scripts/factory_progress.py publish --key KEY --host root@HOST

Events file: $LF_PROGRESS_FILE, else ~/.lf-factory/progress.jsonl. Reporting never fails the
caller: any error prints a warning and exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATIONS = ("plan", "exam", "build", "check", "ship")
STATUSES = ("start", "done", "fail", "wait", "skip", "stopped")
ROLES = ("manager", "planner", "researcher", "exam-writer", "implementer", "reviewer", "scribe", "runner")
DETAIL_MAX = 160
LOG_TAIL = 60
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_SLUG = re.compile(r"^[\w .:/#()+\-]{1,60}$", re.UNICODE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def events_path() -> Path:
    p = os.environ.get("LF_PROGRESS_FILE")
    return Path(p) if p else Path.home() / ".lf-factory" / "progress.jsonl"


def clean(text: str, limit: int = DETAIL_MAX) -> str:
    """Untrusted text: no control characters, capped. The page renders it as text only."""
    text = _CTRL.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def append(event: dict) -> None:
    path = events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events() -> list[dict]:
    path = events_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def current_pass(events: list[dict]) -> list[dict]:
    """Events since the last pass-start (the whole file if there is none)."""
    start = 0
    for i, e in enumerate(events):
        if e.get("kind") == "pass-start":
            start = i
    return events[start:]


def render(events: list[dict]) -> dict:
    evs = current_pass(events)
    runner = {"state": "idle", "pass_started": None, "pass_ended": None, "result": None}
    agents: dict[str, dict] = {}
    issues: dict[int, dict] = {}
    for e in evs:
        kind = e.get("kind")
        if kind == "pass-start":
            runner.update(state="running", pass_started=e["at"], pass_ended=None, result=None,
                          pass_no=e.get("pass_no"), budget=e.get("budget"))
            continue
        if kind == "pass-end":
            runner.update(state="idle", pass_ended=e["at"], result=e.get("result"))
            for a in agents.values():
                if a["state"] == "running":
                    a["state"] = "ended"
            continue
        if kind == "runner-state":
            runner.update(state=e.get("state", "idle"), note=e.get("note", ""))
            continue
        if kind != "step":
            continue
        aid = e["agent"]
        a = agents.setdefault(aid, {"id": aid, "role": e["role"], "issue": e.get("issue"),
                                    "started": e["at"], "state": "running", "substeps": []})
        a["last"] = e["at"]
        a["station"] = e["station"]
        subs = a["substeps"]
        existing = next((s for s in subs if s["substep"] == e["substep"] and s["station"] == e["station"]), None)
        if existing:
            existing.update(status=e["status"], at=e["at"], detail=e.get("detail", ""))
        else:
            subs.append({"station": e["station"], "substep": e["substep"], "status": e["status"],
                         "started": e["at"], "at": e["at"], "detail": e.get("detail", "")})
        if e["status"] in ("fail", "stopped"):
            a["state"] = "failed" if e["status"] == "fail" else "stopped"
        elif e["substep"] == "finished" and e["status"] == "done":
            a["state"] = "done"
        elif a["state"] not in ("failed", "stopped"):
            a["state"] = "running"
        if e.get("issue") is not None:
            i = issues.setdefault(e["issue"], {"issue": e["issue"], "title": e.get("title", ""), "station": e["station"]})
            i["station"] = e["station"]
            if e.get("title"):
                i["title"] = e["title"]
    log = [{k: e.get(k) for k in ("at", "issue", "agent", "role", "station", "substep", "status", "detail")}
           for e in evs if e.get("kind") == "step"][-LOG_TAIL:]
    return {"updated": now(), "runner": runner, "issues": list(issues.values()),
            "agents": list(agents.values()), "log": log}


def cmd_add(a: argparse.Namespace) -> None:
    if a.station not in STATIONS or a.status not in STATUSES or a.role not in ROLES:
        raise ValueError(f"station must be {STATIONS}, status {STATUSES}, role {ROLES}")
    if not _SLUG.match(a.substep) or not _SLUG.match(a.agent):
        raise ValueError("agent and substep: up to 60 letters, digits, space and . : / # ( ) + -")
    detail = sys.stdin.read() if a.detail_file == "-" else ""
    append({"kind": "step", "at": now(), "issue": a.issue, "title": clean(a.title or "", 90),
            "agent": a.agent, "role": a.role, "station": a.station, "substep": a.substep,
            "status": a.status, "detail": clean(detail)})


def cmd_render(a: argparse.Namespace) -> Path:
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(render(read_events()), ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def cmd_publish(a: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory() as d:
        a.out = str(Path(d) / "live.json")
        data = cmd_render(a).read_bytes()
        # The server key has a forced command that writes stdin to live.json and nothing else.
        subprocess.run(["ssh", "-i", a.key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                        "-o", "ConnectTimeout=10", a.host], input=data, check=True, timeout=30,
                       capture_output=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("add")
    ad.add_argument("--issue", type=int)
    ad.add_argument("--title", default="")
    ad.add_argument("--agent", required=True)
    ad.add_argument("--role", required=True)
    ad.add_argument("--station", required=True)
    ad.add_argument("--substep", required=True)
    ad.add_argument("--status", required=True)
    ad.add_argument("--detail-file")
    ps = sub.add_parser("pass-start")
    ps.add_argument("--pass-no", type=int)
    ps.add_argument("--budget", type=int)
    rs = sub.add_parser("runner-state")
    rs.add_argument("--state", required=True, choices=["idle", "paused", "budget", "running"])
    rs.add_argument("--note", default="")
    pe = sub.add_parser("pass-end")
    pe.add_argument("--result", default="done")
    rn = sub.add_parser("render")
    rn.add_argument("--out", required=True)
    pb = sub.add_parser("publish")
    pb.add_argument("--key", required=True)
    pb.add_argument("--host", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "add":
            cmd_add(a)
        elif a.cmd == "pass-start":
            append({"kind": "pass-start", "at": now(), "pass_no": a.pass_no, "budget": a.budget})
        elif a.cmd == "runner-state":
            append({"kind": "runner-state", "at": now(), "state": a.state, "note": clean(a.note, 120)})
        elif a.cmd == "pass-end":
            append({"kind": "pass-end", "at": now(), "result": clean(a.result, 40)})
        elif a.cmd == "render":
            cmd_render(a)
        elif a.cmd == "publish":
            cmd_publish(a)
    except Exception as exc:  # reporting must never break the work
        print(f"factory_progress: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
