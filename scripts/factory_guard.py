"""Factory guard: a PreToolUse hook that blocks a few actions outright in factory runs.

Owner infrastructure (D35). Loaded only through scripts/laptop/factory-hooks.json, which the laptop runner
passes to factory passes; it is referenced from the MAIN checkout, not the factory's worktree, so a pass
cannot change its own guard. Exit code 2 blocks the tool call and shows the reason to the agent.

What it blocks:
  1. Direct access to the owner's server (ssh/scp/sftp/rsync to it). Server changes go through the
     GitHub workflows in the `production` environment, which are reviewed and logged.
  2. Writing or deleting secrets and variables (`gh secret set|delete`, `gh variable set|delete`,
     PUT/DELETE on the secrets API). Secrets are installed by the owner only.
  3. Repository-level changes: `gh repo delete|edit|archive|rename`.
  4. Force pushes without a lease (`git push --force` / `-f`); `--force-with-lease` is allowed.
  5. Edits to the guard, its tests, the hooks file and the laptop runner.

Limits: this is a pattern check on the command text. It stops honest mistakes and drift, not a
determined adversary; the hard boundaries stay where they are (GitHub environment protection for
server keys, forced-command SSH key for the live view, owner-only secrets).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading

# Bounded input (O21, #230). Self-contained on purpose: the guard imports nothing a factory pass may edit.
# STDIN_TIMEOUT_S stays below the guard's hook timeout in factory-hooks.json, so a deny is seen.
# The payload is in the pipe before python starts, so a slow start does not eat into it.
STDIN_TIMEOUT_S = 3.0
WATCHDOG_S = 30.0


def read_stdin(timeout: float = STDIN_TIMEOUT_S) -> str | None:
    """All of stdin, or None if it did not close within `timeout` seconds."""
    box: list[str] = []
    t = threading.Thread(target=lambda: box.append(sys.stdin.read()), daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else None

SERVER = re.compile(r"207\.154\.193\.238|(?<![\w.-])(?:[\w-]+\.)*shashkist\.com\b")
REMOTE_TOOLS = re.compile(r"(?<![\w.-])(?:ssh|scp|sftp|rsync|plink|pscp)(?:\.exe)?(?![\w.-])")
SECRETS = re.compile(r"\bgh\s+(?:secret|variable)\s+(?:set|delete|remove|rm)\b")
SECRETS_API = re.compile(r"actions/(?:secrets|variables)")
WRITE_METHOD = re.compile(r"(?:-x|--method)\s*=?\s*['\"]?(?:put|delete|post|patch)\b")
REPO_ADMIN = re.compile(r"\bgh\s+repo\s+(?:delete|edit|archive|rename)\b")
PUSH = re.compile(r"\bgit\b[^|;&\n]*\bpush\b([^|;&\n]*)")
FORCE = re.compile(r"(?:^|\s)(?:--force(?!-with-lease)(?:\s|=|$)|-f(?:\s|$)|\+[\w/.-]+:)")
PROTECTED = ("scripts/factory_guard.py", "scripts/test_factory_guard.py", "scripts/laptop/")
WRITE_VERBS = re.compile(r"(?:>|\bsed\s+-i|\bset-content\b|\badd-content\b|\bout-file\b|\brm\b|\bdel\b|"
                         r"\bremove-item\b|\bmv\b|\bmove-item\b|\bcp\b|\bcopy-item\b|\bgit\s+(?:rm|mv|checkout|restore)\b|"
                         r"\btruncate\b|\btee\b|write_text|open\()")


def _norm(text: str) -> str:
    return text.replace("\\", "/").lower()


def check(payload: dict) -> str | None:
    """Return the reason to block this tool call, or None to allow it."""
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input") or {}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = _norm(str(inp.get("file_path") or inp.get("notebook_path") or ""))
        if any(p in path for p in PROTECTED):
            return "the factory guard, its hooks and the laptop runner are owner infrastructure (D35); open an issue instead"
        return None
    if tool not in ("Bash", "PowerShell"):
        return None
    cmd = _norm(str(inp.get("command") or ""))
    if REMOTE_TOOLS.search(cmd) and SERVER.search(cmd):
        return ("no direct access to the owner's server (D35): change it through the deploy/bootstrap "
                "workflows in the production environment")
    if SECRETS.search(cmd) or (SECRETS_API.search(cmd) and WRITE_METHOD.search(cmd)):
        return "secrets and variables are installed by the owner only (CLAUDE.md, D35): add an OWNER.md item"
    if REPO_ADMIN.search(cmd):
        return "repository settings are the owner's (D35): add an OWNER.md item"
    for m in PUSH.finditer(cmd):
        if FORCE.search(m.group(1)):
            return "no force push without a lease (D35): use --force-with-lease on your own f/ branch"
    if any(p in cmd for p in PROTECTED) and WRITE_VERBS.search(cmd):
        return "the factory guard, its hooks and the laptop runner are owner infrastructure (D35); open an issue instead"
    return None


def main() -> int:
    raw = read_stdin(float(os.environ.get("LF_GUARD_STDIN_TIMEOUT") or STDIN_TIMEOUT_S))
    if raw is None:
        # Fail closed (O21, #230): a live parent sends the payload at once, so input that never
        # arrives means a dead or broken parent, and denying costs nothing.
        print("Blocked by the factory guard: the hook input could not be read.", file=sys.stderr)
        return 2
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return 0  # complete input we do not understand: never break the run
    reason = check(payload)
    if reason:
        print(f"Blocked by the factory guard: {reason}.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    # Never outlive the caller (#230). Exit 2 here is a backstop: the hook timeout has long passed.
    watchdog = threading.Timer(WATCHDOG_S, os._exit, (2,))
    watchdog.daemon = True  # a normal exit must not wait for it
    watchdog.start()
    sys.exit(main())
