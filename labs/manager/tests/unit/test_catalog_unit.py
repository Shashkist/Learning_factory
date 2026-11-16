from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.catalog import pick, progress

T0 = datetime(2026, 1, 1, tzinfo=UTC)
LAB = SimpleNamespace(tasks=[1, 2, 3])


def row(status, minutes, passed=0):
    return {"status": status, "started_at": T0 + timedelta(minutes=minutes), "passed": passed}


def test_pick_prefers_live_over_newer_ended():
    live, ended = row("running", 0), row("ended", 5)
    assert pick([ended, live]) is live


def test_pick_latest_when_none_live():
    old, new = row("ended", 0), row("ended", 9)
    assert pick([old, new]) is new


def test_progress_values():
    assert progress(LAB, True, None) == "done"
    assert progress(LAB, False, None) == "not_started"
    assert progress(LAB, False, row("ended", 0, 2)) == "2/3"
