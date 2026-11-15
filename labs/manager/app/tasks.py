"""Task engine, slice 1 (D37): the task list, showing a task (setup, shown_at) and navigation.

Showing a hands-on task runs its `setup.sh` as root in the session's container (D45) before
anything is recorded; Next/Skip/Previous/Restart all show a target the same way and never reset
the machine (spec section 5). Check results, attempts and completion are #88 and #89.
Nothing sent to a student carries check/setup/solution text or a quiz `correct` field."""
import asyncio
import logging
import os
import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.auth import Principal, current_student, db_guard
from app.content import LANGS, Lab, Task
from app.runtime import container_name

log = logging.getLogger("lab-manager")
T = db.SCHEMA
DEFAULT_SETUP_TIMEOUT_S = 50.0  # below nginx's default proxy_read_timeout (60 s)
_EXECS = asyncio.Semaphore(max(1, int(os.environ.get("LF_MAX_EXECS", "8") or 8)))
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
    try:
        sid = uuid.UUID(session)
    except ValueError:
        raise HTTPException(404, "no_session") from None
    with db_guard():
        row = await request.app.state.pool.fetchrow(
            f"select student, status, lab_id, current_task from {T}.lab_sessions where id = $1",
            sid,
        )
    if row is None:
        raise HTTPException(404, "no_session")
    if row["student"] != p.subject:
        raise HTTPException(403, "not_your_session")
    if row["status"] == "ended":
        raise HTTPException(410, "session_ended")
    if row["status"] != "running":
        raise HTTPException(409, "session_starting")
    lab = request.app.state.labs.get(row["lab_id"])
    if lab is None:
        raise HTTPException(404, "unknown_lab")
    return _Ctx(request, sid, p.subject, lab, row["current_task"])


async def _show(ctx: _Ctx, task: Task) -> dict[str, Any]:
    """Serialised per session: a second show while one runs gets 409 and runs nothing."""
    lock = _LOCKS.setdefault(ctx.sid, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "setup_running")
    try:
        async with lock:
            return await _do_show(ctx, task)
    finally:
        if not lock.locked() and _LOCKS.get(ctx.sid) is lock:
            del _LOCKS[ctx.sid]


async def _do_show(ctx: _Ctx, task: Task) -> dict[str, Any]:
    """Run the setup (hands-on), then record shown_at (first time only) and the current task."""
    if task.kind == "hands-on":
        script = (ctx.lab.path / "tasks" / task.id / "setup.sh").read_bytes().decode("utf-8")
        try:
            async with _EXECS:
                res = await ctx.request.app.state.runtime.exec_script(
                    container_name(ctx.sid), script, setup_timeout()
                )
        except Exception as exc:
            log.error("setup of %s in session %s failed (%s)", task.id, ctx.sid, type(exc).__name__)
            raise HTTPException(
                502, {"code": "setup_failed", "message": "The task could not be set up."}
            ) from None
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
    return await _show(ctx, ctx.lab.tasks[target])


@router.post("/{task_id}/show")
async def show_task(task_id: str, ctx: Annotated[_Ctx, Depends(_ctx)]) -> dict[str, Any]:
    task = ctx.task(task_id)
    if task is None:
        raise HTTPException(404, "unknown_task")
    return await _show(ctx, task)
