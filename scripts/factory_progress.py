"""Live progress of the factory: who is doing what, right now.

Two event sources, both written by the factory itself (never read from Claude Code's transcripts):
  * steps: the manager and every agent report their sub-steps (`add`), in factory words;
  * hooks: Claude Code hooks (PreToolUse, PostToolUse, Notification, SubagentStop, Stop,
    SessionStart) loaded only for factory runs (`--settings scripts/laptop/factory-hooks.json`)
    record which worker is busy with which kind of tool (`hook`). Hooks record only allowlisted
    fields: no tool input, no text from prompts, titles, paths or branches.

`render` builds live.json from an explicit public schema (the publish step is the redaction
boundary), and `publish` pushes it with a key that can only write that one file.

  python scripts/factory_progress.py add --issue 46 --agent implementer-46 --role implementer \
      --station build --substep "unit tests" --status start [--detail-file -]
  python scripts/factory_progress.py hook < hook-event.json        (from Claude Code hooks)
  python scripts/factory_progress.py pass-start | pass-end | runner-state --state idle
  python scripts/factory_progress.py render --out live.json
  python scripts/factory_progress.py publish --key KEY --host root@HOST

Events file: $LF_PROGRESS_FILE, else ~/.lf-factory/progress.jsonl. `hook` does nothing unless
$LF_PROGRESS_FILE is set (only factory runs set it). Nothing here ever fails the caller.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = 1
STATIONS = ("plan", "exam", "build", "check", "ship")
STATUSES = ("start", "done", "fail", "wait", "skip", "stopped")
ROLES = ("manager", "planner", "researcher", "exam-writer", "implementer", "reviewer", "scribe", "runner")
WORKER_ROLES = ("manager", "researcher", "exam-writer", "implementer", "reviewer", "scribe")
TOOL_CATEGORIES = {"Bash": "Bash", "PowerShell": "Bash", "Read": "Read", "Edit": "Edit", "Write": "Write",
                   "MultiEdit": "Edit", "NotebookEdit": "Edit", "Grep": "Search", "Glob": "Search",
                   "Agent": "Agent", "Task": "Agent", "WebSearch": "Web", "WebFetch": "Web", "Skill": "Skill",
                   "TodoWrite": "Plan"}
THIS_REPO = "Shashkist/Learning_factory"   # events without a repository belong here
# Repositories whose names may appear in the public live.json (compared case-insensitively). #544/repos.yaml can feed this later.
ALLOWED_REPOS = {r.lower(): r for r in (THIS_REPO, "Shashkist/factory")}


def canon_repo(repo: object) -> str | None:
    """"" for this repository (or none given), the canonical name of another allowed one, None for anything else."""
    if not repo:
        return ""
    name = ALLOWED_REPOS.get(str(repo).lower())
    if name is None:
        return None
    return "" if name == THIS_REPO else name
DETAIL_MAX = 160
LOG_TAIL = 60
ABANDONED = timedelta(minutes=15)   # a tool with no PostToolUse after this long is not shown as running
HEARTBEAT_S = 60                    # publish at least this often even when nothing changed
# The server's ufw LIMIT rule rejects a 7th new SSH connection within 30 s from one address, and every
# rejected attempt renews the block. Keep the publisher to 2 connections per 30 s and back off on failure.
MIN_GAP_S = 15                      # at most one push per 15 s, even when the content keeps changing
BACKOFF_S = 90                      # after a failed push, wait this long before trying again
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_SLUG = re.compile(r"^[\w .:/#()+\-]{1,60}$", re.UNICODE)
_SCRUB = [
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"https?://(?!github\.com/Shashkist/Learning_factory(?:[/?#\s]|$))(?!github\.com/Shashkist/factory/)(?!(?:[\w-]+\.)*shashkist\.com(?::\d+)?(?:[/?#\s]|$))\S+", re.IGNORECASE), "<url>"),
    (re.compile(r"(?:https?://|\bgithub\.com/)\S*(?:/\.\.|%2e|%5c|%2f|\\)\S*", re.IGNORECASE), "<url>"),
    (re.compile(r"\bgithub\.com/(?!Shashkist/Learning_factory(?:[/?#\s]|$))(?!Shashkist/factory/)\S+", re.IGNORECASE), "<url>"),
    (re.compile(r"(?:[A-Za-z]:\\|/(?:root|home|Users|etc|var|tmp|c)/)\S*"), "<path>"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "<redacted>"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def issue_no(v: object) -> int:
    """A positive issue number, or 0 for anything else ("0", None, text)."""
    try:
        n = int(v or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def events_path() -> Path:
    p = os.environ.get("LF_PROGRESS_FILE")
    return Path(p) if p else Path.home() / ".lf-factory" / "progress.jsonl"


def clean(text: str, limit: int = DETAIL_MAX) -> str:
    """Untrusted text: no control characters, capped. The page renders it as text only."""
    text = _CTRL.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def scrub(text: str) -> str:
    """Public text: hosts, emails, foreign URLs, absolute paths and key-like strings removed."""
    for rx, repl in _SCRUB:
        text = rx.sub(repl, text)
    return text


def short_id(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:8] if raw else "-"


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


# ---- hooks -> worker events (allowlisted fields only) -------------------------------------------

def hook_event(payload: dict) -> dict | None:
    name = payload.get("hook_event_name", "")
    agent_type = payload.get("agent_type") or ""
    role = agent_type if agent_type in WORKER_ROLES else ("agent" if agent_type else "manager")
    worker = short_id(payload.get("agent_id") or payload.get("session_id") or "")
    base = {"kind": "worker", "at": now(), "worker": worker, "role": role}
    if name in ("PreToolUse", "PostToolUse"):
        tool = payload.get("tool_name", "")
        cat = TOOL_CATEGORIES.get(tool, "MCP" if tool.startswith("mcp__") else "Other")
        return {**base, "event": "tool-start" if name == "PreToolUse" else "tool-end", "tool": cat,
                "call": short_id(payload.get("tool_use_id", ""))}
    if name == "Notification":
        return {**base, "event": "waiting"}
    if name in ("SubagentStop", "Stop"):
        return {**base, "event": "stopped" if name == "SubagentStop" else "idle"}
    if name == "SessionStart":
        return {**base, "event": "session-start"}
    return None


def render_workers(evs: list[dict], at: datetime) -> list[dict]:
    workers: dict[str, dict] = {}
    for e in evs:
        if e.get("kind") != "worker":
            continue
        w = workers.setdefault(e["worker"], {"id": e["worker"], "role": e["role"], "state": "idle", "tool": None,
                                             "tools": {}, "started": e["at"], "last": e["at"], "_open": {}})
        w["last"] = e["at"]
        if w["role"] in ("manager", "agent") and e["role"] not in ("manager", "agent"):
            w["role"] = e["role"]
        ev = e["event"]
        if ev == "tool-start":
            w["_open"][e.get("call") or e["at"]] = (e["tool"], e["at"])
            w["tools"][e["tool"]] = w["tools"].get(e["tool"], 0) + 1
            w["state"] = "busy"
        elif ev == "tool-end":
            w["_open"].pop(e.get("call"), None) if e.get("call") in w["_open"] else (w["_open"].popitem() if w["_open"] else None)
            w["state"] = "busy" if w["_open"] else "thinking"
        elif ev == "waiting":
            w["state"] = "waiting"
        elif ev == "stopped":
            w["state"], w["_open"] = "finished", {}
        elif ev == "idle":
            w["state"], w["_open"] = "idle", {}
    out = []
    for w in workers.values():
        live = {k: v for k, v in w["_open"].items() if at - parse(v[1]) < ABANDONED}
        w["tool"] = next(iter(live.values()))[0] if live else None
        if w["state"] == "busy" and not live:
            w["state"] = "thinking"
        del w["_open"]
        out.append(w)
    return out


# ---- render: the public schema ------------------------------------------------------------------

def render_crew(at: datetime) -> list[dict]:
    """Agents and subagents from the transcripts (scripts/factory_crew.py). Off unless LF_CREW_DIR is set
    (the laptop runner sets it), so tests and other machines never read anyone's transcripts."""
    d = os.environ.get("LF_CREW_DIR")
    if not d:
        return []
    try:
        import factory_crew
        pf = os.environ.get("LF_PROGRESS_FILE")
        return factory_crew.build(Path(d), at, scrub, Path(pf + ".crew-cache.json") if pf else None)
    except Exception:  # noqa: BLE001 - the crew view must never break the publisher
        return []


def render(events: list[dict]) -> dict:
    # the redaction boundary: step events of a repository that is not on the allowlist are dropped, names normalised
    evs = [{**e, "repo": canon_repo(e.get("repo"))} if e.get("kind") == "step" else e
           for e in current_pass(events) if e.get("kind") != "step" or canon_repo(e.get("repo")) is not None]
    t = now()
    runner = {"state": "idle", "pass_no": None, "budget": None, "pass_started": None, "pass_ended": None,
              "result": None, "note": ""}
    agents: dict[str, dict] = {}
    prev_issue: dict[str, tuple[str, int] | None] = {}   # per agent: (repository, issue) of its previous sub-step
    issues: dict[tuple[str, int], dict] = {}
    for e in evs:
        kind = e.get("kind")
        if kind == "pass-start":
            runner.update(state="running", pass_started=e["at"], pass_ended=None, result=None,
                          pass_no=e.get("pass_no"), budget=e.get("budget"), note="")
        elif kind == "pass-end":
            runner.update(state="idle", pass_ended=e["at"], result=e.get("result"))
            for a in agents.values():
                if a["state"] == "running":
                    a["state"] = "ended"
        elif kind == "runner-state":
            runner.update(state=e.get("state", "idle"), note=e.get("note", ""))
        elif kind == "step":
            aid = e["agent"]
            a = agents.setdefault(aid, {"id": aid, "role": e["role"], "issue": issue_no(e.get("issue")) or None, "station": e["station"],
                                        "state": "running", "started": e["at"], "last": e["at"], "substeps": [],
                                        "repo": e.get("repo") or ""})
            a["last"], a["station"] = e["at"], e["station"]
            own = issue_no(e.get("issue"))
            step = (e.get("repo") or "", own) if own else (prev_issue.get(aid) or ((a["repo"], a["issue"]) if a["issue"] else None))   # unscoped: the previous sub-step's issue
            if not own and e["status"] in ("done", "fail"):
                # #527: an unscoped done/fail closes the agent's latest open step of the same station + sub-step, in any issue
                open_ = [s for s in a["substeps"] if s["substep"] == e["substep"] and s["station"] == e["station"]
                         and s["status"] in ("start", "wait") and s.get("issue")]
                if open_:
                    m = max(open_, key=lambda s: s["at"])
                    step = (m["repo"], m["issue"])
            prev_issue[aid] = step
            step_repo, step_issue = step or ("", None)
            sub = next((s for s in a["substeps"] if s["substep"] == e["substep"] and s["station"] == e["station"]
                        and s.get("issue") == step_issue and s.get("repo") == step_repo), None)
            if sub:
                sub.update(status=e["status"], at=e["at"], detail=e.get("detail", ""))
            else:
                a["substeps"].append({"station": e["station"], "substep": e["substep"], "status": e["status"], "issue": step_issue, "repo": step_repo,
                                      "started": e["at"], "at": e["at"], "detail": e.get("detail", "")})
            if e["status"] in ("fail", "stopped"):
                a["state"] = "failed" if e["status"] == "fail" else "stopped"
            elif e["substep"] == "finished" and e["status"] == "done":
                a["state"] = "done"
            elif a["state"] != "stopped":
                a["state"] = "running"   # a later start/done recovers from a failure
            if step_issue:
                i = issues.setdefault(step, {"issue": step_issue, "repo": step_repo, "title": "", "station": e["station"]})
                i["station"] = e["station"]
                if e.get("title"):
                    i["title"] = e["title"]
    log = [e for e in evs if e.get("kind") == "step"][-LOG_TAIL:]
    # Public schema, field by field: nothing reaches live.json unless it is named here.
    return {
        "schema": SCHEMA,
        "generated_at": t,
        "updated": t,
        "runner": {k: runner[k] for k in ("state", "pass_no", "budget", "pass_started", "pass_ended", "result", "note")},
        "issues": [{"issue": i["issue"], "repo": i["repo"], "title": scrub(i["title"]), "station": i["station"]} for i in issues.values()],
        "agents": [{"id": scrub(a["id"]), "role": a["role"], "issue": a["issue"], "repo": a["repo"], "station": a["station"], "state": a["state"],
                    "started": a["started"], "last": a["last"],
                    "substeps": [{"station": s["station"], "substep": scrub(s["substep"]), "status": s["status"],
                                  "issue": s["issue"], "repo": s["repo"], "started": s["started"], "at": s["at"], "detail": scrub(s["detail"])}
                                 for s in a["substeps"]]} for a in agents.values()],
        "workers": render_workers(
            [e for e in evs if not runner["pass_started"] or parse(e["at"]) >= parse(runner["pass_started"])], parse(t)),
        "crew": render_crew(parse(t)),
        "log": [{"at": e["at"], "issue": e.get("issue"), "repo": e.get("repo") or "", "agent": scrub(e["agent"]), "role": e["role"],
                 "station": e["station"], "substep": scrub(e["substep"]), "status": e["status"],
                 "detail": scrub(e.get("detail", ""))} for e in log],
    }


# ---- commands -----------------------------------------------------------------------------------

def cmd_add(a: argparse.Namespace) -> None:
    if a.station not in STATIONS or a.status not in STATUSES or a.role not in ROLES:
        raise ValueError(f"station must be {STATIONS}, status {STATUSES}, role {ROLES}")
    if not _SLUG.match(a.substep) or not _SLUG.match(a.agent):
        raise ValueError("agent and substep: up to 60 letters, digits, space and . : / # ( ) + -")
    repo = canon_repo(a.repo)
    if repo is None:
        raise ValueError("repo: this repository or Shashkist/factory")
    detail = sys.stdin.read() if a.detail_file == "-" else ""
    append({"kind": "step", "at": now(), "issue": a.issue, "repo": repo, "title": clean(a.title or "", 90),
            "agent": a.agent, "role": a.role, "station": a.station, "substep": a.substep,
            "status": a.status, "detail": clean(detail)})


def _hook_io() -> types.ModuleType:
    """The hook-only helpers, imported lazily: some callers load this file by path (#51 exam),
    so scripts/ is not on sys.path for them."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import _hook_io

    return _hook_io


def cmd_hook() -> None:
    if not os.environ.get("LF_PROGRESS_FILE"):
        return  # not a factory run: record nothing
    raw = _hook_io().read_stdin()
    if raw is None:
        return  # stdin never closed (parent gone): record nothing, never fail the caller
    ev = hook_event(json.loads(raw or "{}"))
    if ev:
        append(ev)


def cmd_render(a: argparse.Namespace) -> Path:
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(render(read_events()), ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def content_hash(live: dict) -> str:
    body = {k: v for k, v in live.items() if k not in ("generated_at", "updated")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def cmd_publish(a: argparse.Namespace) -> bool:
    """Push live.json when its content changed (at most every MIN_GAP_S), or every HEARTBEAT_S as a
    liveness signal; after a failure wait BACKOFF_S. State file: '<hash> <time>' or 'fail <time>'.
    --force pushes now (the final state of a pass must not be dropped by the gap); --min-age N is the
    between-passes heartbeat: push iff the last push is at least N s old, whatever the content."""
    live = render(read_events())
    state = Path(a.key + ".last")
    h = content_hash(live)
    now = time.time()
    try:
        last_h, last_t = state.read_text().split()
        age = now - float(last_t)
        force, min_age = getattr(a, "force", False), getattr(a, "min_age", None)
        if last_h == "fail" and age < BACKOFF_S and not force:
            return False
        if last_h != "fail" and not force:
            if min_age is not None:
                if age < min_age:
                    return False
            elif age < MIN_GAP_S or (last_h == h and age < HEARTBEAT_S):
                return False
    except (OSError, ValueError):
        pass
    data = json.dumps(live, ensure_ascii=False).encode("utf-8")
    # The server key has a forced command that writes stdin to live.json (tmp + rename) and nothing else.
    try:
        subprocess.run(["ssh", "-i", a.key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                        "-o", "ConnectTimeout=10", a.host], input=data, check=True, timeout=30, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        state.write_text(f"fail {now}")
        raise
    state.write_text(f"{h} {now}")
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("add")
    for flag in ("--agent", "--role", "--station", "--substep", "--status"):
        ad.add_argument(flag, required=True)
    ad.add_argument("--issue", type=int)
    ad.add_argument("--repo", default="")   # owner/name; default: this repository
    ad.add_argument("--title", default="")
    ad.add_argument("--detail-file")
    sub.add_parser("hook")
    ps = sub.add_parser("pass-start")
    ps.add_argument("--pass-no", type=int)
    ps.add_argument("--budget", type=int)
    pe = sub.add_parser("pass-end")
    pe.add_argument("--result", default="done")
    rs = sub.add_parser("runner-state")
    rs.add_argument("--state", required=True, choices=["idle", "paused", "budget", "running"])
    rs.add_argument("--note", default="")
    rn = sub.add_parser("render")
    rn.add_argument("--out", required=True)
    pb = sub.add_parser("publish")
    pb.add_argument("--key", required=True)
    pb.add_argument("--host", required=True)
    pb.add_argument("--force", action="store_true")
    pb.add_argument("--min-age", type=float)
    try:
        a = p.parse_args(argv)
        if a.cmd == "add":
            cmd_add(a)
        elif a.cmd == "hook":
            cmd_hook()
        elif a.cmd == "pass-start":
            append({"kind": "pass-start", "at": now(), "pass_no": a.pass_no, "budget": a.budget})
        elif a.cmd == "pass-end":
            append({"kind": "pass-end", "at": now(), "result": clean(a.result, 40)})
        elif a.cmd == "runner-state":
            append({"kind": "runner-state", "at": now(), "state": a.state, "note": clean(a.note, 120)})
        elif a.cmd == "render":
            cmd_render(a)
        elif a.cmd == "publish":
            cmd_publish(a)
    except SystemExit:
        pass  # argparse errors: still never fail the caller
    except Exception as exc:
        print(f"factory_progress: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["hook"]:
        _hook_io().start_watchdog(0)  # a hook must never outlive its caller; exiting 0 never blocks it
    sys.exit(main())
