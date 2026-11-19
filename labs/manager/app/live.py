"""Live sessions (D59 slice 4, D61): who is in a lab right now, and ending a session.

A session is addressed by its own random id. The student's id hash never leaves the database,
the smoke cohort is left out, and ending goes through the same `end_session` as the student's
End lab (reason `forced`: ended by someone else). Every route needs an admin session.
"""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app import auth, db
from app.auth import current_admin, db_guard
from app.sessions import LIVE, end_session
from app.smoke import NOT_SMOKE_SQL

T = db.SCHEMA

router = APIRouter(prefix="/api/admin/live", dependencies=[Depends(current_admin)])

LIVE_L = "l." + LIVE

_FROM = (
    f"from {T}.lab_sessions l join {T}.students s on s.id_hash = l.student "
    f"join {T}.cohorts c on c.id = s.cohort_id where {NOT_SMOKE_SQL}"
)


def _stamp(value: Any) -> str | None:
    return value.isoformat() if value else None


@router.get("")
async def live_sessions(request: Request) -> dict[str, Any]:
    pool = auth._pool(request)
    with db_guard():
        rows = await pool.fetch(
            "select l.id, s.key, s.name, c.name as cohort, l.lab_id, l.started_at, "
            f"l.last_heartbeat {_FROM} and {LIVE_L} order by l.started_at desc, l.id"
        )
    labs = request.app.state.labs
    return {
        "sessions": [
            {
                "id": str(r["id"]),
                "key": str(r["key"]),
                "name": r["name"],
                "cohort": r["cohort"],
                "lab_id": r["lab_id"],
                "lab_title": dict(labs[r["lab_id"]].title) if r["lab_id"] in labs else None,
                "started_at": _stamp(r["started_at"]),
                "last_heartbeat": _stamp(r["last_heartbeat"]),
            }
            for r in rows
        ]
    }


@router.post("/{session_id}/end", status_code=204)
async def end_live_session(session_id: str, request: Request) -> Response:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(404, "no_session") from None
    pool = auth._pool(request)
    with db_guard():
        found = await pool.fetchval(f"select l.id {_FROM} and l.id = $1", sid)
        if found is None or not await end_session(pool, request.app.state.runtime, sid, "forced"):
            raise HTTPException(404, "no_session")
    return Response(status_code=204)
