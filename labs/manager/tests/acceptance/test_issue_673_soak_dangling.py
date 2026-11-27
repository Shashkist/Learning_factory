"""Acceptance tests for issue #673 (review of #669, #656): the soak's volume check and df read.

The real `SshHost` is driven with a fake `sh` that behaves like the server's docker and df, so the
tests see only what the soak asks and prints. Nothing here needs docker or a server.
"""
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SMOKE_DIR = REPO_ROOT / "labs" / "deploy" / "smoke"
SOAK = SMOKE_DIR / "soak.py"
SECRET = "customer-billing-db-7f3a"

DF_OK = (
    "Filesystem 1-blocks Used Available Capacity Mounted on\n"
    "/dev/vda1 100 40 60000000000 40% /\n"
)


def _soak():
    assert SOAK.is_file(), "labs/deploy/smoke/soak.py is missing"
    sys.path.insert(0, str(SMOKE_DIR))
    try:
        spec = importlib.util.spec_from_file_location("soak_673", SOAK)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SMOKE_DIR))
    return mod


class Api:
    """Three sessions: heartbeat, time_limit, student; every end reason is known at once."""

    def __init__(self):
        self.ids = [str(uuid.uuid4()) for _ in range(3)]
        self.reasons = dict(zip(self.ids, ["heartbeat", "time_limit", "student"], strict=True))
        self.started = 0

    def start(self, lab):
        sid = self.ids[self.started]
        self.started += 1
        return sid

    def heartbeat(self, sid):
        pass

    def end(self, sid):
        pass

    def end_reason(self, sid):
        return self.reasons[sid]


class Server:
    """Fake `sh`: volumes are (name, dangling) pairs, before and after the sessions."""

    def __init__(self, api, before=(), after=(), df_first=DF_OK):
        self.api, self.before, self.after, self.df_first = api, list(before), list(after), df_first
        self.commands, self.df_reads = [], 0

    def __call__(self, command):
        self.commands.append(command)
        vols = self.after if self.api.started else self.before
        if command.startswith("free"):
            return "total used free shared buff/cache available\nMem: 1 1 1 1 1 8000000000\n"
        if command.startswith("df"):
            self.df_reads += 1
            return self.df_first if self.df_reads == 1 else DF_OK
        if "docker volume ls" in command:
            if "name=lab-" in command:
                return ""
            if "dangling=true" in command:
                return "\n".join(n for n, dangling in vols if dangling)
            return "\n".join(n for n, _ in vols)
        return ""  # docker ps, live-sessions


def _run(before=(), after=(), df_first=DF_OK, monkeypatch=None):
    mod = _soak()
    api = Api()
    host = mod.SshHost("cfg")
    server = Server(api, before, after, df_first)
    host.sh = server
    out, err = [], []
    rc = mod.run_soak(api, host, wait_s=0, out=out.append, err=err.append)
    return rc, out, err, server


# ---- 1. only dangling volumes are compared


def test_ac1_new_non_dangling_foreign_volume_does_not_fail_the_soak():
    after = [("old", False), ("other-svc-data", False)]
    rc, out, err, _ = _run(before=[("old", False)], after=after)
    assert rc == 0 and not err, err
    assert len(out) == 1


def test_ac1_new_dangling_volume_still_fails_the_soak():
    rc, out, err, _ = _run(before=[("old", True)], after=[("old", True), ("lf-leftover", True)])
    assert rc == 1 and err
    assert len(out) == 1


def test_ac1_volumes_are_listed_with_the_dangling_filter():
    _, _, _, server = _run()
    listings = [c for c in server.commands if "docker volume ls" in c and "name=lab-" not in c]
    assert listings and all("docker volume ls -q --filter dangling=true" in c for c in listings)


# ---- 2. foreign names never reach the log, only a count


def test_ac2_new_foreign_dangling_volume_is_reported_by_count_not_name():
    rc, out, err, _ = _run(after=[(SECRET, True)])
    assert rc == 1
    text = "\n".join(out + err)
    assert SECRET not in text
    assert any("volume" in e and "1" in e for e in err), err


def test_ac2_several_foreign_dangling_volumes_report_the_count():
    rc, out, err, _ = _run(after=[(SECRET, True), (SECRET + "-b", True)])
    assert rc == 1
    assert SECRET not in "\n".join(out + err)
    assert any("volume" in e and "2" in e for e in err), err


def test_ac2_lab_and_lf_volume_names_do_not_hide_the_failure():
    rc, _, err, _ = _run(after=[("lab-abc", True)])
    assert rc == 1 and err


# ---- 3. a short or unparsable first df read is a clean failure


@pytest.mark.parametrize(
    "df_text",
    ["", "Filesystem 1-blocks Used Available Capacity Mounted on\n", "garbage", "h\n/dev/x 1 2\n",
     "h\n/dev/x 1 2 notanumber 4% /\n"],
)
def test_ac3_short_or_unparsable_first_df_read_fails_cleanly(df_text, capsys):
    rc, out, err, _ = _run(df_first=df_text)
    assert rc == 1
    assert any(e.startswith("soak failed:") for e in err), err
    assert len(out) == 1 and out[0].startswith("soak summary:")
    shown = "\n".join(out + err) + capsys.readouterr().out + capsys.readouterr().err
    assert "Traceback" not in shown and "IndexError" not in shown


def test_ac3_short_df_failure_does_not_start_any_session():
    mod = _soak()
    api = Api()
    host = mod.SshHost("cfg")
    host.sh = Server(api, df_first="")
    out, err = [], []
    mod.run_soak(api, host, wait_s=0, out=out.append, err=err.append)
    assert api.started == 0


# ---- 4. the wrong-counts path reaches the failure it asserts


def test_ac4_wrong_end_reasons_fail_with_the_per_session_line():
    mod = _soak()
    api = Api()
    api.reasons[api.ids[0]] = "student"  # session 1 should have ended with heartbeat
    host = mod.SshHost("cfg")
    host.sh = Server(api)
    out, err = [], []
    rc = mod.run_soak(api, host, wait_s=0, out=out.append, err=err.append)
    assert rc == 1
    assert any("ended with student, expected heartbeat" in e for e in err), err
    assert len(out) == 1
