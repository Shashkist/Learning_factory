"""Acceptance tests for issue #373 (review of #69, decision D117).

Contract this exam fixes:

* The deploy smoke `labs/deploy/smoke/terminal.py` sends `Origin: <base URL>` (scheme://host:port
  of the BASE_URL, as a browser would) on its terminal WebSocket upgrade. If the server refuses
  that origin, the smoke fails (non-zero exit, one reason line) and still ends its session.
* The smoke makes one more upgrade with a foreign origin (not the base URL's) and expects HTTP
  403. If the server accepts it (the check is off), the smoke fails.
* At startup the manager logs ONE WARNING (logger "lab-manager", message mentions
  `LF_PUBLIC_ORIGIN`) when the value has a path, a query, a fragment, a default port (:443 for
  https, :80 for http), userinfo, or no scheme/host. It does not normalise. A clean value logs no
  such warning; unset keeps today's single "not set" warning.

The smoke tests use a fake manager (the #86 exam's, wrapped in an origin policy); the startup
tests need no database. Ids and passwords are fake.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import logging
import socket
import threading
import time

import pytest
from test_issue_69_ws_origin_check import (  # noqa: F401, F811  (fixtures are used by name)
    PUBLIC,
    _origin_warnings,
    listener,
    make_client,
)
from test_issue_86_terminal_client import (  # noqa: F401, F811
    Fake,
    _build_app,
    assert_clean,
    reason_line,
    run_client,
)

# ---------------------------------------------------------------- smoke: fake manager


class Policy:
    """Which origins the fake terminal accepts: "own" (the base URL only), "none", "any"."""

    def __init__(self) -> None:
        self.mode = "own"
        self.base = ""
        self.origins: list[str | None] = []  # the Origin of every websocket upgrade, in order
        self.statuses: list[int | None] = []  # 403 = the fake refused the upgrade

    def reset(self, mode: str) -> None:
        self.mode = mode
        self.origins = []
        self.statuses = []

    def allows(self, origin: str | None) -> bool:
        if self.mode == "any":
            return True
        if self.mode == "none":
            return False
        return origin is not None and origin.lower().rstrip("/") == self.base.lower()


def _with_policy(app, policy: Policy):
    async def wrapped(scope, receive, send):
        if scope["type"] == "websocket":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            origin = headers.get("origin")
            policy.origins.append(origin)
            if not policy.allows(origin):
                policy.statuses.append(403)
                await receive()  # websocket.connect
                await send({"type": "websocket.close", "code": 1008})  # uvicorn answers 403
                return
            policy.statuses.append(None)
        await app(scope, receive, send)

    return wrapped


@pytest.fixture(scope="module")
def origin_server():
    import uvicorn

    fake = Fake()
    policy = Policy()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    policy.base = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(
        _with_policy(_build_app(fake), policy),
        host="127.0.0.1",
        port=port,
        log_level="error",
        ws="auto",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline, "the fake manager did not start"
        time.sleep(0.05)
    yield fake, policy, policy.base
    server.should_exit = True
    thread.join(timeout=10)


# ---------------------------------------------------------------- AC1: the smoke sends Origin


def test_the_smoke_sends_the_base_url_as_origin_on_the_terminal_upgrade(origin_server):
    fake, policy, base = origin_server
    fake.reset("good")
    policy.reset("own")
    res = run_client(base)
    assert res.returncode == 0, res.stdout + res.stderr
    assert_clean(res, fake)
    assert policy.origins, "no websocket upgrade reached the server"
    assert policy.origins[0] is not None, "the terminal upgrade carried no Origin header"
    assert policy.origins[0].lower().rstrip("/") == base.lower(), policy.origins
    assert ("end", fake.sid, True) in fake.events


def test_the_smoke_fails_when_the_server_refuses_its_own_origin(origin_server):
    fake, policy, base = origin_server
    fake.reset("good")
    policy.reset("none")
    res = run_client(base, timeout="4")
    assert res.returncode != 0, res.stdout + res.stderr
    assert policy.origins and policy.origins[0] is not None, policy.origins
    assert ("end", fake.sid, True) in fake.events, "the session must still be ended"
    assert_clean(res, fake)
    reason_line(res)


# ---------------------------------------------------------------- AC2: one foreign-origin upgrade


def test_the_smoke_makes_one_more_upgrade_with_a_foreign_origin_and_passes_on_403(origin_server):
    fake, policy, base = origin_server
    fake.reset("good")
    policy.reset("own")
    res = run_client(base)
    assert res.returncode == 0, res.stdout + res.stderr
    foreign = [o for o in policy.origins if (o or "").lower().rstrip("/") != base.lower()]
    assert len(foreign) == 1, f"want exactly one foreign-origin upgrade: {policy.origins}"
    assert foreign[0] and foreign[0].startswith(("http://", "https://")), foreign
    assert len(policy.origins) == 2, policy.origins
    assert policy.statuses.count(403) == 1, policy.statuses
    assert_clean(res, fake)


def test_the_smoke_fails_when_a_foreign_origin_is_accepted(origin_server):
    fake, policy, base = origin_server
    fake.reset("good")
    policy.reset("any")  # the Origin check is off
    fake.check_origin = False  # the fake terminal itself must accept a foreign Origin too
    res = run_client(base, timeout="4")
    assert res.returncode != 0, res.stdout + res.stderr
    assert len(policy.origins) >= 2, f"no foreign-origin upgrade was tried: {policy.origins}"
    assert ("end", fake.sid, True) in fake.events, "the session must still be ended"
    assert_clean(res, fake)
    reason_line(res)


# ---------------------------------------------------------------- AC3: startup warning

MALFORMED = [
    pytest.param("https://labs.shashkist.com/app", id="path"),
    pytest.param("https://labs.shashkist.com/a/b/", id="deep-path"),
    pytest.param("https://labs.shashkist.com?x=1", id="query"),
    pytest.param("https://labs.shashkist.com/#frag", id="fragment"),
    pytest.param("https://labs.shashkist.com:443", id="https-default-port"),
    pytest.param("http://localhost:80", id="http-default-port"),
    pytest.param("https://user:pw@labs.shashkist.com", id="userinfo"),
    pytest.param("labs.shashkist.com", id="no-scheme"),
    pytest.param("//labs.shashkist.com", id="no-scheme-slashes"),
    pytest.param("https://", id="no-host"),
    pytest.param("labs.shashkist.com:8080", id="host-port-no-scheme"),
]
CLEAN = [
    pytest.param(PUBLIC, id="https"),
    pytest.param("http://localhost:8080", id="http-port"),
    pytest.param("https://labs.shashkist.com:8443", id="https-nondefault-port"),
    pytest.param("http://labs.shashkist.com:443", id="http-port-443"),
    pytest.param("https://labs.shashkist.com:80", id="https-port-80"),
]


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_public_origin_logs_one_startup_warning(make_client, caplog, value):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        warnings = _origin_warnings(caplog)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "not set" not in warnings[0], "that is the unset warning, not a malformed-value one"


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_public_origin_still_starts_the_app(make_client, value):
    # the warning does not change behaviour: the app starts and serves
    client = make_client(value)
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("value", CLEAN)
def test_a_clean_public_origin_logs_no_startup_warning(make_client, caplog, value):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        assert _origin_warnings(caplog) == []


def test_a_bare_trailing_slash_logs_no_malformed_origin_warning(make_client, caplog):
    # the #69 exam ignores a trailing slash on the setting, so it is not malformed
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(PUBLIC + "/")
        assert _origin_warnings(caplog) == []


def test_the_malformed_warning_is_logged_once_not_per_request(make_client, caplog):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        client = make_client("https://labs.shashkist.com/app")
        client.get("/healthz")
        client.get("/healthz")
        assert len(_origin_warnings(caplog)) == 1


@pytest.mark.parametrize("value", [None, ""])
def test_unset_public_origin_keeps_the_single_not_set_warning(make_client, caplog, value):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        warnings = _origin_warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "not set" in warnings[0]
