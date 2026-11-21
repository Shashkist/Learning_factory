"""Janitor (D24): end sessions that must end, remove stray lab containers, mark orphans."""
import asyncio
import logging
import math
import os
import time
from typing import Any

from app import admission, db
from app.auth import _today
from app.runtime import KILL_BOUND_S
from app.sessions import CLEANUP_BOUND_S, LIVE, READY_TIMEOUT_MAX_S, end_session

log = logging.getLogger("lab-manager")
T = db.SCHEMA

_DUE = f"""
select s.id,
  case
    when s.expires_at <= now() then 'time_limit'
    when coalesce(s.last_heartbeat, s.started_at) < now() - make_interval(secs => $1)
      then 'heartbeat'
    else 'access_expired'
  end as reason
from {T}.lab_sessions s
join {T}.students st on st.id_hash = s.student
join {T}.cohorts c on c.id = st.cohort_id
where s.{LIVE}
  and (s.expires_at <= now()
       or coalesce(s.last_heartbeat, s.started_at) < now() - make_interval(secs => $1)
       or st.disabled
       or coalesce(st.access_until, c.access_until) < $2)
"""


async def _end_due(pool: Any, runtime: Any) -> None:
    rows = await pool.fetch(_DUE, admission.env_num("LF_HEARTBEAT_TIMEOUT_S", 300), _today())
    for row in rows:
        if await end_session(pool, runtime, row["id"], row["reason"]):
            log.info("janitor ended session %s (%s)", row["id"], row["reason"])


async def _remove(runtime: Any, name: str) -> None:
    try:
        await runtime.remove(name)
        log.info("janitor removed container %s", name)
    except Exception as exc:
        log.error("janitor could not remove %s (%s)", name, type(exc).__name__)


_REPEAT_S = 900.0  # an ongoing outage is logged again about every 15 minutes
_warned_deadlines: set[str] = set()


def start_deadline_minimum() -> float:
    """Longest a row can legitimately stay in `starting` before the janitor may see it as an orphan:
    the readiness budget, the kill of a timed-out readiness exec and the first cleanup bound.
    (The second cleanup bound removes the container after the row is gone.)"""
    return READY_TIMEOUT_MAX_S + KILL_BOUND_S + CLEANUP_BOUND_S


def _start_deadline() -> float:
    """LF_START_DEADLINE_S; a non-finite, non-positive or unparsable value falls back to 180."""
    raw = os.environ.get("LF_START_DEADLINE_S")
    try:
        value = float(raw) if raw is not None else 180.0
    except ValueError:
        value = math.nan
    if math.isfinite(value) and value > 0:
        minimum = start_deadline_minimum()
        if value < minimum and raw not in _warned_deadlines:
            _warned_deadlines.add(raw or "")
            log.warning(
                "LF_START_DEADLINE_S=%r is below the longest legitimate start (minimum %g); "
                "a slow start can be ended as an orphan",
                raw,
                minimum,
            )
        return value
    if raw not in _warned_deadlines:
        _warned_deadlines.add(raw or "")
        log.warning("LF_START_DEADLINE_S=%r is not a positive number; using 180", raw)
    return 180.0


async def _sweep_containers(pool: Any, runtime: Any) -> None:
    live_sql = (
        f"select id, container, status, "
        f"started_at < now() - make_interval(secs => $1) as stale from {T}.lab_sessions "
        f"where {LIVE}"
    )
    deadline = _start_deadline()
    before = await pool.fetch(live_sql, deadline)
    containers = await runtime.list_lab_containers()
    after = await pool.fetch(live_sql, deadline)  # a row can flip while the listing runs
    status = {str(r["id"]): r["status"] for r in after}
    starting = {str(r["id"]) for r in (*before, *after) if r["status"] == "starting"}
    # A start that never finished (crash between create and the row update, DB error): end it.
    # The end is guarded in the database: a row that flipped to running meanwhile is kept.
    candidates = {str(r["id"]) for r in after if r["status"] == "starting" and r["stale"]}
    stale = set()
    for sid in candidates:
        if await end_session(pool, runtime, sid, "orphan", stale_start_s=deadline):
            stale.add(sid)
            log.info("janitor ended session %s (orphan, start deadline)", sid)
    present = set()
    for info in containers:
        name, running, session = info[0], info[1], info[2]
        if session in stale:
            continue  # end_session removed it
        # A starting session owns its container (the start path cleans up after itself).
        if session in starting or (session in status and running):
            present.add(name)
            continue
        await _remove(runtime, name)
    for row in before:
        if row["status"] == "running" and row["container"] not in present:
            if await end_session(pool, runtime, row["id"], "orphan"):
                log.info("janitor ended session %s (orphan)", row["id"])


async def _sweep_expired(runtime: Any) -> None:
    """Without a usable database: remove containers past their `lf.expires` label."""
    now = time.time()
    for info in await runtime.list_lab_containers():
        if info.expires is not None and info.expires <= now:
            await _remove(runtime, info[0])


# step name -> (error type, outage start, time of the last ERROR line), monotonic seconds
_failing: dict[str, tuple[str, float, float]] = {}


async def _step(name: str, coro: Any) -> bool:
    try:
        await coro
    except Exception as exc:
        now = time.monotonic()
        kind = type(exc).__name__
        prev = _failing.get(name)
        if prev is None or prev[0] != kind:
            # the outage start survives a type change; only its first failure has a traceback
            _failing[name] = (kind, prev[1] if prev else now, now)
            log.error("janitor step %s failed (%s)", name, kind, exc_info=prev is None)
        elif now - prev[2] >= _REPEAT_S:
            _failing[name] = (kind, prev[1], now)
            log.error(
                "janitor step %s still failing (%s) for %d min", name, kind, (now - prev[1]) // 60
            )
        else:
            log.debug("janitor step %s failed again (%s)", name, kind)
        return False
    prev = _failing.pop(name, None)
    if prev is not None:
        log.info(
            "janitor step %s recovered after %d min", name, (time.monotonic() - prev[1]) // 60
        )
    return True


async def run_once(app: Any) -> bool:
    """One pass; True when the database-backed sweep worked."""
    pool = app.state.pool
    runtime = app.state.runtime
    try:
        for reason in admission.low_resources():
            log.warning("low resources: %s", reason)
    except Exception as exc:
        log.error("janitor step resources failed (%s)", type(exc).__name__)
    ok = pool is not None
    if pool is not None:
        await _step("end_due", _end_due(pool, runtime))
        ok = await _step("sweep", _sweep_containers(pool, runtime))
        if ok:
            _failing.pop("sweep_expired", None)  # it only runs while the database is down
    if not ok:
        await _step("sweep_expired", _sweep_expired(runtime))
    return ok


async def loop(app: Any) -> None:
    """Sweep on startup, then every LF_JANITOR_INTERVAL_S seconds, until cancelled.

    While the database is unusable the retry delay starts at 2 s and doubles up to the interval.
    """
    _failing.clear()
    backoff = 2.0
    while True:
        healthy = False
        try:
            healthy = await run_once(app)
        except Exception as exc:
            log.error("janitor pass failed (%s)", type(exc).__name__)
        interval = max(1.0, admission.env_num("LF_JANITOR_INTERVAL_S", 60))
        if healthy:
            backoff = 2.0
            await asyncio.sleep(interval)
        else:
            await asyncio.sleep(min(backoff, interval))
            backoff *= 2
