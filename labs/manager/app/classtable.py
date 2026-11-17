"""Class table (D59, slice 2 of #11): students down the side, published labs across the top.

Read-only. Rows carry the name and the cohort name only, never an id or a hash. The smoke
cohort (D61) is left out of every query here.
"""
import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app import auth, db
from app.auth import current_admin, db_guard
from app.content import Lab
from app.smoke import NOT_SMOKE_SQL

T = db.SCHEMA

router = APIRouter(prefix="/api/admin", dependencies=[Depends(current_admin)])


def cell_status(lab: Lab, passed_ids: set[str], done: bool) -> str:
    """`done`, `n/m` (n >= 1, tasks still in lab.yaml only) or `none`."""
    if done:
        return "done"
    n = len(passed_ids & {t.id for t in lab.tasks})
    return f"{n}/{len(lab.tasks)}" if n else "none"


def published(labs: dict[str, Lab]) -> list[Lab]:
    found = [lab for lab in labs.values() if lab.status == "published"]
    return sorted(found, key=lambda lab: (lab.module, lab.lesson, lab.id))


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(text: str) -> str:
    """Keep a pasted name from running as a spreadsheet formula (CSV injection)."""
    return "'" + text if text.startswith(_FORMULA_START) else text


def csv_text(table: dict[str, Any]) -> str:
    ids = [lab["id"] for lab in table["labs"]]
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["name", "cohort", "last_activity", *ids])
    for row in table["rows"]:
        cells = [
            row["cells"][i]["status"] + (" ⚡" if row["cells"][i]["flag"] else "") for i in ids
        ]
        writer.writerow(
            [safe_cell(row["name"]), safe_cell(row["cohort"]), row["last_activity"] or "", *cells]
        )
    return out.getvalue()


async def build(request: Request, cohort: int | None, flagged: int | None) -> dict[str, Any]:
    pool = auth._pool(request)
    labs = published(request.app.state.labs)
    lab_ids = [lab.id for lab in labs]
    with db_guard():
        if cohort is not None and not await pool.fetchval(
            f"select 1 from {T}.cohorts c where {NOT_SMOKE_SQL} and c.id = $1", cohort
        ):
            raise HTTPException(404, "unknown_cohort")
        students = await pool.fetch(
            f"select s.id_hash, s.name, c.name as cohort from {T}.students s "
            f"join {T}.cohorts c on c.id = s.cohort_id where {NOT_SMOKE_SQL} "
            "and ($1::int is null or c.id = $1) order by c.name, s.name, s.id_hash",
            cohort,
        )
        keys = [s["id_hash"] for s in students]
        passed = await pool.fetch(
            f"select distinct student, lab_id, task_id from {T}.task_results "
            "where passed_at is not null and student = any($1) and lab_id = any($2)",
            keys,
            lab_ids,
        )
        done = await pool.fetch(
            f"select student, lab_id from {T}.lab_completions "
            "where student = any($1) and lab_id = any($2)",
            keys,
            lab_ids,
        )
        flags = await pool.fetch(
            f"select student, lab_id from {T}.task_results where fast_flag "
            "and flag_dismissed_at is null and student = any($1) and lab_id = any($2) "
            f"union select student, lab_id from {T}.lab_runs where fast_flag "
            "and flag_dismissed_at is null and student = any($1) and lab_id = any($2)",
            keys,
            lab_ids,
        )
        seen = await pool.fetch(
            "select student, max(greatest(started_at, last_heartbeat, ended_at)) as at "
            f"from {T}.lab_sessions where student = any($1) group by student",
            keys,
        )
    passed_by: dict[tuple[str, str], set[str]] = {}
    for r in passed:
        passed_by.setdefault((r["student"], r["lab_id"]), set()).add(r["task_id"])
    done_set = {(r["student"], r["lab_id"]) for r in done}
    flag_set = {(r["student"], r["lab_id"]) for r in flags}
    last = {r["student"]: r["at"] for r in seen}
    rows = []
    for s in students:
        key = s["id_hash"]
        cells = {
            lab.id: {
                "status": cell_status(
                    lab, passed_by.get((key, lab.id), set()), (key, lab.id) in done_set
                ),
                "flag": (key, lab.id) in flag_set,
            }
            for lab in labs
        }
        if flagged == 1 and not any(c["flag"] for c in cells.values()):
            continue
        at = last.get(key)
        rows.append(
            {
                "name": s["name"],
                "cohort": s["cohort"],
                "last_activity": at.isoformat() if at else None,
                "cells": cells,
            }
        )
    return {"labs": [{"id": lab.id, "title": dict(lab.title)} for lab in labs], "rows": rows}


@router.get("/class")
async def class_table(
    request: Request, cohort: int | None = None, flagged: int | None = None
) -> dict[str, Any]:
    return await build(request, cohort, flagged)


@router.get("/class.csv")
async def class_csv(
    request: Request, cohort: int | None = None, flagged: int | None = None
) -> Response:
    text = csv_text(await build(request, cohort, flagged))
    return Response(
        # The BOM makes Excel on Windows read the file as UTF-8 (Hebrew names, the bolt).
        text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="class.csv"'},
    )
