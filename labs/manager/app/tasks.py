"""Task engine, slice 1 (D37): the task list, showing a task (setup, shown_at) and navigation.

Showing a hands-on task runs its `setup.sh` as root in the session's container (D45) before
anything is recorded; Next/Skip/Previous/Restart all show a target the same way and never reset
the machine (spec section 5). Check results, attempts and quiz grading are #88; completion is #89.
Nothing sent to a student carries check/setup/solution text or a quiz `correct` field."""
import asyncio
import logging
import math
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictInt

from app import db
from app.auth import Principal, current_student, db_guard
from app.content import LANGS, Lab, Task, parse_check_output
from app.flags import fast_lab_reason, fast_task_reason
from app.runtime import container_name
from app.term_proxy import authorize

log = logging.getLogger("lab-manager")
T = db.SCHEMA
DEFAULT_SETUP_TIMEOUT_S = 50.0  # below nginx's default proxy_read_timeout (60 s)
CHECK_TIMEOUT_S = 20.0  # spec section 7
SLOT_WAIT_S = 5.0  # waiting for an exec slot, on top of the setup timeout


def _max_execs() -> int:
    try:
        return max(1, int(os.environ.get("LF_MAX_EXECS", "")))
    except ValueError:
        return 8


_EXECS = asyncio.Semaphore(_max_execs())
_LOCKS: dict[uuid.UUID, asyncio.Lock] = {}  # one manager process (D23): setups per session

router = APIRouter(prefix="/api/sessions/{session}/tasks")


def setup_timeout() -> float:
    try:
        value = float(os.environ.get("LF_SETUP_TIMEOUT_S", ""))
    except ValueError:
        return DEFAULT_SETUP_TIMEOUT_S
    return value if value > 0 else DEFAULT_SETUP_TIMEOUT_S


def _read(lab: Lab, task: Task, name: str) -> str:
    return (lab.path / "tasks" / task.id / name).read_text(encoding="utf-8")


def _title(lab: Lab, task: Task) -> dict[str, str]:
    if task.kind == "quiz":
        return dict(task.question)
    out: dict[str, str] = {}
    for lang in LANGS:
        m = re.search(r"^#+[ \t]*(.+?)[ \t]*$", _read(lab, task, f"task.{lang}.md"), re.M)
        out[lang] = m.group(1) if m else task.id
    return out


def _state(row: Any) -> str:
    if row is None or row["shown_at"] is None:
        return "not_started"
    return "passed" if row["passed_at"] is not None else "shown"


def _detail(lab: Lab, task: Task, row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": task.id,
        "index": lab.tasks.index(task) + 1,
        "total": len(lab.tasks),
        "kind": task.kind,
        "title": _title(lab, task),
        "state": _state(row),
    }
    if task.kind == "quiz":
        out["question"] = dict(task.question)
        out["options"] = [dict(o) for o in task.options]
    else:
        out["text"] = {lang: _read(lab, task, f"task.{lang}.md") for lang in LANGS}
        out["checklist"] = [dict(i) for i in task.checklist]
    return out


class _Ctx:
    def __init__(self, request: Request, sid: uuid.UUID, student: str, lab: Lab, current: Any):
        self.request, self.sid, self.student, self.lab = request, sid, student, lab
        self.current: str | None = current
        self.pool = request.app.state.pool

    def task(self, task_id: str | None) -> Task | None:
        return next((t for t in self.lab.tasks if t.id == task_id), None)

    async def result(self, task: Task) -> Any:
        with db_guard():
            return await self.pool.fetchrow(
                f"select shown_at, passed_at from {T}.task_results "
                "where session_id = $1 and task_id = $2",
                self.sid,
                task.id,
            )


async def _ctx(
    session: str, request: Request, p: Annotated[Principal, Depends(current_student)]
) -> _Ctx:
    row = await authorize(request, session, p)
    sid = uuid.UUID(session)
    if row["status"] == "starting":
        raise HTTPException(409, "session_starting")
    if row["status"] != "running":
        raise HTTPException(410, "session_ended")
    lab = request.app.state.labs.get(row["lab_id"])
    if lab is None:
        raise HTTPException(404, "unknown_lab")
    return _Ctx(request, sid, p.subject, lab, row["current_task"])


@asynccontextmanager
async def _session_lock(sid: uuid.UUID, busy: str) -> AsyncIterator[None]:
    """The per-session lock, never waited for: if it is held the caller gets 409 `busy`."""
    lock = _LOCKS.setdefault(sid, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, busy)
    try:
        async with lock:
            yield
    finally:
        if not lock.locked() and _LOCKS.get(sid) is lock:
            del _LOCKS[sid]


async def _show(ctx: _Ctx, task: Task, moving_from: str | None = None) -> dict[str, Any]:
    """Serialised per session: a second show while one runs gets 409 and runs nothing.

    `moving_from` (navigation): the current task the target was computed from; if another
    request changed it before this one got the lock, answer 409 and run nothing."""
    async with _session_lock(ctx.sid, "setup_running"):
        if moving_from is not None:
            with db_guard():
                now = await ctx.pool.fetchval(
                    f"select current_task from {T}.lab_sessions where id = $1", ctx.sid
                )
            if now != moving_from:
                raise HTTPException(409, "task_changed")
        return await _do_show(ctx, task)


async def _do_show(ctx: _Ctx, task: Task) -> dict[str, Any]:
    """Run the setup (hands-on), then record shown_at (first time only) and the current task."""
    if task.kind == "hands-on":
        script = (ctx.lab.path / "tasks" / task.id / "setup.sh").read_bytes().decode("utf-8")
        try:
            await asyncio.wait_for(_EXECS.acquire(), SLOT_WAIT_S)
        except TimeoutError:
            log.error("setup of %s in session %s found no free exec slot", task.id, ctx.sid)
            raise HTTPException(
                504, {"code": "setup_timeout", "message": "The server is busy; try again."}
            ) from None
        try:
            res = await ctx.request.app.state.runtime.exec_script(
                container_name(ctx.sid), script, setup_timeout()
            )
        except Exception as exc:
            log.error("setup of %s in session %s failed (%s)", task.id, ctx.sid, type(exc).__name__)
            raise HTTPException(
                502, {"code": "setup_failed", "message": "The task could not be set up."}
            ) from None
        finally:
            _EXECS.release()
        if res.timed_out:
            log.error("setup of %s in session %s timed out", task.id, ctx.sid)
            raise HTTPException(
                504, {"code": "setup_timeout", "message": "Setting up the task took too long."}
            )
        if res.exit_code != 0:
            log.error("setup of %s in session %s exited %s", task.id, ctx.sid, res.exit_code)
            raise HTTPException(
                502, {"code": "setup_failed", "message": "The task could not be set up."}
            )
    with db_guard():
        async with ctx.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"insert into {T}.task_results (student, lab_id, session_id, task_id, shown_at) "
                "values ($1, $2, $3, $4, now()) on conflict (session_id, task_id) "
                "do update set shown_at = coalesce(task_results.shown_at, now()) "
                "returning shown_at, passed_at",
                ctx.student,
                ctx.lab.id,
                ctx.sid,
                task.id,
            )
            await conn.execute(
                f"update {T}.lab_sessions set current_task = $2 where id = $1", ctx.sid, task.id
            )
    ctx.current = task.id
    return _detail(ctx.lab, task, row)


@router.get("")
async def task_list(ctx: Annotated[_Ctx, Depends(_ctx)]) -> dict[str, Any]:
    with db_guard():
        rows = await ctx.pool.fetch(
            f"select task_id, shown_at, passed_at from {T}.task_results where session_id = $1",
            ctx.sid,
        )
    by_id = {r["task_id"]: r for r in rows}
    return {
        "lab_id": ctx.lab.id,
        "current": ctx.current if ctx.task(ctx.current) else None,
        "tasks": [
            {
                "id": t.id,
                "index": i,
                "kind": t.kind,
                "title": _title(ctx.lab, t),
                "state": _state(by_id.get(t.id)),
            }
            for i, t in enumerate(ctx.lab.tasks, 1)
        ],
    }


def _current(ctx: _Ctx) -> Task:
    task = ctx.task(ctx.current)
    if task is None:
        raise HTTPException(409, "no_current_task")
    return task


@router.get("/current")
async def current_task(ctx: Annotated[_Ctx, Depends(_ctx)]) -> dict[str, Any]:
    task = ctx.task(ctx.current)
    if task is None:
        raise HTTPException(404, "no_current_task")
    return _detail(ctx.lab, task, await ctx.result(task))


@router.post("/current/{action}")
async def navigate(action: str, ctx: Annotated[_Ctx, Depends(_ctx)]) -> dict[str, Any]:
    offsets = {"next": 1, "skip": 1, "previous": -1, "restart": 0}
    if action not in offsets:
        raise HTTPException(404, "unknown_action")
    here = ctx.lab.tasks.index(_current(ctx))
    target = here + offsets[action]
    if target >= len(ctx.lab.tasks):
        raise HTTPException(409, "no_next_task")
    if target < 0:
        raise HTTPException(409, "no_previous_task")
    return await _show(ctx, ctx.lab.tasks[target], moving_from=ctx.lab.tasks[here].id)


@router.post("/{task_id}/show")
async def show_task(task_id: str, ctx: Annotated[_Ctx, Depends(_ctx)]) -> dict[str, Any]:
    task = ctx.task(task_id)
    if task is None:
        raise HTTPException(404, "unknown_task")
    return await _show(ctx, task)


async def _complete(conn: Any, ctx: _Ctx) -> None:
    """If every task of the lab has passed in this session, record the completion (once) and
    the session's lab run. Runs in the transaction of the passing task."""
    done = await conn.fetchval(
        f"select count(*) from {T}.task_results "
        "where session_id = $1 and passed_at is not null and task_id = any($2)",
        ctx.sid,
        [t.id for t in ctx.lab.tasks],
    )
    if done != len(ctx.lab.tasks):
        return
    first = (
        await conn.fetchval(
            f"insert into {T}.lab_completions (student, lab_id) values ($1, $2) "
            "on conflict do nothing returning 1",
            ctx.student,
            ctx.lab.id,
        )
        is not None
    )
    active = float(
        await conn.fetchval(f"select active_seconds from {T}.lab_sessions where id = $1", ctx.sid)
    )
    priors: list[int] = []
    if first:
        rows = await conn.fetch(  # runs without heartbeat data (0 s) say nothing about the class
            f"select active_seconds from {T}.lab_runs "
            "where lab_id = $1 and first_completion and student <> $2 and active_seconds > 0",
            ctx.lab.id,
            ctx.student,
        )
        priors = [r["active_seconds"] for r in rows]
    reason = fast_lab_reason(active, ctx.lab.fast_lab_min, priors, repeat=not first)
    fast = reason is not None
    await conn.execute(
        f"insert into {T}.lab_runs (student, lab_id, session_id, first_completion, "
        "active_seconds, fast_flag, fast_reason) values ($1, $2, $3, $4, $5, $6, $7) "
        "on conflict (session_id) do nothing",
        ctx.student,
        ctx.lab.id,
        ctx.sid,
        first,
        math.ceil(active),  # whole seconds, never below the session's own total
        fast,
        reason,
    )


async def _count_attempt(ctx: _Ctx, task: Task, passed: bool) -> int:
    """One atomic increment; `passed_at` only on the first pass, never cleared. The first pass
    also sets the task's fast flag and may complete the lab, in the same transaction."""
    with db_guard():
        async with ctx.pool.acquire() as conn, conn.transaction():
            # lock order: task row, then session row (as in _do_show). The session lock
            # serialises completions per session (two tasks passing at once each see the
            # other's commit) and keeps End-lab from slipping in between.
            old = await conn.fetchrow(
                f"select passed_at, extract(epoch from now() - shown_at) as secs "
                f"from {T}.task_results where session_id = $1 and task_id = $2 "
                "and shown_at is not null for update",
                ctx.sid,
                task.id,
            )
            if old is not None and await conn.fetchval(
                f"select 1 from {T}.lab_sessions where id = $1 and status = 'running' "
                "for no key update",
                ctx.sid,
            ) is None:
                old = None
            attempts = None
            if old is not None:
                first_pass = passed and old["passed_at"] is None
                fast, reason = False, None
                if first_pass:
                    repeat = await conn.fetchval(
                        f"select exists (select 1 from {T}.lab_completions "
                        "where student = $1 and lab_id = $2)",
                        ctx.student,
                        ctx.lab.id,
                    )
                    secs = float(old["secs"])
                    reason = fast_task_reason(
                        task.id, task.kind, secs, task.min_task_seconds, repeat
                    )
                    fast = reason is not None
                attempts = await conn.fetchval(
                    f"update {T}.task_results set attempts = attempts + 1, "
                    "passed_at = case when $3 then now() else passed_at end, "
                    "fast_flag = case when $3 then $4 else fast_flag end, "
                    "fast_reason = case when $3 then $5 else fast_reason end "
                    "where session_id = $1 and task_id = $2 returning attempts",
                    ctx.sid,
                    task.id,
                    first_pass,
                    fast,
                    reason,
                )
                if first_pass:
                    await _complete(conn, ctx)
    if attempts is None:
        with db_guard():
            status = await ctx.pool.fetchval(
                f"select status from {T}.lab_sessions where id = $1", ctx.sid
            )
        if status != "running":
            raise HTTPException(410, "session_ended")
        raise HTTPException(409, "task_not_shown")
    return int(attempts)


async def _gradable(ctx: _Ctx, task_id: str, kind: str) -> Task:
    task = ctx.task(task_id)
    if task is None:
        raise HTTPException(404, "unknown_task")
    if task.kind != kind:
        raise HTTPException(409, "wrong_task_kind")
    row = await ctx.result(task)
    if row is None or row["shown_at"] is None:
        raise HTTPException(409, "task_not_shown")
    return task


async def _run_check(ctx: _Ctx, task: Task) -> tuple[str | None, str, bool]:
    """(reason, output, exited 0); the reason is "timeout" or "error" when nothing was read."""
    script = (ctx.lab.path / "tasks" / task.id / "check.sh").read_bytes().decode("utf-8")
    try:
        await asyncio.wait_for(_EXECS.acquire(), SLOT_WAIT_S)
    except TimeoutError:
        log.error("check of %s in session %s found no free exec slot", task.id, ctx.sid)
        return "error", "", False
    try:
        res = await ctx.request.app.state.runtime.exec_script(
            container_name(ctx.sid), script, CHECK_TIMEOUT_S
        )
    except Exception as exc:
        log.error("check of %s in session %s failed (%s)", task.id, ctx.sid, type(exc).__name__)
        return "error", "", False
    finally:
        _EXECS.release()
    if res.timed_out:
        log.error("check of %s in session %s timed out", task.id, ctx.sid)
        return "timeout", "", False
    return None, res.output, res.exit_code == 0


@router.post("/{task_id}/check")
async def check_task(task_id: str, ctx: Annotated[_Ctx, Depends(_ctx)]) -> dict[str, Any]:
    task = await _gradable(ctx, task_id, "hands-on")
    async with _session_lock(ctx.sid, "session_busy"):
        reason, output, exit_ok = await _run_check(ctx, task)
        res = parse_check_output(output, 0 if exit_ok else 1)
        items = [
            {
                "n": n,
                "text": dict(text),
                "passed": n in res.items and res.items[n].passed,
                "hint": res.items[n].hint if n in res.items else None,
            }
            for n, text in enumerate(task.checklist, 1)
        ]
        passed = res.passed and all(i["passed"] for i in items)
        if reason == "error":  # the server's failure, not the student's: no attempt
            with db_guard():
                attempts = await ctx.pool.fetchval(
                    f"select attempts from {T}.task_results where session_id = $1 and task_id = $2",
                    ctx.sid,
                    task.id,
                )
            attempts = int(attempts or 0)
        else:
            attempts = await _count_attempt(ctx, task, passed)
    return {"passed": passed, "attempts": attempts, "reason": reason, "checklist": items}


class _Answer(BaseModel):
    option: StrictInt


@router.post("/{task_id}/answer")
async def answer_task(
    task_id: str, body: _Answer, ctx: Annotated[_Ctx, Depends(_ctx)]
) -> dict[str, Any]:
    task = await _gradable(ctx, task_id, "quiz")
    if not 0 <= body.option < len(task.options):
        raise HTTPException(422, "invalid_option")
    correct = body.option == task.correct
    return {"correct": correct, "attempts": await _count_attempt(ctx, task, correct)}
