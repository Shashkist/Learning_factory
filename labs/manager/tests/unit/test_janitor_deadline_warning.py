import logging

import pytest

from app import janitor
from app.sessions import CLEANUP_BOUND_S, READY_TIMEOUT_MAX_S

MINIMUM = READY_TIMEOUT_MAX_S + 2 * CLEANUP_BOUND_S


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(janitor, "_warned_deadlines", set())


def _warns(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_below_the_minimum_warns_once_and_keeps_the_value(monkeypatch, caplog):
    monkeypatch.setenv("LF_START_DEADLINE_S", "30")
    with caplog.at_level(logging.WARNING):
        assert janitor._start_deadline() == 30
        assert janitor._start_deadline() == 30
    assert len(_warns(caplog)) == 1


@pytest.mark.parametrize("value", [str(MINIMUM), str(MINIMUM + 1)])
def test_at_or_above_the_minimum_does_not_warn(monkeypatch, caplog, value):
    monkeypatch.setenv("LF_START_DEADLINE_S", value)
    with caplog.at_level(logging.WARNING):
        assert janitor._start_deadline() == float(value)
    assert _warns(caplog) == []
