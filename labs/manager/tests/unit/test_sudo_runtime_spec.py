from types import SimpleNamespace

from app.runtime import container_spec


def _rt(sudo, monkeypatch, env=None):
    if env:
        monkeypatch.setenv("LF_SUDO_RUNTIME", env)
    else:
        monkeypatch.delenv("LF_SUDO_RUNTIME", raising=False)
    lab = SimpleNamespace(image="term", sudo=sudo, time_limit_min=5)
    return container_spec(lab, "s1")["HostConfig"]["Runtime"]  # type: ignore[arg-type]


def test_sudo_default(monkeypatch):
    assert _rt(True, monkeypatch) == "runsc-suid"


def test_sudo_override(monkeypatch):
    assert _rt(True, monkeypatch, "custom") == "custom"


def test_plain_lab_stays_runsc(monkeypatch):
    assert _rt(False, monkeypatch, "custom") == "runsc"
