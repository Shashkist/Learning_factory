import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import factory_guard as g  # noqa: E402


def bash(cmd, tool="Bash"):
    return {"tool_name": tool, "tool_input": {"command": cmd}}


@pytest.mark.parametrize("cmd", [
    "ssh root@207.154.193.238 'docker ps'",
    "scp file.txt root@207.154.193.238:/root/",
    "ssh -o BatchMode=yes root@labs.shashkist.com uptime",
    "C:\\Windows\\System32\\OpenSSH\\ssh.exe root@207.154.193.238",
    "rsync -a dist/ root@factory.shashkist.com:/root/static/",
    "python -c \"import subprocess; subprocess.run(['ssh','root@207.154.193.238','ls'])\"",
])
def test_blocks_direct_server_access(cmd):
    assert "server" in g.check(bash(cmd))


@pytest.mark.parametrize("cmd", [
    "gh secret set LF_DATABASE_URL --env production",
    "gh secret delete SSH_PRIVATE_KEY -R Shashkist/Learning_factory",
    "gh variable set FOO --body bar",
    "gh api -X PUT repos/Shashkist/Learning_factory/actions/secrets/X",
    "gh api --method DELETE repos/o/r/environments/production/actions/secrets/X",
])
def test_blocks_secret_writes(cmd):
    assert "owner only" in g.check(bash(cmd))


@pytest.mark.parametrize("cmd", [
    "gh repo edit Shashkist/Learning_factory --visibility public",
    "gh repo delete Shashkist/Learning_factory --yes",
])
def test_blocks_repo_admin(cmd):
    assert "repository settings" in g.check(bash(cmd))


@pytest.mark.parametrize("cmd", [
    "git push --force origin main",
    "git push -f origin f/8-proxy",
    "git push origin +HEAD:main",
    "git -C ../wt push --force",
])
def test_blocks_force_push_without_lease(cmd):
    assert "force push" in g.check(bash(cmd))


@pytest.mark.parametrize("path", [
    "C:\\projects\\Learning_factory\\scripts\\factory_guard.py",
    "C:/projects/Learning_factory-factory/scripts/laptop/factory-hooks.json",
    "scripts/laptop/factory-poll.ps1",
    "scripts/test_factory_guard.py",
])
def test_blocks_edits_to_the_guard_and_runner(path):
    assert "owner infrastructure" in g.check({"tool_name": "Edit", "tool_input": {"file_path": path}})
    assert "owner infrastructure" in g.check({"tool_name": "Write", "tool_input": {"file_path": path}})


@pytest.mark.parametrize("cmd", [
    "sed -i 's/x/y/' scripts/factory_guard.py",
    "echo {} > scripts/laptop/factory-hooks.json",
    "Set-Content scripts\\laptop\\factory-poll.ps1 ''",
    "git checkout HEAD~1 -- scripts/factory_guard.py",
])
def test_blocks_shell_writes_to_the_guard(cmd):
    assert "owner infrastructure" in g.check(bash(cmd, "PowerShell" if "Set-Content" in cmd else "Bash"))


@pytest.mark.parametrize("cmd", [
    "gh pr merge 59 --squash --delete-branch",
    "git push origin HEAD:main",
    "git push --force-with-lease origin f/8-proxy",
    "gh secret list -R Shashkist/Learning_factory",
    "gh workflow run bootstrap.yml -f apply=true",
    "curl -s https://factory.shashkist.com/live.json",
    "python scripts/factory_progress.py add --issue 3 --agent manager --role manager --station ship --substep x --status done",
    "cat scripts/factory_guard.py",
    "git diff scripts/laptop/factory-poll.ps1",
    "pytest -q scripts/test_factory_guard.py",
    "ssh-keygen -lf key.pub",
    "git log -f --oneline",
])
def test_allows_normal_factory_work(cmd):
    assert g.check(bash(cmd)) is None


def test_allows_edits_elsewhere_and_other_tools():
    assert g.check({"tool_name": "Edit", "tool_input": {"file_path": "labs/manager/app/main.py"}}) is None
    assert g.check({"tool_name": "Read", "tool_input": {"file_path": "scripts/factory_guard.py"}}) is None
    assert g.check({"tool_name": "Grep", "tool_input": {"pattern": "ssh root@207.154.193.238"}}) is None


def run_cli(payload: str):
    return subprocess.run([sys.executable, str(Path(__file__).parent / "factory_guard.py")],
                          input=payload, capture_output=True, text=True)


def test_cli_exit_2_with_reason_on_block():
    r = run_cli(json.dumps(bash("gh secret set X")))
    assert r.returncode == 2 and "Blocked by the factory guard" in r.stderr


def test_cli_allows_and_survives_bad_input():
    assert run_cli(json.dumps(bash("git status"))).returncode == 0
    assert run_cli("not json").returncode == 0
    assert run_cli("").returncode == 0


def test_hooks_file_runs_the_guard_from_the_main_checkout():
    hooks = json.loads((Path(__file__).parent / "laptop" / "factory-hooks.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for e in hooks["hooks"]["PreToolUse"] for h in e["hooks"]]
    guard = [c for c in cmds if "factory_guard.py" in c]
    assert guard and all("CLAUDE_PROJECT_DIR" not in c and "Learning_factory/scripts" in c for c in guard)


def test_cli_fails_closed_when_stdin_never_closes():
    # O21, #230: a guard that cannot read its input denies instead of hanging forever.
    import os
    env = {**os.environ, "LF_HOOK_STDIN_TIMEOUT": "1"}
    p = subprocess.Popen([sys.executable, str(Path(__file__).parent / "factory_guard.py")],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        assert p.wait(timeout=5) == 2
    finally:
        p.kill()
        p.stdin.close()
    assert b"could not be read" in p.stderr.read()


def test_guard_read_bound_is_below_its_hook_timeout():
    # A deny after the hook timeout is never seen: Claude Code has already killed the hook.
    hooks = json.loads((Path(__file__).parent / "laptop" / "factory-hooks.json").read_text(encoding="utf-8"))
    guard = [h for e in hooks["hooks"]["PreToolUse"] for h in e["hooks"] if "factory_guard.py" in h["command"]]
    assert guard and all(g.STDIN_TIMEOUT_S < h["timeout"] for h in guard)


def test_guard_imports_only_the_standard_library():
    # D35: a pass cannot change its own guard, so the guard must not import a file a pass may edit.
    import ast
    tree = ast.parse((Path(__file__).parent / "factory_guard.py").read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names)
