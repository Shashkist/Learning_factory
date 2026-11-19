"""Student catalog (slice 2 of #10): published labs with the student's own progress.

`draft` and `internal` labs are never listed, not even for the smoke cohort (D54)."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.auth import Principal, current_student, db_guard
from app.content import Lab
from app.sessions import LIVE_STATUSES

T = db.SCHEMA

router = APIRouter(prefix="/api/labs")


def progress(lab: Lab, done: bool, session: Any) -> str:
    if done:
        return "done"
    if session is None:
        return "not_started"
    return f"{passed(lab, session)}/{len(lab.tasks)}"


def passed(lab: Lab, session: Any) -> int:
    """Passed tasks that lab.yaml still lists (a removed task id never counts)."""
    return len(set(session["passed_ids"]) & {t.id for t in lab.tasks})


def pick(rows: list[Any]) -> Any:
    """The live session when there is one, else the most recently started."""
    live = [r for r in rows if r["status"] in LIVE_STATUSES]
    return (live or sorted(rows, key=lambda r: r["started_at"], reverse=True))[0]


@router.get("")
async def catalog(
    request: Request, p: Annotated[Principal, Depends(current_student)]
) -> list[dict[str, Any]]:
    state = request.app.state
    pool = db.bounded(state.pool)
    if pool is None:
        raise HTTPException(503, "database unavailable")
    with db_guard():
        sessions = await pool.fetch(
            "select s.lab_id, s.id, s.status, s.started_at, "
            f"(select coalesce(array_agg(r.task_id), '{{}}') from {T}.task_results r "
            "  where r.session_id = s.id and r.passed_at is not null) as passed_ids "
            f"from {T}.lab_sessions s where s.student = $1",
            p.subject,
        )
        done = {
            r["lab_id"]
            for r in await pool.fetch(
                f"select lab_id from {T}.lab_completions where student = $1", p.subject
            )
        }
    by_lab: dict[str, list[Any]] = {}
    for r in sessions:
        by_lab.setdefault(r["lab_id"], []).append(r)
    labs = sorted(
        (x for x in state.labs.values() if x.status == "published"),
        key=lambda x: (x.module, x.lesson, x.id),
    )
    out: list[dict[str, Any]] = []
    for lab in labs:
        s = pick(by_lab[lab.id]) if lab.id in by_lab else None
        live = bool(s and s["status"] in LIVE_STATUSES)
        out.append(
            {
                "id": lab.id,
                "title": lab.title,
                "description": lab.description,
                "module": lab.module,
                "lesson": lab.lesson,
                "tasks": len(lab.tasks),
                "progress": progress(lab, lab.id in done, s),
                "session_id": str(s["id"]) if s is not None and live else None,
            }
        )
    return out
