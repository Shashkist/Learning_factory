"""Unit tests for the nightly soak (#656): end reasons, id check, volumes, final reads."""
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
SMOKE = ROOT / "labs" / "deploy" / "smoke"


def _load():
    saved = sys.modules.get("lab_api")
    mods = {}
    for name, file in (("lab_api", "lab_api.py"), ("soak_unit", "soak.py")):
        spec = importlib.util.spec_from_file_location(name, SMOKE / file)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    sys.modules.pop("soak_unit", None)
    if saved is None:
        sys.modules.pop("lab_api", None)
    else:
        sys.modules["lab_api"] = saved
    return mods["soak_unit"]


soak = _load()


class Api:
    def __init__(self, ids):
        self.ids, self.n = ids, 0
        self.reasons = {ids[0]: "heartbeat", ids[1]: "time_limit"}
        self.hidden: set[str] = set()

    def start(self, lab):
        self.n += 1
        return self.ids[self.n - 1]

    def heartbeat(self, sid):
        pass

    def end(self, sid):
        pass

    def end_reason(self, sid):
        return None if sid in self.hidden else self.reasons.get(sid, "student")


class Host:
    def __init__(self, vols=((), ()), final_error=None):
        self.vols, self.final_error, self.reads = list(vols), final_error, 0

    def memory_available(self):
        self.reads += 1
        if self.final_error and self.reads > 1:
            raise self.final_error
        return 10

    def disk_available(self):
        return 10

    def lab_containers(self):
        return []

    def lab_volumes(self):
        return []

    def volume_names(self):
        return list(self.vols.pop(0))


def _api():
    return Api([str(uuid.uuid4()) for _ in range(30)])


def _go(api, host):
    out, err = [], []
    rc = soak.run_soak(api, host, wait_s=0, out=out.append, err=err.append)
    return rc, out, err


def test_all_good():
    rc, out, err = _go(_api(), Host())
    assert rc == 0 and not err
    assert "student=28 heartbeat=1 time_limit=1" in out[0]


def test_missing_end_reason_after_student_end_fails():
    api = _api()
    api.hidden.add(api.ids[5])
    rc, _, err = _go(api, Host())
    assert rc == 1 and any("no end reason recorded" in e for e in err)


def test_repeated_session_id_fails():
    api = _api()
    api.ids[5] = api.ids[4]
    rc, _, err = _go(api, Host())
    assert rc == 1 and any("repeated" in e for e in err)


def test_wrong_counts_fail():
    api = _api()
    api.reasons = {api.ids[0]: "heartbeat", api.ids[1]: "student"}
    rc, _, err = _go(api, Host())
    assert rc == 1 and any("ended with student, expected time_limit" in e for e in err)


def test_new_foreign_volume_is_counted_never_named():
    rc, out, err = _go(_api(), Host(vols=(["a"], ["a", "deadbeef", "other"])))
    assert rc == 1 and len(out) == 1
    assert any("2 new dangling docker volume" in e for e in err)
    assert not any("deadbeef" in e or "other" in e for e in err + out)


def test_new_lab_volume_is_named():
    rc, _, err = _go(_api(), Host(vols=([], ["lf-left"])))
    assert rc == 1 and any("lf-left" in e for e in err)


def test_volumes_listed_dangling_only(monkeypatch):
    host = soak.SshHost("cfg")
    seen = []
    monkeypatch.setattr(host, "sh", lambda c: seen.append(c) or "a b")
    assert host.volume_names() == ["a", "b"]
    assert seen == ["docker volume ls -q --filter dangling=true"]


@pytest.mark.parametrize("text", ["", "h\n/dev/x 1 2\n", "h\n/dev/x 1 2 nope 4% /\n"])
def test_short_first_df_fails_cleanly_without_sessions(text):
    api = _api()
    host = Host()
    host.disk_available = lambda: soak.parse_df_available(text)
    rc, out, err = _go(api, host)
    assert rc == 1 and api.n == 0 and len(out) == 1
    assert any(e.startswith("soak failed:") for e in err)


def test_final_read_error_is_a_failure_and_summary_printed():
    rc, out, err = _go(_api(), Host(final_error=IndexError()))
    assert rc == 1 and len(out) == 1 and any("final reads failed" in e for e in err)


def test_public_api_start_rejects_non_uuid(monkeypatch):
    host = soak.SshHost("cfg")
    api = soak.PublicApi("http://x", "c", host)
    monkeypatch.setattr(soak, "start", lambda *a: "1; rm -rf /")
    with pytest.raises(soak.Fail):
        api.start("lab")
    good = str(uuid.uuid4())
    monkeypatch.setattr(soak, "start", lambda *a: good.upper())
    assert api.start("lab") == good and good in host.own


def test_lab_containers_skip_foreign_live_sessions(monkeypatch):
    host = soak.SshHost("cfg")
    host.own = {"mine"}
    answers = {"docker ps": "lab-mine lab-theirs lab-old", "live-sessions": "mine theirs"}
    monkeypatch.setattr(host, "sh", lambda c: next(v for k, v in answers.items() if k in c))
    assert host.lab_containers() == ["lab-mine", "lab-old"]
