import datetime as dt
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import live
from app.auth import current_admin
from app.content import Lab

SID = uuid.uuid4()
NOW = dt.datetime(2026, 1, 1, 9, 0, tzinfo=dt.UTC)


class Pool:
    def __init__(self, rows=(), found=None, ended=None):
        self.rows, self.found, self.ended = list(rows), found, ended
        self.sql: list[str] = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return self.rows

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        return self.found if sql.startswith("select") else self.ended


class Runtime:
    def __init__(self):
        self.removed = []

    async def remove(self, name):
        self.removed.append(name)


def make(pool, labs=None):
    app = FastAPI()
    app.include_router(live.router)
    app.dependency_overrides[current_admin] = lambda: None
    app.state.pool, app.state.labs, app.state.runtime = pool, labs or {}, Runtime()
    return app, TestClient(app)


def row(lab_id="a", beat=None):
    return {
        "id": SID,
        "key": uuid.UUID(int=7),
        "name": "Fake N",
        "cohort": "C",
        "lab_id": lab_id,
        "started_at": NOW,
        "last_heartbeat": beat,
    }


def lab():
    return Lab(
        id="a",
        title={"en": "A", "he": "א"},
        status="draft",
        image="term",
        module=1,
        lesson=1,
        path=Path("."),
        tasks=[],
    )


def test_list_shape_title_lookup_and_null_heartbeat():
    pool = Pool([row("a"), row("gone", NOW)])
    _, c = make(pool, {"a": lab()})
    body = c.get("/api/admin/live").json()["sessions"]
    assert body[0]["lab_title"] == {"en": "A", "he": "א"}
    assert body[0]["last_heartbeat"] is None
    assert body[1]["lab_title"] is None
    assert body[1]["last_heartbeat"] == NOW.isoformat()
    assert body[0]["id"] == str(SID) and body[0]["key"] == str(uuid.UUID(int=7))
    assert "lf-smoke" in pool.sql[0] and "l.status in" in pool.sql[0]


def test_end_unknown_session_is_404_without_runtime_call():
    app, c = make(Pool(found=None))
    assert c.post(f"/api/admin/live/{SID}/end").status_code == 404
    assert app.state.runtime.removed == []


def test_end_malformed_id_is_404():
    _, c = make(Pool())
    assert c.post("/api/admin/live/nope/end").status_code == 404


def test_end_removes_the_container():
    app, c = make(Pool(found=SID, ended=SID))
    assert c.post(f"/api/admin/live/{SID}/end").status_code == 204
    assert app.state.runtime.removed == [f"lab-{SID}"]


def test_end_lost_race_is_404():
    app, c = make(Pool(found=SID, ended=None))
    assert c.post(f"/api/admin/live/{SID}/end").status_code == 404
    assert app.state.runtime.removed == []
