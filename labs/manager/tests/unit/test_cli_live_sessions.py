import asyncio

from app import cli


def test_prints_one_id_per_line(monkeypatch, capsys):
    async def fake(dsn):
        assert dsn == "postgres://x"
        return ["a-1", "b-2"]

    monkeypatch.setenv("LF_DATABASE_URL", "postgres://x")
    monkeypatch.setattr(cli, "live_sessions", fake)
    assert cli.main(["live-sessions"]) == 0
    assert capsys.readouterr().out == "a-1\nb-2\n"


def test_no_live_sessions_prints_nothing(monkeypatch, capsys):
    async def fake(dsn):
        return []

    monkeypatch.setenv("LF_DATABASE_URL", "postgres://x")
    monkeypatch.setattr(cli, "live_sessions", fake)
    assert cli.main(["live-sessions"]) == 0
    assert capsys.readouterr().out == ""


def test_without_database_url_fails_naming_it(monkeypatch, capsys):
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    assert cli.main(["live-sessions"]) == 1
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err.strip() == "LF_DATABASE_URL is not set"


def test_database_error_prints_only_the_type(monkeypatch, capsys):
    async def fake(dsn):
        raise ConnectionError("postgres://user:secret@host/db refused")

    monkeypatch.setenv("LF_DATABASE_URL", "postgres://user:secret@host/db")
    monkeypatch.setattr(cli, "live_sessions", fake)
    assert cli.main(["live-sessions"]) == 1
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err.strip() == "database error (ConnectionError)"
    assert "secret" not in cap.err


def test_query_uses_the_live_predicate(monkeypatch):
    seen = {}

    class Conn:
        async def fetch(self, sql):
            seen["sql"] = sql
            return [{"id": "x"}]

        async def close(self):
            seen["closed"] = True

    async def connect(dsn):
        return Conn()

    monkeypatch.setattr(cli.db, "connect", connect)
    assert asyncio.run(cli.live_sessions("dsn")) == ["x"]
    assert cli.LIVE in seen["sql"] and seen["closed"]
