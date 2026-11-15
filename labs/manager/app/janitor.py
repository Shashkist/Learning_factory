"""Janitor (D24): end sessions that must end, remove stray lab containers, mark orphans."""
import asyncio
import logging
import time
from typing import Any

from app import admission, db
from app.auth import _today
from app.sessions import LIVE, end_session

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


async def _sweep_containers(pool: Any, runtime: Any) -> None:
    live_sql = (
        f"select id, container, status, "
        f"started_at < now() - make_interval(secs => $1) as stale from {T}.lab_sessions "
        f"where {LIVE}"
    )
    deadline = admission.env_num("LF_START_DEADLINE_S", 180)
    before = await pool.fetch(live_sql, deadline)
    containers = await runtime.list_lab_containers()
    after = await pool.fetch(live_sql, deadline)  # a row can flip while the listing runs
    status = {str(r["id"]): r["status"] for r in after}
    starting = {str(r["id"]) for r in (*before, *after) if r["status"] == "starting"}
    # A start that never finished (crash between create and the row update, DB error): end it.
    stale = {str(r["id"]) for r in after if r["status"] == "starting" and r["stale"]}
    for sid in stale:
        if await end_session(pool, runtime, sid, "orphan"):
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


_failing: dict[str, str] = {}  # step name -> the error already logged for it


async def _step(name: str, coro: Any) -> bool:
    try:
        await coro
        _failing.pop(name, None)
        return True
    except Exception as exc:
        msg = "janitor step %s failed (%s)" % (name, type(exc).__name__)  # noqa: UP031
        # an outage is logged once, not on every pass; a different error is logged again
        log.log(logging.DEBUG if _failing.get(name) == msg else logging.ERROR, msg)
        _failing[name] = msg
        return False


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
