"""Edge cases of labs/deploy/nginx/install-block.sh (review of #24): fake docker, temp files."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
INSTALLER = ROOT / "labs" / "deploy" / "nginx" / "install-block.sh"
BASH = shutil.which("bash")

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
if [ -n "${FAKE_FAIL_T:-}" ] && [[ "$*" == *"nginx -t"* ]]; then exit 1; fi
if [ -n "${FAKE_FAIL_RELOAD:-}" ] && [[ "$*" == *"reload"* ]]; then exit 1; fi
exit 0
"""

OTHER = "server {\n    server_name other.example.org;\n}\n"
TAIL = "server {\n    server_name n8n.example.org;\n}\n"


def blk(name: str, body: str) -> str:
    return f"# BEGIN learning-factory:{name}\n{body}\n# END learning-factory:{name}\n"


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    assert BASH
    docker = tmp_path / "fake-docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "conf.d").mkdir()
    return {"tmp": tmp_path, "conf": tmp_path / "conf.d" / "default.conf",
            "log": tmp_path / "docker.log", "docker": docker}


def run(env: dict[str, Path], conf: bytes | None, block: bytes, path: str = "", **flags: str):
    if conf is not None:
        env["conf"].write_bytes(conf)
    f = env["tmp"] / "block.conf"
    f.write_bytes(block)
    e = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_")}
    e.update(LF_NGINX_CONF=str(env["conf"]), LF_DOCKER=str(env["docker"]),
             FAKE_LOG=str(env["log"]), **flags)
    if path:
        e["PATH"] = path + os.pathsep + e["PATH"]
    assert BASH
    return subprocess.run([BASH, str(INSTALLER), "factory", str(f)],
                          capture_output=True, text=True, env=e)


def calls(env: dict[str, Path]) -> list[str]:
    return env["log"].read_text().splitlines() if env["log"].exists() else []


def backups(env: dict[str, Path]) -> list[Path]:
    return sorted(env["conf"].parent.glob("default.conf.*.bak"))


def test_end_before_begin_is_refused_and_nothing_changes(env):
    conf = (OTHER + "# END learning-factory:factory\n" + TAIL
            + "# BEGIN learning-factory:factory\nold\n").encode()
    p = run(env, conf, blk("factory", "new").encode())
    assert p.returncode != 0
    assert env["conf"].read_bytes() == conf
    assert calls(env) == []


@pytest.mark.parametrize("marker", ["# BEGIN learning-factory:factory \n",
                                    "  # BEGIN learning-factory:factory\n",
                                    "# BEGIN learning-factory:factory\r\n"])
def test_loose_markers_are_refused_not_duplicated(env, marker):
    conf = (OTHER + marker + "old\n# END learning-factory:factory\n" + TAIL).encode()
    p = run(env, conf, blk("factory", "new").encode())
    assert p.returncode != 0
    assert env["conf"].read_bytes() == conf


def test_unchanged_block_neither_backs_up_nor_reloads(env):
    conf = (OTHER + blk("factory", "same") + TAIL).encode()
    p = run(env, conf, blk("factory", "same").encode())
    assert p.returncode == 0, p.stderr
    assert calls(env) == [] and backups(env) == []
    assert env["conf"].read_bytes() == conf


def test_block_without_final_newline_is_idempotent(env):
    block = blk("factory", "x").rstrip("\n").encode()
    assert run(env, OTHER.encode(), block).returncode == 0
    first = env["conf"].read_bytes()
    assert run(env, None, block).returncode == 0
    assert env["conf"].read_bytes() == first
    assert first.endswith(b"# END learning-factory:factory\n")


def test_failed_reload_keeps_the_valid_config_and_says_so(env):
    conf = (OTHER + blk("factory", "old") + TAIL).encode()
    p = run(env, conf, blk("factory", "new").encode(), FAKE_FAIL_RELOAD="1")
    assert p.returncode != 0
    assert "reload failed" in p.stderr
    assert env["conf"].read_bytes() == (OTHER + blk("factory", "new") + TAIL).encode()


def test_failed_test_restores_and_keeps_backup(env):
    conf = (OTHER + blk("factory", "old") + TAIL).encode()
    p = run(env, conf, blk("factory", "broken").encode(), FAKE_FAIL_T="1")
    assert p.returncode != 0
    assert env["conf"].read_bytes() == conf
    assert not any("reload" in c for c in calls(env))
    assert any(b.read_bytes() == conf for b in backups(env))


def test_old_backups_are_pruned(env):
    env["conf"].write_bytes(OTHER.encode())
    for i in range(8):
        assert run(env, None, blk("factory", f"v{i}").encode()).returncode == 0
    assert 1 <= len(backups(env)) <= 5


def test_block_file_must_start_and_end_with_its_markers(env):
    p = run(env, OTHER.encode(), ("junk\n" + blk("factory", "x")).encode())
    assert p.returncode != 0
    assert env["conf"].read_bytes() == OTHER.encode()


def test_foreign_backups_survive_pruning(env):
    env["conf"].write_bytes(OTHER.encode())
    foreign = [env["conf"].parent / n for n in ("default.conf.20250101.bak",
                                                 "default.conf.before-n8n.bak")]
    for f in foreign:
        f.write_bytes(b"owner backup")
    for i in range(7):
        assert run(env, None, blk("factory", f"v{i}").encode()).returncode == 0
    assert all(f.exists() for f in foreign)


def test_failed_backup_copy_leaves_the_live_conf_untouched(env):
    fake_bin = env["tmp"] / "bin"
    fake_bin.mkdir()
    cp = fake_bin / "cp"
    # A copy that writes a few bytes and then fails (disk full).
    cp.write_text('#!/usr/bin/env bash\nhead -c 10 "${@: -2:1}" > "${@: -1}"\nexit 1\n',
                  encoding="utf-8", newline="\n")
    cp.chmod(cp.stat().st_mode | stat.S_IEXEC)
    conf = (OTHER + blk("factory", "old") + TAIL).encode()
    p = run(env, conf, blk("factory", "new").encode(), path=str(fake_bin))
    assert p.returncode != 0
    assert env["conf"].read_bytes() == conf
    assert calls(env) == []
