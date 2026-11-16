"""Teacher API (D59, slice 1): cohorts and paste import. Every route needs an admin session.

Raw ids and the pasted text live in memory only while a request is handled: ids are hashed,
nothing of the paste is logged or stored, and error messages never repeat an id.
"""
import os
import re
from datetime import date
from typing import Annotated, Any, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, BeforeValidator, Field

from app import auth, db
from app.auth import current_admin, db_guard
from app.cli import SMOKE_COHORT

T = db.SCHEMA
# The reserved smoke cohort (D36/D54) is left out of every teacher query that joins `cohorts c`.
NOT_SMOKE = f"lower(trim(c.name)) <> '{SMOKE_COHORT}'"

router = APIRouter(prefix="/api/admin", dependencies=[Depends(current_admin)])


def printable(value: str) -> bool:
    """No control or bidi-format characters (Postgres refuses NUL; the rest garble the tables)."""
    return value.isprintable()


def _name(value: str) -> str:
    value = value.strip()
    if not value or value.lower() == SMOKE_COHORT or not printable(value):
        raise ValueError("invalid name")
    return value


def _date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("expected YYYY-MM-DD")
    return date.fromisoformat(value)


MAX_NAME = 200
Name = Annotated[str, Field(max_length=MAX_NAME), AfterValidator(_name)]
Password = Annotated[str, Field(min_length=1, max_length=1000)]
Day = Annotated[date, BeforeValidator(_date)]


class NewCohort(BaseModel):
    name: Name
    password: Password
    access_until: Day


class CohortEdit(BaseModel):
    name: Name | None = None
    password: Password | None = None
    access_until: Day | None = None


class ImportBody(BaseModel):
    text: str = Field(max_length=200_000)


def _cohort(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "access_until": row["access_until"].isoformat(),
        "students": row["students"],
    }


_COHORT_SQL = (
    "select c.id, c.name, c.access_until, "
    f"(select count(*) from {T}.students s where s.cohort_id = c.id)::int as students "
    f"from {T}.cohorts c where {NOT_SMOKE}"
)


async def _one(pool: Any, cohort_id: int) -> Any:
    row = await pool.fetchrow(f"{_COHORT_SQL} and c.id = $1", cohort_id)
    if row is None:
        raise HTTPException(404, "unknown_cohort")
    return row


@router.get("/cohorts")
async def list_cohorts(request: Request) -> list[dict[str, Any]]:
    with db_guard():
        rows = await auth._pool(request).fetch(f"{_COHORT_SQL} order by c.id")
    return [_cohort(r) for r in rows]


@router.post("/cohorts", status_code=201)
async def create_cohort(body: NewCohort, request: Request) -> dict[str, Any]:
    pool = auth._pool(request)
    pw_hash = await auth.hash_password_bounded(body.password)
    with db_guard():
        cid = await pool.fetchval(
            f"insert into {T}.cohorts (name, password_hash, access_until) "
            "values ($1, $2, $3) returning id",
            body.name, pw_hash, body.access_until,
        )
        return _cohort(await _one(pool, cid))


@router.patch("/cohorts/{cohort_id}")
async def edit_cohort(cohort_id: int, body: CohortEdit, request: Request) -> dict[str, Any]:
    pool = auth._pool(request)
    with db_guard():
        await _one(pool, cohort_id)  # before any argon2 work
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.access_until is not None:
        fields["access_until"] = body.access_until
    if body.password is not None:
        fields["password_hash"] = await auth.hash_password_bounded(body.password)
    with db_guard():
        if fields:  # column names come from the fixed keys above, never from the request
            sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, 2))
            await pool.execute(
                f"update {T}.cohorts set {sets} where id = $1", cohort_id, *fields.values()
            )
        return _cohort(await _one(pool, cohort_id))


class Line(NamedTuple):
    line: int
    id: str  # as pasted: only ever hashed
    name: str


def split_lines(text: str) -> list[Line]:
    """The non-blank lines "ID, name" with their 1-based line numbers; split at the first comma."""
    out = []
    for n, raw in enumerate(text.split("\n"), 1):
        if raw.strip():
            sid, _, name = raw.partition(",")
            out.append(Line(n, sid, name.strip()))
    return out


def _error(line: int, code: str, message: str) -> dict[str, Any]:
    # The UI shows the line number itself; the message never repeats the id.
    return {"line": line, "status": "error", "code": code, "message": message}


def check_lines(lines: list[Line]) -> dict[int, dict[str, Any]]:
    """Per-line errors found without the database (invalid id, missing name, duplicate)."""
    errors: dict[int, dict[str, Any]] = {}
    seen: set[str] = set()
    for ln in lines:
        if not auth.valid_id(ln.id):
            errors[ln.line] = _error(ln.line, "invalid_id", "the ID is not valid.")
        elif not ln.name:
            errors[ln.line] = _error(ln.line, "missing_name", "the name is missing.")
        elif len(ln.name) > MAX_NAME or not printable(ln.name):
            errors[ln.line] = _error(
                ln.line, "invalid_name", "the name is too long or has control characters."
            )
        elif (norm := auth.normalize(ln.id)) in seen:
            errors[ln.line] = _error(ln.line, "duplicate_in_paste", "same ID as an earlier line.")
        else:
            seen.add(norm)
    return errors


@router.post("/cohorts/{cohort_id}/import")
async def import_students(cohort_id: int, body: ImportBody, request: Request) -> dict[str, Any]:
    pool = auth._pool(request)
    secret = os.environ.get("LF_ID_SECRET")
    if not secret:
        raise HTTPException(503, "login not configured")
    lines = split_lines(body.text)
    errors = check_lines(lines)
    good = [(auth.id_hash(ln.id, secret), ln) for ln in lines if ln.line not in errors]
    result: dict[int, dict[str, Any]] = dict(errors)
    with db_guard():
        await _one(pool, cohort_id)
        async with pool.acquire() as conn, conn.transaction():
            # In id_hash order: overlapping imports lock rows in the same order (no deadlock).
            for h, ln in sorted(good, key=lambda g: g[0]):
                saved = await conn.fetchval(
                    f"insert into {T}.students (id_hash, name, cohort_id) values ($1, $2, $3) "
                    "on conflict (id_hash) do nothing returning 1",
                    h, ln.name, cohort_id,
                )
                result[ln.line] = (
                    {"line": ln.line, "status": "added"} if saved
                    else _error(ln.line, "already_exists", "this ID is already a student.")
                )
    rows = [result[ln.line] for ln in lines]
    return {"added": sum(r["status"] == "added" for r in rows), "rows": rows}


@router.get("/cohorts/{cohort_id}/students")
async def list_students(cohort_id: int, request: Request) -> list[dict[str, Any]]:
    pool = auth._pool(request)
    with db_guard():
        await _one(pool, cohort_id)
        rows = await pool.fetch(
            f"select name, note, disabled, access_until from {T}.students "
            "where cohort_id = $1 order by name",
            cohort_id,
        )
    return [
        {
            "name": r["name"],
            "note": r["note"],
            "disabled": r["disabled"],
            "access_until": r["access_until"].isoformat() if r["access_until"] else None,
        }
        for r in rows
    ]
