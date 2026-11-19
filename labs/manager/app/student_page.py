"""Student page (D59 slice 3, D63, D64): one student's progress and flags, and the actions.

A student is addressed by `students.key`, an opaque random uuid: the id hash stays inside the
database and is never part of a response. An unknown or malformed key and a student of the smoke
cohort (D61) all answer 404. Every route needs an admin session.
"""
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool, field_validator

from app import auth, db
from app.auth import current_admin, db_guard
from app.classtable import cell_status, published
from app.content import Lab
from app.smoke import NOT_SMOKE_SQL
from app.teacher import Day, read_body

T = db.SCHEMA
MAX_NOTE = 1000
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
KEY_RE = re.compile(_UUID)
_TASK_FLAG = re.compile(rf"task-({_UUID})-(.+)")
_LAB_FLAG = re.compile(rf"lab-({_UUID})")

router = APIRouter(prefix="/api/admin/students", dependencies=[Depends(current_admin)])

Row = Mapping[str, Any]


class StudentEdit(BaseModel):
    """Any of the three fields; only the ones sent change (`model_fields_set`)."""

    note: Annotated[str, Field(max_length=MAX_NOTE)] | None = None
    access_until: Day | None = None
    disabled: StrictBool = False

    @field_validator("note")
    @classmethod
    def _no_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:  # Postgres refuses NUL in text
            raise ValueError("invalid note")
        return value


def clean_note(note: str | None) -> str | None:
    """Stored trimmed; blank or whitespace-only becomes null."""
    return (note or "").strip() or None


def task_flag_id(session_id: Any, task_id: str) -> str:
    return f"task-{session_id}-{task_id}"


def lab_flag_id(session_id: Any) -> str:
    return f"lab-{session_id}"


def parse_flag_id(flag_id: str) -> tuple[str, uuid.UUID, str | None] | None:
    """("task", session, task id) or ("lab", session, None); None for anything else."""
    if m := _TASK_FLAG.fullmatch(flag_id):
        return "task", uuid.UUID(m[1]), m[2]
    if m := _LAB_FLAG.fullmatch(flag_id):
        return "lab", uuid.UUID(m[1]), None
    return None


def _seconds(value: float | None) -> float | None:
    return None if value is None else round(float(value), 1)


def assemble(
    labs: list[Lab],
    student: Row,
    tasks: Iterable[Row],
    firsts: Iterable[Row],
    medians: Iterable[Row],
    done: Iterable[Row],
    flags: Iterable[Row],
) -> dict[str, Any]:
    """The page body from the query rows (no database here, so it is unit-tested)."""
    by_task = {(r["lab_id"], r["task_id"]): r for r in tasks}
    first = {(r["lab_id"], r["task_id"]): r["secs"] for r in firsts}
    median = {(r["lab_id"], r["task_id"]): r["median"] for r in medians}
    done_ids = {r["lab_id"] for r in done}
    flags_by: dict[str, list[Row]] = {}
    for f in flags:
        flags_by.setdefault(f["lab_id"], []).append(f)
    out = []
    for lab in labs:
        order = {t.id: i for i, t in enumerate(lab.tasks)}
        passed = {tid for (lid, tid), r in by_task.items() if lid == lab.id and r["passed"]}
        lab_flags = sorted(
            flags_by.get(lab.id, []),
            key=lambda f: (
                f["scope"] == "lab",
                order.get(f["task_id"], len(order)),
                str(f["task_id"]),
                str(f["session_id"]),
            ),
        )
        out.append(
            {
                "id": lab.id,
                "title": dict(lab.title),
                "status": cell_status(lab, passed, lab.id in done_ids),
                "tasks_passed": len(passed & set(order)),
                "tasks_total": len(lab.tasks),
                "attempts": sum(  # only tasks still in lab.yaml: equals the task rows shown
                    int(r["attempts"]) for (lid, tid), r in by_task.items()
                    if lid == lab.id and tid in order
                ),
                "tasks": [
                    {
                        "id": t.id,
                        "passed": t.id in passed,
                        "attempts": int(by_task[(lab.id, t.id)]["attempts"])
                        if (lab.id, t.id) in by_task
                        else 0,
                        "seconds": _seconds(first.get((lab.id, t.id))),
                        "median_seconds": _seconds(median.get((lab.id, t.id))),
                    }
                    for t in lab.tasks
                ],
                "flags": [
                    {
                        "id": task_flag_id(f["session_id"], f["task_id"])
                        if f["scope"] == "task"
                        else lab_flag_id(f["session_id"]),
                        "scope": f["scope"],
                        "task_id": f["task_id"],
                        "reason": f["fast_reason"] or "",
                        "dismissed_at": f["flag_dismissed_at"].isoformat()
                        if f["flag_dismissed_at"]
                        else None,
                    }
                    for f in lab_flags
                ],
            }
        )
    until = student["access_until"]
    return {
        "key": str(student["key"]),
        "name": student["name"],
        "cohort": student["cohort"],
        "note": student["note"],
        "disabled": student["disabled"],
        "access_until": until.isoformat() if until else None,
        "cohort_access_until": student["cohort_until"].isoformat(),
        "labs": out,
    }


_STUDENT_SQL = (
    "select s.id_hash, s.key, s.name, s.note, s.disabled, s.access_until, "
    "c.name as cohort, c.access_until as cohort_until "
    f"from {T}.students s join {T}.cohorts c on c.id = s.cohort_id "
    f"where {NOT_SMOKE_SQL} and s.key = $1"
)

_TASKS_SQL = (
    "select lab_id, task_id, sum(attempts)::int as attempts, "
    "bool_or(passed_at is not null) as passed "
    f"from {T}.task_results where student = $1 and lab_id = any($2) group by lab_id, task_id"
)

# A first completion is the earliest passed result of a task (several sessions count once).
_FIRSTS_SQL = (
    "select distinct on (lab_id, task_id) lab_id, task_id, "
    "extract(epoch from passed_at - shown_at)::float8 as secs "
    f"from {T}.task_results where student = $1 and lab_id = any($2) and passed_at is not null "
    "order by lab_id, task_id, passed_at"
)

# Every student's first completion, all cohorts but the smoke cohort, no minimum count (D64).
_MEDIANS_SQL = f"""
with firsts as (
  select distinct on (r.student, r.lab_id, r.task_id) r.lab_id, r.task_id,
         extract(epoch from r.passed_at - r.shown_at)::float8 as secs
  from {T}.task_results r
  join {T}.students s on s.id_hash = r.student
  join {T}.cohorts c on c.id = s.cohort_id
  where {NOT_SMOKE_SQL} and r.passed_at is not null and r.lab_id = any($1)
  order by r.student, r.lab_id, r.task_id, r.passed_at
)
select lab_id, task_id, percentile_cont(0.5) within group (order by secs) as median
from firsts where secs is not null group by lab_id, task_id
"""

_DONE_SQL = f"select lab_id from {T}.lab_completions where student = $1 and lab_id = any($2)"

_FLAGS_SQL = (
    "select 'task' as scope, lab_id, session_id, task_id, fast_reason, flag_dismissed_at "
    f"from {T}.task_results where student = $1 and lab_id = any($2) and fast_flag "
    "union all "
    "select 'lab', lab_id, session_id, null, fast_reason, flag_dismissed_at "
    f"from {T}.lab_runs where student = $1 and lab_id = any($2) and fast_flag"
)


async def _student(pool: Any, key: str) -> Row:
    """The student behind a key; 404 for a malformed or unknown key and for the smoke cohort."""
    if not KEY_RE.fullmatch(key):
        raise HTTPException(404, "unknown_student")
    row = await pool.fetchrow(_STUDENT_SQL, uuid.UUID(key))
    if row is None:
        raise HTTPException(404, "unknown_student")
    return dict(row)


async def _page(request: Request, pool: Any, key: str) -> dict[str, Any]:
    labs = published(request.app.state.labs)
    ids = [lab.id for lab in labs]
    s = await _student(pool, key)
    h = s["id_hash"]
    return assemble(
        labs,
        s,
        await pool.fetch(_TASKS_SQL, h, ids),
        await pool.fetch(_FIRSTS_SQL, h, ids),
        await pool.fetch(_MEDIANS_SQL, ids),
        await pool.fetch(_DONE_SQL, h, ids),
        await pool.fetch(_FLAGS_SQL, h, ids),
    )


@router.get("/{key}")
async def student_page(key: str, request: Request) -> dict[str, Any]:
    pool = auth._pool(request)
    with db_guard():
        return await _page(request, pool, key)


@router.patch("/{key}")
async def edit_student(key: str, request: Request) -> dict[str, Any]:
    pool = auth._pool(request)
    with db_guard():
        s = await _student(pool, key)  # an unknown key is a 404 before the body is read
    edit = await read_body(request, StudentEdit)
    fields: dict[str, Any] = {}
    if "note" in edit.model_fields_set:
        fields["note"] = clean_note(edit.note)
    if "access_until" in edit.model_fields_set:
        fields["access_until"] = edit.access_until
    if "disabled" in edit.model_fields_set:
        fields["disabled"] = edit.disabled
    with db_guard():
        if fields:  # column names come from the fixed keys above, never from the request
            sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, 2))
            await pool.execute(
                f"update {T}.students set {sets} where id_hash = $1", s["id_hash"], *fields.values()
            )
        return await _page(request, pool, key)


@router.post("/{key}/flags/{flag_id}/dismiss")
async def dismiss_flag(key: str, flag_id: str, request: Request) -> dict[str, Any]:
    pool = auth._pool(request)
    ids = [lab.id for lab in published(request.app.state.labs)]
    with db_guard():
        s = await _student(pool, key)
        parsed = parse_flag_id(flag_id)
        if parsed is None:
            raise HTTPException(404, "unknown_flag")
        scope, session_id, task_id = parsed
        # Scoped to this student's own flag rows: another student's flag id matches nothing.
        if scope == "task":
            hit = await pool.fetchval(
                f"update {T}.task_results set flag_dismissed_at = coalesce(flag_dismissed_at, "
                "now()) where student = $1 and session_id = $2 and task_id = $3 and fast_flag "
                "and lab_id = any($4) returning 1",
                s["id_hash"], session_id, task_id, ids,
            )
        else:
            hit = await pool.fetchval(
                f"update {T}.lab_runs set flag_dismissed_at = coalesce(flag_dismissed_at, now()) "
                "where student = $1 and session_id = $2 and fast_flag and lab_id = any($3) "
                "returning 1",
                s["id_hash"], session_id, ids,
            )
        if not hit:
            raise HTTPException(404, "unknown_flag")
        return await _page(request, pool, key)
