"""Acceptance tests for issue #69 (decision D117): the terminal WebSocket refuses a foreign Origin.

Contract this exam fixes:

* `LF_PUBLIC_ORIGIN` (e.g. `https://labs.shashkist.com`) is the one allowed browser origin. A
  WebSocket upgrade to `/s/<sid>/term` or `/s/<sid>/term/<rest>` whose `Origin` header is present
  and is not that origin is refused before accept with HTTP 403 and detail `origin_not_allowed`,
  before authentication (so an unauthenticated foreign-origin request gets 403, not 401) and
  before any upstream connection. The compare is exact on scheme, host and port, case-insensitive;
  a trailing `/` on the setting is ignored; `Origin: null` is present and refused.
* No `Origin` header (non-browser clients) is allowed as before.
* `LF_PUBLIC_ORIGIN` unset or empty: no check, and one WARNING line of the logger "lab-manager"
  at startup that mentions `LF_PUBLIC_ORIGIN`.
* `labs/deploy/compose.yml` sets `LF_PUBLIC_ORIGIN: https://labs.shashkist.com` on the manager.

The setting may be read at request time or at app start: every test sets it before the app starts
and leaves it set while requests are made. Most tests run without a database: a matching Origin
is shown by the request failing later with the auth refusal (401) instead of 403. The upstream is
a plain TCP listener that counts connections (`LF_TERM_UPSTREAM`), or the stub ttyd of the #8 exam
in the database tests, which skip locally without LF_TEST_DATABASE_URL (CI has it). Student ids
are fake.
"""
import logging
import socket
import threading
import uuid
from pathlib import Path

import pytest
import yaml
from test_issue_8_term_proxy import (  # noqa: F401  (fixtures are used by name)
    NGINX_HEADERS,
    SCHEMA,
    Env,
    FakeRuntime,
    Pump,
    _app_dsn,
    _db,
    _set_env,
    _stub,
    cookie_of,
    fresh_ip,
    upstream,
    write_content,
)

PUBLIC = "https://labs.shashkist.com"
FOREIGN = [
    "https://evil.shashkist.com",
    "http://labs.shashkist.com",
    "https://labs.shashkist.com:8443",
    "https://labs.shashkist.com.evil.test",
    "null",
]
REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
SID = str(uuid.uuid4())
ROUTES = [f"/s/{SID}/term", f"/s/{SID}/term/ws"]


# ---------------------------------------------------------------- helpers


class CountingListener:
    """A TCP listener standing in for ttyd; it only counts connections."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.connections = 0
        self.address = f"127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            conn.close()

    def close(self) -> None:
        self.sock.close()


def upgrade(client, path: str, origin: str | None = None, headers: dict | None = None):
    """Try a WebSocket upgrade. Returns ('denied', status, body text), ('closed',) for a close
    before accept, or ('accepted',) after closing the socket again."""
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    hdrs = dict(headers or {})
    if origin is not None:
        hdrs["origin"] = origin
    cm = client.websocket_connect(path, subprotocols=["tty"], headers=hdrs)
    try:
        cm.__enter__()
    except WebSocketDenialResponse as denial:
        return ("denied", denial.status_code, denial.text)
    except WebSocketDisconnect:
        return ("closed",)
    cm.__exit__(None, None, None)
    return ("accepted",)


def assert_origin_refused(result) -> None:
    assert result[0] == "denied", f"the upgrade was not denied: {result!r}"
    assert result[1] == 403, f"denied with {result[1]}, want 403"
    assert "origin_not_allowed" in result[2], f"body {result[2]!r}"


def assert_origin_check_passed(result) -> None:
    """Without a login the request must fail later with the auth refusal, not on the Origin."""
    assert result[0] == "denied", f"want the auth refusal (401), got {result!r}"
    assert result[1] == 401, f"want the auth refusal (401), got {result!r}"


@pytest.fixture
def listener(monkeypatch):
    lst = CountingListener()
    monkeypatch.setenv("LF_TERM_UPSTREAM", lst.address)
    yield lst
    lst.close()


@pytest.fixture
def make_client(tmp_path, monkeypatch, listener):
    """Start the app with no database and the given LF_PUBLIC_ORIGIN (None = unset)."""
    from fastapi.testclient import TestClient

    started: list = []

    def build(origin: str | None):
        monkeypatch.delenv("LF_DATABASE_URL", raising=False)
        _set_env(monkeypatch, write_content(tmp_path / "content"), tmp_path / "meminfo", tmp_path)
        monkeypatch.setenv("LF_TERM_UPSTREAM", listener.address)
        if origin is None:
            monkeypatch.delenv("LF_PUBLIC_ORIGIN", raising=False)
        else:
            monkeypatch.setenv("LF_PUBLIC_ORIGIN", origin)
        from app.main import app

        cm = TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000))
        started.append(cm)
        return cm.__enter__()

    yield build
    for cm in started:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------- no database


@pytest.mark.parametrize("origin", FOREIGN)
@pytest.mark.parametrize("path", ROUTES)
def test_a_foreign_origin_is_refused_403_origin_not_allowed_before_authentication(
    make_client, path, origin
):
    client = make_client(PUBLIC)
    assert_origin_refused(upgrade(client, path, origin))


@pytest.mark.parametrize("origin", FOREIGN)
@pytest.mark.parametrize("path", ROUTES)
def test_a_foreign_origin_never_reaches_the_upstream(make_client, listener, path, origin):
    client = make_client(PUBLIC)
    upgrade(client, path, origin, headers=NGINX_HEADERS)
    assert listener.connections == 0


@pytest.mark.parametrize(
    "origin", [PUBLIC, "HTTPS://Labs.Shashkist.com", "https://LABS.shashkist.COM"]
)
@pytest.mark.parametrize("path", ROUTES)
def test_a_matching_origin_passes_the_origin_check_case_insensitively(
    make_client, listener, path, origin
):
    client = make_client(PUBLIC)
    assert_origin_check_passed(upgrade(client, path, origin))
    assert listener.connections == 0  # no login, so still no upstream


@pytest.mark.parametrize("origin", [PUBLIC, "HTTPS://Labs.Shashkist.com"])
def test_a_trailing_slash_on_the_setting_is_ignored(make_client, origin):
    client = make_client(PUBLIC + "/")
    assert_origin_check_passed(upgrade(client, ROUTES[1], origin))
    assert_origin_refused(upgrade(client, ROUTES[1], "https://evil.shashkist.com"))


@pytest.mark.parametrize("path", ROUTES)
def test_no_origin_header_passes_the_origin_check(make_client, path):
    client = make_client(PUBLIC)
    assert_origin_check_passed(upgrade(client, path, None))


@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_setting_means_no_check(make_client, value):
    client = make_client(value)
    for path in ROUTES:
        assert_origin_check_passed(upgrade(client, path, "https://evil.shashkist.com"))
        assert_origin_check_passed(upgrade(client, path, "null"))


def _origin_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "lab-manager"
        and r.levelno == logging.WARNING
        and "LF_PUBLIC_ORIGIN" in r.getMessage()
    ]


@pytest.mark.parametrize("value", [None, ""])
def test_unset_setting_logs_one_warning_line_at_startup(make_client, caplog, value):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        client = make_client(value)
        assert len(_origin_warnings(caplog)) == 1, [r.getMessage() for r in caplog.records]
        upgrade(client, ROUTES[1], "https://evil.shashkist.com")
        upgrade(client, ROUTES[0], "https://evil.shashkist.com")
        assert len(_origin_warnings(caplog)) == 1, "the warning is logged once, not per request"


def test_a_configured_setting_logs_no_startup_warning(make_client, caplog):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(PUBLIC)
        assert _origin_warnings(caplog) == []


# ---------------------------------------------------------------- deploy


def test_production_compose_sets_the_public_origin_for_the_manager():
    compose = yaml.safe_load((REPO_ROOT / "labs" / "deploy" / "compose.yml").read_text("utf-8"))
    env = compose["services"]["lf-manager"]["environment"]
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    assert env.get("LF_PUBLIC_ORIGIN") == PUBLIC


# ---------------------------------------------------------------- with a database (CI)


@pytest.fixture(scope="module")
def env69(_db, tmp_path_factory):
    from fastapi.testclient import TestClient

    tmp = tmp_path_factory.mktemp("origin-exam")
    mp = pytest.MonkeyPatch()
    mp.setenv("LF_DATABASE_URL", _app_dsn(_db))
    _set_env(mp, write_content(tmp / "content"), tmp / "meminfo", tmp)
    mp.setenv("LF_PUBLIC_ORIGIN", PUBLIC)
    from app.main import app

    async def end_all(conn):
        await conn.execute(
            f"update {SCHEMA}.lab_sessions set status = 'ended', ended_at = now(), "
            "end_reason = 'orphan' where status in ('starting', 'running')"
        )

    try:
        with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
            app.state.runtime = FakeRuntime()
            e = Env(_db, c)
            e.sql(end_all)
            yield e
            e.sql(end_all)
    finally:
        mp.undo()


@pytest.mark.parametrize("path", ["", "/ws"])
@pytest.mark.parametrize("origin", [PUBLIC, "HTTPS://Labs.Shashkist.com", None])
def test_owner_with_a_matching_or_absent_origin_is_proxied(env69, upstream, path, origin):
    client, sid, _ = env69.owner_with_session()
    hdrs = cookie_of(client)
    if origin is not None:
        hdrs["origin"] = origin
    with client.websocket_connect(
        f"/s/{sid}/term{path}", subprotocols=["tty"], headers=hdrs
    ) as ws:
        assert ws.accepted_subprotocol == "tty"
        pump = Pump(ws)
        ws.send_bytes(b"0hello")
        assert pump.frame() == ("bytes", b"0hello")
    assert upstream.ws, "the upstream was not contacted"


@pytest.mark.parametrize("path", ["", "/ws"])
@pytest.mark.parametrize("origin", FOREIGN)
def test_owner_with_a_foreign_origin_is_refused_and_the_lab_is_not_contacted(
    env69, upstream, path, origin
):
    client, sid, _ = env69.owner_with_session()
    result = upgrade(client, f"/s/{sid}/term{path}", origin, headers=cookie_of(client))
    assert_origin_refused(result)
    assert upstream.untouched()


def test_a_foreign_origin_wins_over_an_unknown_session(env69, upstream):
    client, _, _ = env69.owner_with_session()
    result = upgrade(
        client, f"/s/{uuid.uuid4()}/term/ws", "https://evil.shashkist.com",
        headers=cookie_of(client),
    )
    assert_origin_refused(result)
