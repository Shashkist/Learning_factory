"""Acceptance tests for issue #8: `/s/<session>/term/*` proxy with an ownership check.

The proxy forwards HTTP and WebSocket traffic to the ttyd of `lab-<session>` (port 7681, base
path `/s/<session>/term/`, so the path is forwarded unchanged, never stripped). Only the
session's owner may use it. The real-ttyd part is in test_issue_8_term_proxy_docker.py.

Contract this exam fixes (the build must follow it or change the exam through the manager):

* SEAM: `LF_TERM_UPSTREAM`. When set (`host:port`, e.g. `127.0.0.1:34567`) it replaces the
  upstream address of every session; when unset the upstream is `lab-<session uuid>:7681`
  (container name on the lab network, resolved by Docker's DNS). It is read on every request,
  not at import or startup. Production never sets it; the exam needs it because a test runner
  is not on the lab network (the host cannot resolve `lab-<uuid>`).
* Authentication is the existing cookie `lf_session` (D23, `app.auth.current_principal`):
  no or invalid cookie -> 401; a disabled student -> 401. Then, in this order: an id that
  is not a UUID or a session that does not exist -> 404; a session of another student -> 403
  (admins are not covered here); the owner's session with status `ended` -> 410. A refusal
  never contacts the upstream.
* For WebSocket the same refusals happen before the upgrade is accepted. A server that sends a
  denial response must use the status above; closing the socket before accept (the ASGI
  default, HTTP 403 on the wire) is accepted as well. Accept-then-close is tolerated only if
  the very first frame is the close and the upstream was never contacted.
* The `lf_session` cookie is never forwarded to the lab (the student has a shell there and
  could read it). An `X-Forwarded-For` sent by an untrusted peer is not forwarded verbatim.
* Subprotocol (`tty`) and text/binary frame types survive the proxy; a close on either side
  closes the other side. An unreachable upstream answers 502, 503 or 504 on HTTP.
* Behind nginx: requests carrying `X-Forwarded-For`/`-Proto`/`-Host` and upgrade headers
  work with or without `LF_TRUSTED_PROXY` (same convention as `app.auth.client_ip`).

Dev dependency: `websockets` (uvicorn serves the stub upstream's WebSocket with it; the
proxy's own WebSocket client is the builder's choice).

Database tests need a superuser DSN to a throwaway Postgres in LF_TEST_DATABASE_URL and skip
locally when it is absent (CI sets it and fails on any skip). The two tests marked "no
database" run anywhere. Student ids are fake. No container is started: the runtime is a fake.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import os
import queue
import socket
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"
SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)
FRAME_WAIT = 10.0

_n = itertools.count(1)

LAB_YAML = """id: alpha
title:
  en: Lab alpha
  he: מעבדה אלפא
description:
  en: Fake lab for the proxy exam.
  he: מעבדה מזויפת למבחן הפרוקסי.
image: term
time_limit_min: 15
internet: false
allow_domains: []
sudo: false
status: published
module: 1
lesson: 5
tasks:
  - id: q1
    kind: quiz
    question:
      en: Pick one.
      he: בחר אחד.
    options:
      - en: a
        he: א
      - en: b
        he: ב
    correct: 0
"""


# ---------------------------------------------------------------- stub upstream (a fake ttyd)


class Upstream:
    """A local server that behaves like ttyd for the purposes of the proxy and records what
    it was sent. Echo protocol: a binary frame `0<data>` is answered with `0<data>`; any text
    frame is answered with a binary `1stub`; the text frame `close-me` makes it close."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.ws: list[dict] = []
        self.frames: list[tuple[str, bytes | str]] = []
        self.ws_closed = threading.Event()
        self.host = "127.0.0.1"
        self.port = 0
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def reset(self) -> None:
        self.requests.clear()
        self.ws.clear()
        self.frames.clear()
        self.ws_closed.clear()

    def untouched(self) -> bool:
        return not self.requests and not self.ws

    def start(self) -> None:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Route, WebSocketRoute

        up = self

        async def page(request):
            body = await request.body()
            up.requests.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "query": request.url.query,
                    "headers": {k.lower(): v for k, v in request.headers.items()},
                    "body": body,
                }
            )
            if request.url.path.endswith("/teapot"):
                return Response("short and stout", status_code=418, headers={"X-Stub": "1"})
            return Response(
                f"stub-ttyd {request.method}", media_type="text/html", headers={"X-Stub": "1"}
            )

        async def socket_(websocket):
            wanted = websocket.scope.get("subprotocols") or []
            up.ws.append(
                {
                    "path": websocket.url.path,
                    "query": websocket.url.query,
                    "headers": {k.lower(): v for k, v in websocket.headers.items()},
                    "subprotocols": list(wanted),
                }
            )
            await websocket.accept(subprotocol="tty" if "tty" in wanted else None)
            try:
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    if msg.get("bytes") is not None:
                        data = msg["bytes"]
                        up.frames.append(("bytes", data))
                        await websocket.send_bytes(b"0" + data[1:])
                    else:
                        text = msg.get("text") or ""
                        up.frames.append(("text", text))
                        if text == "close-me":
                            await websocket.close(1000)
                            break
                        await websocket.send_bytes(b"1stub")
            finally:
                up.ws_closed.set()

        app = Starlette(
            routes=[
                WebSocketRoute("/{rest:path}", socket_),
                Route(
                    "/{rest:path}",
                    page,
                    methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"],
                ),
            ]
        )
        config = uvicorn.Config(
            app, host=self.host, port=0, log_level="warning", ws="auto", lifespan="off"
        )
        server = uvicorn.Server(config)
        self._server = server
        self._thread = threading.Thread(target=server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "the stub upstream did not start"
        self.port = server.servers[0].sockets[0].getsockname()[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)


@pytest.fixture(scope="module")
def _stub():
    up = Upstream()
    up.start()
    yield up
    up.stop()


@pytest.fixture
def upstream(_stub, monkeypatch):
    _stub.reset()
    monkeypatch.setenv("LF_TERM_UPSTREAM", _stub.address)
    return _stub


# ---------------------------------------------------------------- websocket helpers


class Pump:
    """Reads a Starlette TestClient websocket in a thread so a hung proxy fails the test
    after a timeout instead of blocking it forever."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                msg = self.ws.receive()
            except Exception as exc:  # the session was torn down
                self.q.put({"type": "websocket.close", "code": 1006, "error": repr(exc)})
                return
            self.q.put(msg)
            if msg["type"] == "websocket.close":
                return

    def frame(self, timeout: float = FRAME_WAIT):
        """('bytes', b'..') | ('text', '..') | ('close', code)."""
        try:
            msg = self.q.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"no websocket frame within {timeout} s") from None
        if msg["type"] == "websocket.close":
            return ("close", msg.get("code"))
        if msg.get("bytes") is not None:
            return ("bytes", msg["bytes"])
        return ("text", msg.get("text"))


def cookie_of(client) -> dict[str, str]:
    token = client.cookies.get("lf_session")
    assert token, "the client is not logged in"
    return {"cookie": f"lf_session={token}"}


def open_ws(client, session_id: str, path: str = "ws", headers: dict | None = None):
    hdrs = {} if headers is None else dict(headers)
    return client.websocket_connect(
        f"/s/{session_id}/term/{path}", subprotocols=["tty"], headers=hdrs
    )


def assert_ws_refused(client, session_id: str, status: int, headers: dict | None = None) -> None:
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    try:
        ws = open_ws(client, session_id, headers=headers)
        entered = ws.__enter__()
    except WebSocketDenialResponse as denial:
        assert denial.status_code == status, f"denied with {denial.status_code}, want {status}"
        return
    except WebSocketDisconnect:
        return  # closed before accept
    try:
        first = Pump(entered).frame(5)
        assert first[0] == "close", f"the socket was accepted and sent {first!r}"
    finally:
        ws.__exit__(None, None, None)


# ---------------------------------------------------------------- fake runtime and database


class FakeRuntime:
    """Starts nothing; `lab-<uuid>` names only. Methods are async like DockerRuntime's.

    It lists the containers it "started" as running, like Docker would. The janitor's first
    pass with a database runs about 2 s after startup (its pass before the pool exists fails
    and backs off); with an empty listing it ended every live session as an orphan, so a test
    whose session spanned that moment got 410 (#105).
    """

    def __init__(self) -> None:
        self.removed: list[str] = []
        self.started: dict[str, dict] = {}

    async def start(self, spec):
        name = str(spec["name"])
        self.started[name] = dict(spec.get("Labels") or {})
        return name

    async def remove(self, name):
        self.removed.append(name)
        self.started.pop(name, None)

    async def list_lab_containers(self):
        from app.runtime import ContainerInfo

        return [
            ContainerInfo(name, True, labels.get("lf.session", ""), labels)
            for name, labels in self.started.items()
        ]


def _admin_dsn() -> str:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set (no Postgres available)")
    host = urlsplit(dsn).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail(f"LF_TEST_DATABASE_URL must point at a local throwaway Postgres, not {host!r}")
    return dsn


def _app_dsn(admin_dsn: str) -> str:
    parts = urlsplit(admin_dsn)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_as(dsn: str, fn):
    async def go():
        import asyncpg  # a missing asyncpg must fail, not skip

        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


def id_hash(raw: str) -> str:
    return hmac.new(TEST_SECRET.encode(), raw.lstrip("0").encode(), hashlib.sha256).hexdigest()


def fresh_ip() -> str:
    n = next(_n)
    return f"10.88.{n // 250}.{n % 250 + 1}"


def write_content(root: Path) -> Path:
    d = root / "test" / "alpha"
    d.mkdir(parents=True)
    (d / "lab.yaml").write_text(LAB_YAML, encoding="utf-8")
    return root


def _set_env(mp: pytest.MonkeyPatch, content: Path, meminfo: Path, tmp: Path) -> None:
    meminfo.write_text(
        "MemTotal: 32768000 kB\nMemFree: 8192000 kB\nMemAvailable: 16384000 kB\nSwapTotal: 0 kB\n",
        encoding="utf-8",
    )
    mp.setenv("LF_ID_SECRET", TEST_SECRET)
    mp.setenv("LF_CONTENT_DIR", str(content))
    mp.setenv("LF_MEMINFO_PATH", str(meminfo))
    mp.setenv("LF_DOCKER_HOST", f"unix://{tmp / 'no-docker.sock'}")
    mp.setenv("LF_DISK_RESERVE_GB", "0")
    mp.setenv("LF_JANITOR_INTERVAL_S", "3600")
    mp.setenv("LF_MAX_TERM", "1000")
    for var in (
        "LF_TRUSTED_PROXY", "LF_MAX_CODE", "LF_MEM_RESERVE_MB", "LF_HEARTBEAT_TIMEOUT_S",
        "LF_DISK_PATH", "LF_TERM_UPSTREAM",
    ):
        mp.delenv(var, raising=False)


@pytest.fixture(scope="module")
def _db():
    admin_dsn = _admin_dsn()

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        assert OWNER_SQL.is_file(), f"{OWNER_SQL} is missing"
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)
    return admin_dsn


class Env:
    def __init__(self, admin_dsn, client):
        self.admin_dsn = admin_dsn
        self.client = client
        self.app = client.app
        self.cohort_id: int | None = None

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def new_client(self):
        from fastapi.testclient import TestClient

        ip = fresh_ip()
        c = TestClient(self.app, base_url="https://testserver", client=(ip, 50000))
        c.portal = self.client.portal  # the app's pool belongs to the lifespan client's loop
        c.peer_ip = ip  # the socket peer the app sees (what LF_TRUSTED_PROXY is compared to)
        return c

    def student(self):
        """Seed a fake student and log in; returns (client, id hash)."""
        from argon2 import PasswordHasher

        raw = str(700000000 + next(_n))
        pw = PasswordHasher().hash(COHORT_PASSWORD)

        async def seed(conn):
            if self.cohort_id is None:
                self.cohort_id = await conn.fetchval(
                    f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                    "values ($1, $2, $3) returning id",
                    f"Fake cohort {next(_n)}", pw, TODAY + 30 * DAY,
                )
            await conn.execute(
                f"insert into {SCHEMA}.students (id_hash, name, cohort_id) values ($1, $2, $3)",
                id_hash(raw), f"Fake Student {raw}", self.cohort_id,
            )

        self.sql(seed)
        client = self.new_client()
        resp = client.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return client, id_hash(raw)

    def open_lab(self, client) -> str:
        resp = client.post("/api/sessions", json={"lab_id": "alpha"})
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    def owner_with_session(self):
        client, h = self.student()
        return client, self.open_lab(client), h

    def disable(self, student_hash: str) -> None:
        async def go(conn):
            await conn.execute(
                f"update {SCHEMA}.students set disabled = true where id_hash = $1", student_hash
            )

        self.sql(go)


@pytest.fixture(scope="module")
def env(_db, tmp_path_factory):
    from fastapi.testclient import TestClient

    tmp = tmp_path_factory.mktemp("proxy-exam")
    mp = pytest.MonkeyPatch()
    mp.setenv("LF_DATABASE_URL", _app_dsn(_db))
    _set_env(mp, write_content(tmp / "content"), tmp / "meminfo", tmp)
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


@pytest.fixture
def nodb_client(tmp_path, monkeypatch):
    """The app with no database configured."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    _set_env(monkeypatch, write_content(tmp_path / "content"), tmp_path / "meminfo", tmp_path)
    from app.main import app

    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        yield c


# ---------------------------------------------------------------- no database: no cookie


def test_http_without_a_cookie_is_401_and_the_lab_is_not_contacted(nodb_client, upstream):
    sid = str(uuid.uuid4())
    for path in ("", "token", "deep/path/file.js"):
        resp = nodb_client.get(f"/s/{sid}/term/{path}")
        assert resp.status_code == 401, (path, resp.status_code, resp.text[:200])
    assert upstream.untouched()


def test_websocket_without_a_cookie_is_refused_and_the_lab_is_not_contacted(nodb_client, upstream):
    assert_ws_refused(nodb_client, str(uuid.uuid4()), 401)
    assert upstream.untouched()


# ---------------------------------------------------------------- HTTP proxying


def test_owner_get_is_proxied_to_the_lab_under_the_session_base_path(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/")
    assert resp.status_code == 200, resp.text
    assert resp.text == "stub-ttyd GET"
    assert resp.headers.get("x-stub") == "1", "upstream response headers are passed through"
    assert resp.headers["content-type"].startswith("text/html")
    assert [r["path"] for r in upstream.requests] == [f"/s/{sid}/term/"]
    assert upstream.requests[0]["method"] == "GET"


def test_subpaths_and_query_strings_are_forwarded_unchanged(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/token?a=1&b=two%20words")
    assert resp.status_code == 200
    seen = upstream.requests[-1]
    assert seen["path"] == f"/s/{sid}/term/token"
    assert seen["query"] == "a=1&b=two%20words"


def test_request_method_and_body_are_forwarded(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.post(f"/s/{sid}/term/upload", content=b"payload-123")
    assert resp.status_code == 200
    seen = upstream.requests[-1]
    assert seen["method"] == "POST"
    assert seen["body"] == b"payload-123"


def test_the_upstream_status_and_body_pass_through(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/teapot")
    assert resp.status_code == 418
    assert resp.text == "short and stout"


def test_the_path_without_a_trailing_slash_reaches_the_terminal(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term", follow_redirects=False)
    if resp.status_code in (301, 302, 307, 308):
        assert urlsplit(resp.headers["location"]).path == f"/s/{sid}/term/"
    else:
        assert resp.status_code == 200
        assert resp.text == "stub-ttyd GET"


def test_the_session_cookie_is_never_forwarded_to_the_lab(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/", headers={"cookie": cookie_of(client)["cookie"] + "; a=b"})
    assert resp.status_code == 200
    forwarded = upstream.requests[-1]["headers"].get("cookie", "")
    assert "lf_session" not in forwarded
    assert cookie_of(client)["cookie"].split("=", 1)[1] not in str(upstream.requests[-1]["headers"])


def test_an_unreachable_lab_is_a_bad_gateway(env, monkeypatch):
    client, sid, _ = env.owner_with_session()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()  # nothing listens there now
    monkeypatch.setenv("LF_TERM_UPSTREAM", f"127.0.0.1:{dead_port}")
    resp = client.get(f"/s/{sid}/term/")
    assert resp.status_code in (502, 503, 504), resp.status_code


def test_without_an_override_the_upstream_is_the_lab_container_on_7681(env, monkeypatch):
    """Records the host name the proxy resolves: `lab-<session>` and port 7681."""
    client, sid, _ = env.owner_with_session()
    monkeypatch.delenv("LF_TERM_UPSTREAM", raising=False)
    looked_up: list[tuple[object, object]] = []
    real = socket.getaddrinfo

    def spy(host, port, *args, **kwargs):
        if isinstance(host, str) and host.startswith("lab-"):
            looked_up.append((host, port))
            raise socket.gaierror(socket.EAI_NONAME, "no such lab (exam)")
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", spy)
    resp = client.get(f"/s/{sid}/term/")
    assert resp.status_code in (502, 503, 504), resp.status_code
    assert looked_up, "the proxy never tried to reach lab-<session>"
    assert {h for h, _ in looked_up} == {f"lab-{sid}"}
    assert {int(p) for _, p in looked_up} == {7681}


# ---------------------------------------------------------------- ownership (HTTP)


def test_no_cookie_is_401_for_a_real_session(env, upstream):
    _, sid, _ = env.owner_with_session()
    anon = env.new_client()
    assert anon.get(f"/s/{sid}/term/").status_code == 401
    assert upstream.untouched()


def test_an_invalid_cookie_is_401(env, upstream):
    _, sid, _ = env.owner_with_session()
    anon = env.new_client()
    resp = anon.get(f"/s/{sid}/term/", headers={"cookie": "lf_session=not-a-real-token"})
    assert resp.status_code == 401
    assert upstream.untouched()


def test_another_logged_in_student_gets_403_and_the_lab_is_not_contacted(env, upstream):
    _, sid, _ = env.owner_with_session()
    intruder, _ = env.student()
    for path in ("", "token", "ws"):
        resp = intruder.get(f"/s/{sid}/term/{path}")
        assert resp.status_code == 403, (path, resp.status_code, resp.text[:200])
    assert intruder.post(f"/s/{sid}/term/token", content=b"x").status_code == 403
    assert upstream.untouched()


def test_an_unknown_session_is_404(env, upstream):
    client, _, _ = env.owner_with_session()
    assert client.get(f"/s/{uuid.uuid4()}/term/").status_code == 404
    assert upstream.untouched()


def test_a_malformed_session_id_is_404(env, upstream):
    client, _, _ = env.owner_with_session()
    for bad in ("not-a-uuid", "123", "..%2f..%2fetc"):
        assert client.get(f"/s/{bad}/term/").status_code == 404, bad
    assert upstream.untouched()


def test_an_ended_session_is_410_and_the_lab_is_not_contacted(env, upstream):
    client, sid, _ = env.owner_with_session()
    assert client.get(f"/s/{sid}/term/").status_code == 200  # live: works
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    upstream.reset()
    for path in ("", "token"):
        assert client.get(f"/s/{sid}/term/{path}").status_code == 410, path
    assert upstream.untouched()


def test_a_disabled_student_is_refused_with_401(env, upstream):
    client, sid, h = env.owner_with_session()
    assert client.get(f"/s/{sid}/term/").status_code == 200
    env.disable(h)
    upstream.reset()
    assert client.get(f"/s/{sid}/term/").status_code == 401
    assert upstream.untouched()


def test_the_owner_keeps_access_after_another_student_was_refused(env, upstream):
    client, sid, _ = env.owner_with_session()
    intruder, _ = env.student()
    assert intruder.get(f"/s/{sid}/term/").status_code == 403
    assert client.get(f"/s/{sid}/term/").status_code == 200


# ---------------------------------------------------------------- WebSocket proxying


def test_owner_websocket_reaches_the_lab_and_echoes_input(env, upstream):
    client, sid, _ = env.owner_with_session()
    with open_ws(client, sid, headers=cookie_of(client)) as ws:
        assert ws.accepted_subprotocol == "tty"
        pump = Pump(ws)
        ws.send_bytes(b'{"AuthToken":"","columns":80,"rows":24}')
        ws.send_bytes(b"0echo hi\r")
        got = []
        deadline = time.time() + FRAME_WAIT
        while time.time() < deadline and (b"0echo hi\r" not in [d for _, d in got]):
            got.append(pump.frame(deadline - time.time() + 0.1))
        assert ("bytes", b"0echo hi\r") in got
    assert upstream.ws[-1]["path"] == f"/s/{sid}/term/ws"
    assert "tty" in upstream.ws[-1]["subprotocols"]


def test_websocket_frame_types_and_order_are_preserved(env, upstream):
    client, sid, _ = env.owner_with_session()
    with open_ws(client, sid, headers=cookie_of(client)) as ws:
        pump = Pump(ws)
        ws.send_text('{"columns":100,"rows":30}')
        assert pump.frame() == ("bytes", b"1stub")
        ws.send_bytes(b"0first")
        ws.send_bytes(b"0second")
        assert pump.frame() == ("bytes", b"0first")
        assert pump.frame() == ("bytes", b"0second")
    assert upstream.frames[0] == ("text", '{"columns":100,"rows":30}')
    assert upstream.frames[1:3] == [("bytes", b"0first"), ("bytes", b"0second")]


def test_a_large_binary_message_passes_through_intact(env, upstream):
    client, sid, _ = env.owner_with_session()
    blob = b"0" + bytes(range(256)) * 1024  # 256 KiB, like a big paste
    with open_ws(client, sid, headers=cookie_of(client)) as ws:
        pump = Pump(ws)
        ws.send_bytes(blob)
        assert pump.frame(30) == ("bytes", blob)


def test_the_session_cookie_is_not_forwarded_on_the_websocket_handshake(env, upstream):
    client, sid, _ = env.owner_with_session()
    token = cookie_of(client)["cookie"].split("=", 1)[1]
    with open_ws(client, sid, headers=cookie_of(client)) as ws:
        Pump(ws)
        ws.send_bytes(b"0x")
    assert upstream.ws, "the lab was never contacted"
    assert "lf_session" not in upstream.ws[-1]["headers"].get("cookie", "")
    assert token not in str(upstream.ws[-1]["headers"])


def test_the_lab_closing_the_websocket_closes_the_client_side(env, upstream):
    client, sid, _ = env.owner_with_session()
    with open_ws(client, sid, headers=cookie_of(client)) as ws:
        pump = Pump(ws)
        ws.send_text("close-me")
        kind, _ = pump.frame()
        assert kind == "close"


def test_the_client_closing_the_websocket_closes_the_lab_side(env, upstream):
    client, sid, _ = env.owner_with_session()
    with open_ws(client, sid, headers=cookie_of(client)) as ws:
        Pump(ws)
        ws.send_bytes(b"0hello")
    assert upstream.ws_closed.wait(10), "the lab side of the websocket stayed open"


def test_websocket_of_another_student_is_refused_with_403(env, upstream):
    _, sid, _ = env.owner_with_session()
    intruder, _ = env.student()
    assert_ws_refused(intruder, sid, 403, headers=cookie_of(intruder))
    assert upstream.untouched()


def test_websocket_without_a_cookie_is_refused_with_401_for_a_real_session(env, upstream):
    _, sid, _ = env.owner_with_session()
    assert_ws_refused(env.new_client(), sid, 401)
    assert upstream.untouched()


def test_websocket_of_an_unknown_session_is_refused_with_404(env, upstream):
    client, _, _ = env.owner_with_session()
    assert_ws_refused(client, str(uuid.uuid4()), 404, headers=cookie_of(client))
    assert upstream.untouched()


def test_websocket_of_an_ended_session_is_refused_with_410(env, upstream):
    client, sid, _ = env.owner_with_session()
    assert client.post(f"/api/sessions/{sid}/end").status_code == 204
    assert_ws_refused(client, sid, 410, headers=cookie_of(client))
    assert upstream.untouched()


def test_ending_another_session_does_not_disturb_an_open_terminal(env, upstream):
    a, sid_a, _ = env.owner_with_session()
    b, sid_b, _ = env.owner_with_session()
    with open_ws(b, sid_b, headers=cookie_of(b)) as ws:
        pump = Pump(ws)
        assert a.post(f"/api/sessions/{sid_a}/end").status_code == 204
        ws.send_bytes(b"0still here")
        assert pump.frame() == ("bytes", b"0still here")


def test_websocket_without_an_override_connects_to_the_lab_container_on_7681(env, monkeypatch):
    client, sid, _ = env.owner_with_session()
    monkeypatch.delenv("LF_TERM_UPSTREAM", raising=False)
    looked_up: list[tuple[object, object]] = []
    real = socket.getaddrinfo

    def spy(host, port, *args, **kwargs):
        if isinstance(host, str) and host.startswith("lab-"):
            looked_up.append((host, port))
            raise socket.gaierror(socket.EAI_NONAME, "no such lab (exam)")
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", spy)
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    try:
        with open_ws(client, sid, headers=cookie_of(client)) as ws:
            assert Pump(ws).frame(10)[0] == "close"
    except (WebSocketDisconnect, WebSocketDenialResponse):
        pass  # refused during the handshake is fine as well
    assert looked_up, "the proxy never tried to reach lab-<session>"
    assert {h for h, _ in looked_up} == {f"lab-{sid}"}
    assert {int(p) for _, p in looked_up} == {7681}


# ---------------------------------------------------------------- behind nginx


NGINX_HEADERS = {
    "x-forwarded-for": "203.0.113.9",
    "x-forwarded-proto": "https",
    "x-forwarded-host": "labs.example.test",
    "x-real-ip": "203.0.113.9",
}


def test_http_behind_nginx_with_a_trusted_proxy_works(env, upstream, monkeypatch):
    client, sid, _ = env.owner_with_session()
    monkeypatch.setenv("LF_TRUSTED_PROXY", client.peer_ip)
    resp = client.get(f"/s/{sid}/term/", headers=NGINX_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.text == "stub-ttyd GET"


def test_http_behind_nginx_without_a_trusted_proxy_works(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/", headers=NGINX_HEADERS)
    assert resp.status_code == 200, resp.text


def test_x_forwarded_for_from_an_untrusted_peer_is_not_passed_to_the_lab(env, upstream):
    client, sid, _ = env.owner_with_session()
    resp = client.get(f"/s/{sid}/term/", headers={"x-forwarded-for": "198.51.100.77"})
    assert resp.status_code == 200
    assert "198.51.100.77" not in upstream.requests[-1]["headers"].get("x-forwarded-for", "")


def test_websocket_upgrade_behind_nginx_headers_works(env, upstream, monkeypatch):
    client, sid, _ = env.owner_with_session()
    hdrs = {
        **NGINX_HEADERS,
        **cookie_of(client),
        "connection": "upgrade",
        "upgrade": "websocket",
        "origin": "https://labs.example.test",
    }
    with open_ws(client, sid, headers=hdrs) as ws:
        assert ws.accepted_subprotocol == "tty"
        pump = Pump(ws)
        ws.send_bytes(b"0behind nginx")
        assert pump.frame() == ("bytes", b"0behind nginx")


def test_websocket_behind_nginx_with_a_trusted_proxy_works(env, upstream, monkeypatch):
    client, sid, _ = env.owner_with_session()
    monkeypatch.setenv("LF_TRUSTED_PROXY", client.peer_ip)
    hdrs = {**NGINX_HEADERS, **cookie_of(client), "upgrade": "websocket"}
    with open_ws(client, sid, headers=hdrs) as ws:
        pump = Pump(ws)
        ws.send_bytes(b"0trusted")
        assert pump.frame() == ("bytes", b"0trusted")
