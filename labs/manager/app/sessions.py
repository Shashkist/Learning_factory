"""Session lifecycle API (D24): start, reopen, current, heartbeat, end.

One live session per student is enforced by the partial unique index; the row is inserted
before the container is started, so a losing concurrent start never creates a container."""

import asyncio
import logging
import time
import uuid
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import admission, db
from app.auth import Principal, current_student, db_guard
from app.content import Lab
from app.runtime import LabRuntime, container_name, container_spec, memory_limit_mb
from app.smoke import is_smoke_member

log = logging.getLogger("lab-manager")
T = db.SCHEMA
LIVE_STATUSES = admission.LIVE_STATUSES
LIVE = admission.LIVE

router = APIRouter(prefix="/api/sessions")


class StartBody(BaseModel):
    lab_id: str = Field(max_length=200)
    force: bool = False


async def _in_smoke_cohort(pool: Any, student: str) -> bool:
    with db_guard():
        name = await pool.fetchval(
            f"select c.name from {T}.students s join {T}.cohorts c on c.id = s.cohort_id "
            "where s.id_hash = $1",
            student,
        )
    return is_smoke_member(name)


async def remove_container(runtime: Any, session_id: Any) -> None:
    """Best effort: a container that survives is removed by the janitor's sweep."""
    try:
        await runtime.remove(container_name(session_id))
    except Exception as exc:
        log.error("removing container of session %s failed (%s)", session_id, type(exc).__name__)


async def end_session(pool: Any, runtime: Any, session_id: Any, reason: str) -> bool:
    """End a live session and remove its container; False when it was not live."""
    ended = await pool.fetchval(
        f"update {T}.lab_sessions set status = 'ended', ended_at = now(), end_reason = $2 "
        f"where id = $1 and {LIVE} returning id",
        session_id,
        reason,
    )
    if ended is None:
        return False
    await remove_container(runtime, session_id)
    return True


def _shape(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "lab_id": row["lab_id"],
        "status": row["status"],
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
    }


async def _live(pool: Any, student: str) -> Any:
    return await pool.fetchrow(
        f"select id, lab_id, status, expires_at from {T}.lab_sessions "
        f"where student = $1 and {LIVE}",
        student,
    )


def _conflict(labs: dict[str, Lab], row: Any) -> HTTPException:
    lab = labs.get(row["lab_id"])
    return HTTPException(
        409,
        {
            "code": "another_lab_open",
            "session_id": str(row["id"]),
            "lab_id": row["lab_id"],
            "title": lab.title if lab else {},
        },
    )


# nginx answers 504 at 60 s (its default proxy_read_timeout; labs/deploy/nginx/labs.conf sets none)
READY_TIMEOUT_MAX_S = 50.0
CLEANUP_BOUND_S = 5.0  # what is left after a failed start is the janitor's (LF_START_DEADLINE_S)


async def _stamp_request(request: Request) -> None:
    """Runs before authentication (route dependencies come first), so the start budget counts
    the auth round trips too."""
    request.state.started = time.monotonic()


def _loop_time(deadline: float) -> float:
    return asyncio.get_running_loop().time() + (deadline - time.monotonic())


async def wait_ready(runtime: LabRuntime, name: str, deadline: float) -> None:
    """Running means the entrypoint copied the skel (ttyd starts after it). Bounded by
    LF_READY_TIMEOUT_S (default 45, capped at 50 s, below nginx's 60 s) counted from the start of
    the request: `deadline` is a time.monotonic() value, so a slow forced end or create/start
    leaves the wait only what is left. A runtime without `wait_ready` is ready at once."""
    waiter = getattr(runtime, "wait_ready", None)
    if waiter is None:
        log.warning("runtime %s has no wait_ready; assuming ready", type(runtime).__name__)
        return
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("start budget spent before the readiness wait")
    await waiter(name, left)


@router.post("", status_code=201, dependencies=[Depends(_stamp_request)])
async def start(
    body: StartBody,
    request: Request,
    response: Response,
    p: Annotated[Principal, Depends(current_student)],
) -> dict[str, Any]:
    # The route dependency stamps the request before authentication; a direct call (the #177
    # exam calls start() with a bare request) counts from here.
    started = getattr(getattr(request, "state", None), "started", None) or time.monotonic()
    budget = min(admission.env_num("LF_READY_TIMEOUT_S", 45), READY_TIMEOUT_MAX_S)
    deadline = started + budget
    state = request.app.state
    labs: dict[str, Lab] = state.labs
    lab = labs.get(body.lab_id)
    if lab is None:
        raise HTTPException(404, "unknown_lab")
    pool = state.pool
    runtime: LabRuntime = state.runtime
    rows: list[Any] = []  # the inserted row, once there is one (what a failure must clean up)

    async def run() -> dict[str, Any]:
        if lab.status == "internal" and (
            pool is None or not await _in_smoke_cohort(pool, p.subject)
        ):
            raise HTTPException(404, "unknown_lab")  # byte-identical to a missing lab (D54)
        if pool is None:
            raise HTTPException(503, "database unavailable")
        existing = await _live(pool, p.subject)
        if existing is not None:
            if existing["lab_id"] == lab.id:
                response.status_code = 200
                return _shape(existing)
            if not body.force:
                raise _conflict(labs, existing)
        if not (
            admission.resources_ok(memory_limit_mb(lab)) and await admission.cap_ok(pool, lab, labs)
        ):
            raise HTTPException(503, "capacity_full")
        if existing is not None:
            await end_session(pool, runtime, existing["id"], "forced")
        try:
            row = await pool.fetchrow(
                f"insert into {T}.lab_sessions (student, lab_id, status, expires_at) "
                "values ($1, $2, 'starting', now() + make_interval(mins => $3)) "
                "returning id, lab_id, status, expires_at",
                p.subject,
                lab.id,
                lab.time_limit_min,
            )
        except asyncpg.UniqueViolationError:
            other = await _live(pool, p.subject)
            if other is None:
                raise
            if other["lab_id"] == lab.id:
                response.status_code = 200
                return _shape(other)
            raise _conflict(labs, other) from None
        rows.append(row)
        name = str(await runtime.start(container_spec(lab, str(row["id"]))))
        await wait_ready(runtime, name, deadline)
        marked = await pool.fetchval(
            f"update {T}.lab_sessions set status = 'running', container = $2, "
            "last_heartbeat = now() "
            "where id = $1 and status = 'starting' returning id",
            row["id"],
            name,
        )
        if marked is None:  # ended while the container was starting
            await remove_container(runtime, str(row["id"]))
            raise HTTPException(409, "session_ended")
        return _shape({**dict(row), "status": "running"})

    with db_guard():
        try:
            # one deadline over the whole start, database calls included; a TimeoutError escaping
            # would be turned into 503 by db_guard, so it is answered here
            async with asyncio.timeout_at(_loop_time(deadline)):
                return await run()
        except HTTPException:
            raise
        except Exception as exc:
            if not rows and not isinstance(exc, TimeoutError):
                raise
            log.error("starting session of lab %s failed (%s)", lab.id, type(exc).__name__)
            ended_meanwhile = False
            if rows:  # the deadline (if it fired) is over: cleanup has two 5 s bounds (D24)
                row = rows[0]
                try:  # the row first: it holds the student's slot
                    async with asyncio.timeout(CLEANUP_BOUND_S):
                        deleted = await pool.execute(
                            f"delete from {T}.lab_sessions where id = $1 and status = 'starting'",
                            row["id"],
                        )
                        if str(deleted).endswith(" 0"):
                            # not 'starting' any more: the final update committed before the
                            # deadline cancelled it (end the live row; 'orphan' is the janitor's
                            # reason for a live row without a container), or it was ended meanwhile
                            ended_now = await pool.fetchval(
                                f"update {T}.lab_sessions set status = 'ended', ended_at = now(), "
                                f"end_reason = 'orphan' where id = $1 and {LIVE} returning id",
                                row["id"],
                            )
                            ended_meanwhile = ended_now is None
                except Exception as err:  # a slow or failed row step never skips the remove
                    log.error("cleaning up the session row failed (%s)", type(err).__name__)
                try:  # the janitor's orphan sweep removes what is left
                    async with asyncio.timeout(CLEANUP_BOUND_S):
                        await remove_container(runtime, str(row["id"]))
                except Exception as err:
                    log.error("removing the container failed (%s)", type(err).__name__)
            if ended_meanwhile:
                raise HTTPException(409, "session_ended") from None
            raise HTTPException(502, "start_failed") from None


@router.get("/current")
async def current(
    request: Request, p: Annotated[Principal, Depends(current_student)]
) -> dict[str, Any]:
    if request.app.state.pool is None:
        raise HTTPException(503, "database unavailable")
    with db_guard():
        row = await _live(request.app.state.pool, p.subject)
    if row is None:
        raise HTTPException(404, "no_session")
    return _shape(row)


def _session_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, "no_session") from None


@router.post("/{session_id}/heartbeat", status_code=204)
async def heartbeat(
    session_id: str, request: Request, p: Annotated[Principal, Depends(current_student)]
) -> Response:
    sid = _session_id(session_id)
    if request.app.state.pool is None:
        raise HTTPException(503, "database unavailable")
    with db_guard():
        found = await request.app.state.pool.fetchval(
            # active time (the SQL form of app.flags.covered_seconds): the gap since the
            # previous beat counts, as fractional seconds, unless it is a long silence
            f"update {T}.lab_sessions set last_heartbeat = now(), active_seconds = active_seconds"
            " + case when last_heartbeat is not null"
            " and now() - last_heartbeat <= make_interval(secs => $3)"
            " then extract(epoch from now() - last_heartbeat)::double precision else 0 end "
            f"where id = $1 and student = $2 and {LIVE} returning id",
            sid,
            p.subject,
            float(admission.env_num("LF_HEARTBEAT_TIMEOUT_S", 300)),
        )
    if found is None:
        raise HTTPException(404, "no_session")
    return Response(status_code=204)


@router.post("/{session_id}/end", status_code=204)
async def end(
    session_id: str, request: Request, p: Annotated[Principal, Depends(current_student)]
) -> Response:
    sid = _session_id(session_id)
    pool = request.app.state.pool
    if pool is None:
        raise HTTPException(503, "database unavailable")
    with db_guard():
        mine = await pool.fetchval(
            f"select id from {T}.lab_sessions where id = $1 and student = $2", sid, p.subject
        )
        if mine is None:
            raise HTTPException(404, "no_session")
        await end_session(pool, request.app.state.runtime, sid, "student")
    return Response(status_code=204)
