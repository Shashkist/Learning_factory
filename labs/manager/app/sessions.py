"""Session lifecycle API (D24): start, reopen, current, heartbeat, end.

One live session per student is enforced by the partial unique index; the row is inserted
before the container is started, so a losing concurrent start never creates a container."""
import logging
import uuid
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import admission, db
from app.auth import Principal, current_student, db_guard
from app.cli import SMOKE_COHORT
from app.content import Lab
from app.runtime import container_name, container_spec, memory_limit_mb

log = logging.getLogger("lab-manager")
T = db.SCHEMA
LIVE = "status in ('starting', 'running')"

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
    return bool(name == SMOKE_COHORT)


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


@router.post("", status_code=201)
async def start(
    body: StartBody,
    request: Request,
    response: Response,
    p: Annotated[Principal, Depends(current_student)],
) -> dict[str, Any]:
    state = request.app.state
    labs: dict[str, Lab] = state.labs
    lab = labs.get(body.lab_id)
    if lab is None:
        raise HTTPException(404, "unknown_lab")
    pool = state.pool
    if pool is None:
        raise HTTPException(503, "database unavailable")
    if lab.status == "internal" and not await _in_smoke_cohort(pool, p.subject):
        raise HTTPException(404, "unknown_lab")  # byte-identical to a missing lab (D54)
    runtime = state.runtime
    with db_guard():
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
        sid = str(row["id"])
        try:
            name = str(await runtime.start(container_spec(lab, sid)))
        except Exception as exc:
            log.error("starting container of session %s failed (%s)", sid, type(exc).__name__)
            await remove_container(runtime, sid)
            await pool.execute(f"delete from {T}.lab_sessions where id = $1", row["id"])
            raise HTTPException(502, "start_failed") from None
        marked = await pool.fetchval(
            f"update {T}.lab_sessions set status = 'running', container = $2 "
            "where id = $1 and status = 'starting' returning id",
            row["id"],
            name,
        )
        if marked is None:  # ended while the container was starting
            await remove_container(runtime, sid)
            raise HTTPException(409, "session_ended")
    return _shape({**dict(row), "status": "running"})


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
            f"update {T}.lab_sessions set last_heartbeat = now() "
            f"where id = $1 and student = $2 and {LIVE} returning id",
            sid,
            p.subject,
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
