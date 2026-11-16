from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.catalog import pick, progress

T0 = datetime(2026, 1, 1, tzinfo=UTC)
LAB = SimpleNamespace(tasks=[SimpleNamespace(id=i) for i in ("a", "b", "c")])


def row(status, minutes, passed=()):
    started = T0 + timedelta(minutes=minutes)
    return {"status": status, "started_at": started, "passed_ids": list(passed)}


def test_pick_prefers_live_over_newer_ended():
    live, ended = row("running", 0), row("ended", 5)
    assert pick([ended, live]) is live


def test_pick_latest_when_none_live():
    old, new = row("ended", 0), row("ended", 9)
    assert pick([old, new]) is new


def test_progress_values():
    assert progress(LAB, True, None) == "done"
    assert progress(LAB, False, None) == "not_started"
    assert progress(LAB, False, row("ended", 0, "ab")) == "2/3"


def test_removed_task_ids_never_count():
    assert progress(LAB, False, row("ended", 0, ["a", "x", "y", "z"])) == "1/3"
    assert progress(LAB, False, row("running", 0, ["a", "b", "c", "x"])) == "3/3"


def test_live_means_the_same_in_sql_and_python():
    from app.sessions import LIVE, LIVE_STATUSES

    assert LIVE == "status in ('starting', 'running')" and "ended" not in LIVE_STATUSES
