import asyncio

from app import cli


def _run(monkeypatch, fake):
    monkeypatch.setenv("LF_DATABASE_URL", "postgres://x")
    monkeypatch.setattr(cli, "session_end", fake)
    return cli.main(["session-end", "sid-1"])


def test_prints_the_reason(monkeypatch, capsys):
    async def fake(dsn, sid):
        assert sid == "sid-1"
        return "heartbeat"

    assert _run(monkeypatch, fake) == 0
    assert capsys.readouterr().out == "heartbeat\n"


def test_prints_nothing_while_live(monkeypatch, capsys):
    async def fake(dsn, sid):
        return None

    assert _run(monkeypatch, fake) == 0
    assert capsys.readouterr().out == ""


def test_without_database_url_fails(monkeypatch, capsys):
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    assert cli.main(["session-end", "sid-1"]) == 1
    assert capsys.readouterr().err.strip() == "LF_DATABASE_URL is not set"


def test_database_error_prints_only_the_type(monkeypatch, capsys):
    async def fake(dsn, sid):
        raise ConnectionError("postgres://user:secret@host/db refused")

    assert _run(monkeypatch, fake) == 1
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err.strip() == "database error (ConnectionError)"


def test_query_is_one_select_by_id(monkeypatch):
    seen = {}

    class Conn:
        async def fetchrow(self, sql, sid):
            seen["sql"], seen["sid"] = sql, sid
            return {"end_reason": "student"}

        async def close(self):
            seen["closed"] = True

    async def connect(dsn):
        return Conn()

    monkeypatch.setattr(cli.db, "connect", connect)
    assert asyncio.run(cli.session_end("dsn", "abc")) == "student"
    assert seen["sql"].lower().startswith("select") and seen["sid"] == "abc" and seen["closed"]
