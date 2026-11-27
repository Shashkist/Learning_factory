import asyncio
import logging
from pathlib import Path

from app import janitor
from app.content import Lab
from app.runtime import MB, container_spec


def test_spec_caps_shm_and_covers_the_mail_spool():
    lab = Lab("l", {}, "ready", "term", 1, 1, Path("."))
    host = container_spec(lab, "abc")["HostConfig"]
    assert host["ShmSize"] <= 160 * MB
    assert "size=" in host["Tmpfs"]["/var/mail"]


def test_a_repeated_step_failure_is_logged_once_and_again_after_recovery(caplog):
    async def bad():
        raise ConnectionError("x")

    async def good():
        return None

    async def run():
        janitor._failing.clear()
        for _ in range(3):
            await janitor._step("s", bad())
        await janitor._step("s", good())
        await janitor._step("s", bad())

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2


def test_cron_spool_is_a_capped_tmpfs_owned_by_student():
    lab = Lab("l", {}, "ready", "term", 1, 1, Path("."))
    opts = container_spec(lab, "abc")["HostConfig"]["Tmpfs"]["/var/spool/cron/crontabs"]
    assert "size=1m" in opts and "uid=1001" in opts and "gid=1001" in opts
