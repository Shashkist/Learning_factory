"""Two tasks passing at once complete the lab exactly once (needs LF_TEST_DATABASE_URL)."""
import asyncio
import datetime as dt
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from app import db, tasks
from app.content import Lab, Task

T = db.SCHEMA
STUDENT = "f" * 64  # fake id hash


def _lab(ids: list[str]) -> Lab:
    return Lab(
        id="race", title={}, status="published", image="term", module=1, lesson=6,
        path=Path("."), tasks=[Task(id=i, kind="quiz", correct=0) for i in ids],
    )


async def _race(dsn: str, ids: list[str]) -> tuple[list[object], list[object]]:
    conn = await db.connect(dsn)
    try:
        await conn.execute(f"drop schema if exists {T} cascade")
        await conn.execute(f"create schema {T}")
    finally:
        await conn.close()
    await db.migrate(dsn)
    pool = await db.create_pool(dsn)
    try:
        sid = uuid.uuid4()
        today = dt.datetime.now(dt.UTC).date()
        cohort = await pool.fetchval(
            f"insert into {T}.cohorts (name, password_hash, access_until) "
            "values ('race', 'x', $1) returning id",
            today + dt.timedelta(days=30),
        )
        await pool.execute(
            f"insert into {T}.students (id_hash, name, cohort_id) values ($1, 'Fake', $2)",
            STUDENT, cohort,
        )
        await pool.execute(
            f"insert into {T}.lab_sessions (id, student, lab_id, status) "
            "values ($1, $2, 'race', 'running')",
            sid, STUDENT,
        )
        for i in ids:
            await pool.execute(
                f"insert into {T}.task_results (student, lab_id, session_id, task_id, shown_at) "
                "values ($1, 'race', $2, $3, now())",
                STUDENT, sid, i,
            )
        lab = _lab(ids)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool)))
        ctx = tasks._Ctx(request, sid, STUDENT, lab, None)  # type: ignore[arg-type]
        await asyncio.gather(*(tasks._count_attempt(ctx, t, True) for t in lab.tasks))
        completions = await pool.fetch(f"select * from {T}.lab_completions")
        runs = await pool.fetch(f"select * from {T}.lab_runs")
        return list(completions), list(runs)
    finally:
        await pool.close()


@pytest.mark.parametrize("ids", [["a", "b"], ["a", "b", "c"]])
def test_concurrent_passes_write_exactly_one_completion(ids: list[str]) -> None:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set (no Postgres available)")
    if urlsplit(dsn).hostname not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail("LF_TEST_DATABASE_URL must point at localhost: this test drops a schema")
    for _ in range(5):
        completions, runs = asyncio.run(_race(dsn, ids))
        assert len(completions) == 1 and len(runs) == 1
