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
    live_sql = f"select id, container, status from {T}.lab_sessions where {LIVE}"
    before = await pool.fetch(live_sql)
    containers = await runtime.list_lab_containers()
    status = {str(r["id"]): r["status"] for r in await pool.fetch(live_sql)}  # after listing
    present = set()
    for info in containers:
        name, running, session = info[0], info[1], info[2]
        # A starting session owns its container (the start path cleans up after itself).
        if session in status and (running or status[session] == "starting"):
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


async def _step(name: str, coro: Any) -> bool:
    try:
        await coro
        return True
    except Exception as exc:
        log.error("janitor step %s failed (%s)", name, type(exc).__name__)
        return False


async def run_once(app: Any) -> None:
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


async def loop(app: Any) -> None:
    """Sweep on startup, then every LF_JANITOR_INTERVAL_S seconds, until cancelled."""
    while True:
        try:
            await run_once(app)
        except Exception as exc:
            log.error("janitor pass failed (%s)", type(exc).__name__)
        if app.state.pool is None:  # database not up yet: the first full sweep waits for it
            await asyncio.sleep(2.0)
        else:
            await asyncio.sleep(max(1.0, admission.env_num("LF_JANITOR_INTERVAL_S", 60)))
