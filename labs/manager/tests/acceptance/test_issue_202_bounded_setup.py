"""Acceptance tests for issue #202: bounded task setup under nginx, end reasons as one set.

No Docker, no database. Contract the implementation must provide (names are the contract):

- `app.tasks.SETUP_TIMEOUT_MAX_S` (float, 40.0): upper cap of the task setup timeout.
  `app.tasks.setup_timeout()` never returns more, whatever LF_SETUP_TIMEOUT_S says (clamped,
  not refused); unset, junk or non-positive values still give `DEFAULT_SETUP_TIMEOUT_S`, which
  must not exceed the cap (so it drops from 50 s to at most 40 s).
- `app.tasks.SLOT_WAIT_S` (exists, 5.0): longest wait for an exec slot.
- `app.runtime.KILL_BOUND_S` (float, 5.0): longest kill of a timed-out script (the existing
  `min(5.0, ...)` in `DockerRuntime.exec_script` uses it).
- `app.tasks.DB_STEP_BOUND_S` (float, 8.0): longest database step after the setup (its
  lock_timeout and statement bound must be set to this, not 30 s).
- `SLOT_WAIT_S + SETUP_TIMEOUT_MAX_S + KILL_BOUND_S + DB_STEP_BOUND_S < 60` (nginx's
  proxy_read_timeout), asserted here.
- `app.sessions.END_REASONS` (frozenset[str]): the allowed `end_reason` values. It equals the
  set in the CHECK constraint of the newest migration under `migrations/` that defines the
  `end_reason` constraint. `end_session` raises ValueError for any reason outside it, before
  touching the pool or the runtime.
"""
import asyncio
import re
from pathlib import Path

import pytest

NGINX_READ_TIMEOUT_S = 60.0
SID = "00000000-0000-0000-0000-000000000000"
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _tasks():
    from app import tasks

    return tasks


def test_setup_timeout_cap_and_slot_wait_kill_db_sum_stay_under_nginx_60s():
    from app import runtime

    tasks = _tasks()
    total = (
        tasks.SLOT_WAIT_S
        + tasks.SETUP_TIMEOUT_MAX_S
        + runtime.KILL_BOUND_S
        + tasks.DB_STEP_BOUND_S
    )
    assert total < NGINX_READ_TIMEOUT_S, total


def test_default_setup_timeout_is_within_the_cap(monkeypatch):
    tasks = _tasks()
    monkeypatch.delenv("LF_SETUP_TIMEOUT_S", raising=False)
    assert 0 < tasks.DEFAULT_SETUP_TIMEOUT_S <= tasks.SETUP_TIMEOUT_MAX_S
    assert tasks.setup_timeout() == tasks.DEFAULT_SETUP_TIMEOUT_S


@pytest.mark.parametrize("value", ["55", "60", "3600", "1e9", "inf"])
def test_setup_timeout_above_the_cap_is_clamped(monkeypatch, value):
    tasks = _tasks()
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", value)
    assert tasks.setup_timeout() == tasks.SETUP_TIMEOUT_MAX_S


def test_setup_timeout_below_the_cap_is_kept(monkeypatch):
    tasks = _tasks()
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", "12.5")
    assert tasks.setup_timeout() == 12.5
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", str(tasks.SETUP_TIMEOUT_MAX_S))
    assert tasks.setup_timeout() == tasks.SETUP_TIMEOUT_MAX_S


@pytest.mark.parametrize("value", ["", "abc", "0", "-5", "nan"])
def test_setup_timeout_junk_falls_back_to_the_default(monkeypatch, value):
    tasks = _tasks()
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", value)
    assert tasks.setup_timeout() == tasks.DEFAULT_SETUP_TIMEOUT_S


# ---- end reasons -------------------------------------------------------------------------


def _newest_migration_reasons(migrations: Path = MIGRATIONS) -> set[str]:
    """Reasons of the newest migration that mentions end_reason. If that migration states the
    constraint in a form this parser does not understand, fail (never fall back to an older
    migration, which would hide the newer constraint)."""
    for path in sorted(migrations.glob("*.sql"), reverse=True):
        sql = path.read_text(encoding="utf-8")
        if not re.search(r"end_reason", sql, re.IGNORECASE):
            continue
        found = re.search(r"end_reason\s+in\s*\(([^)]*)\)", sql, re.IGNORECASE)
        assert found, (
            f"{path.name} mentions end_reason but its constraint is not in the "
            "`end_reason in ('a', 'b')` form this exam can read; teach the exam the new form"
        )
        reasons = set(re.findall(r"'([^']*)'", found.group(1)))
        assert reasons, f"{path.name}: end_reason constraint lists no reasons"
        return reasons
    raise AssertionError("no migration defines the end_reason constraint")


def test_end_reason_parser_fails_on_a_newer_migration_it_cannot_parse(tmp_path):
    (tmp_path / "003_a.sql").write_text(
        "alter table t add column end_reason text check (end_reason in ('a', 'b'));"
    )
    assert _newest_migration_reasons(tmp_path) == {"a", "b"}
    (tmp_path / "004_b.sql").write_text(
        "alter table t drop constraint c, add check (end_reason = any(array['a', 'c']));"
    )
    with pytest.raises(AssertionError, match="004_b.sql"):
        _newest_migration_reasons(tmp_path)
    unrelated = "-- unrelated" + chr(10) + "alter table t add column x int;"
    (tmp_path / "005_c.sql").write_text(unrelated)
    with pytest.raises(AssertionError, match="004_b.sql"):
        _newest_migration_reasons(tmp_path)


def test_end_reasons_is_a_frozenset_equal_to_the_newest_migration_constraint():
    from app import sessions

    assert isinstance(sessions.END_REASONS, frozenset)
    assert all(isinstance(r, str) and r for r in sessions.END_REASONS)
    assert set(sessions.END_REASONS) == _newest_migration_reasons()


class _Pool:
    def __init__(self):
        self.calls = 0

    async def fetchval(self, *args, **kwargs):
        self.calls += 1
        return None

    fetchrow = fetch = execute = fetchval


class _Runtime:
    def __getattr__(self, name):
        raise AssertionError(f"runtime touched: {name}")


@pytest.mark.parametrize("reason", ["x", "", "Student", "student ", "student')--", "deleted"])
def test_end_session_rejects_an_unknown_reason_before_the_database(reason):
    from app import sessions

    pool = _Pool()
    with pytest.raises(ValueError):
        asyncio.run(sessions.end_session(pool, _Runtime(), SID, reason))
    assert pool.calls == 0


def test_end_session_accepts_every_allowed_reason():
    from app import sessions

    for reason in sorted(sessions.END_REASONS):
        pool = _Pool()  # fetchval answers None: "not live", so nothing is removed
        res = asyncio.run(
            sessions.end_session(pool, _Runtime(), SID, reason)
        )
        assert res is False
        assert pool.calls == 1
