"""Acceptance tests for issue #410 (review leftovers of #409; D117, D119).

Contract this exam fixes, on top of the #69, #373 and #399 exams:

* The `LF_PUBLIC_ORIGIN` startup check warns (one line naming the setting) when the host is not
  what a browser sends: a leading-zero port (`:08443`), a control character such as a tab inside
  the host, a non-ASCII (IDN) host, a `%` in the host, or an IPv6 literal whose compressed form
  differs from what was written. Canonical values stay quiet.
* The smoke's own-Origin 403 failure line names `LF_PUBLIC_ORIGIN` only when the 403 body's
  detail is `origin_not_allowed` or there is no body. A `not_your_session` 403 names that.

The fake manager is the #373/#399 exams'; ids and passwords are fake. No Docker.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import json
import logging
import socket
import threading
import time

import pytest
from test_issue_69_ws_origin_check import (  # noqa: F401, F811
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

# ---------------------------------------------------------------- startup check: AC1, AC2


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("https://labs.shashkist.com:08443", id="leading-zero-port"),
        pytest.param("https://labs\t.shashkist.com", id="tab-inside-host"),
        pytest.param("https://lab\ts.shashkist.com:8443", id="tab-inside-host-with-port"),
    ],
)
def test_the_startup_check_warns_on_a_leading_zero_port_or_a_tab_in_the_host(
    make_client, caplog, value
):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        warnings = _origin_warnings(caplog)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "not set" not in warnings[0]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("https://läbs.shashkist.com", id="idn-host"),
        pytest.param("https://מעבדה.example", id="hebrew-host"),
        pytest.param("https://labs%2eshashkist.com", id="percent-in-host"),
        pytest.param("https://labs.shashkist.com%", id="trailing-percent-in-host"),
        pytest.param("https://[0:0::1]", id="ipv6-not-compressed"),
        pytest.param("https://[::ffff:192.168.0.1]", id="ipv6-mapped-dotted"),
        pytest.param("http://[0:0:0:0:0:0:0:1]:8080", id="ipv6-expanded-with-port"),
    ],
)
def test_the_startup_check_warns_when_the_host_is_not_what_a_browser_sends(
    make_client, caplog, value
):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        warnings = _origin_warnings(caplog)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "not set" not in warnings[0]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(PUBLIC, id="plain"),
        pytest.param("https://labs.shashkist.com", id="no-port"),
        pytest.param("http://127.0.0.1:8080", id="ipv4"),
        pytest.param("http://localhost:3000", id="localhost"),
        pytest.param("https://[::1]", id="ipv6-loopback"),
        pytest.param("https://[::1]:8443", id="ipv6-loopback-port"),
        pytest.param("https://[2001:db8::1]", id="ipv6-compressed"),
        pytest.param("HTTPS://Labs.Shashkist.com", id="upper-case"),
        pytest.param("https://labs.shashkist.com:8443/", id="port-and-slash"),
        pytest.param("https://labs.shashkist.com//", id="double-trailing-slash"),
        pytest.param("https://xn--lbs-qla.shashkist.com", id="punycode"),
    ],
)
def test_canonical_values_still_log_no_startup_warning(make_client, caplog, value):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        assert _origin_warnings(caplog) == []


# ---------------------------------------------------------------- smoke: AC3


class OwnOriginRefusal:
    """Refuses the smoke's own Origin with 403 and the given JSON detail (None = no body)."""

    def __init__(self) -> None:
        self.base = ""
        self.detail: str | None = None
        self.refused = 0


def _with_own_origin_refusal(app, policy: OwnOriginRefusal):
    async def wrapped(scope, receive, send):
        if scope["type"] == "websocket":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            origin = headers.get("origin")
            if origin is not None and origin.lower().rstrip("/") == policy.base.lower():
                policy.refused += 1
                await receive()  # websocket.connect
                if policy.detail is None:
                    await send({"type": "websocket.close", "code": 1008})  # HTTP 403, no body
                    return
                body = json.dumps({"detail": policy.detail}).encode()
                await send(
                    {
                        "type": "websocket.http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "websocket.http.response.body", "body": body})
                return
        await app(scope, receive, send)

    return wrapped


@pytest.fixture(scope="module")
def refusal_server():
    import uvicorn

    fake = Fake()
    policy = OwnOriginRefusal()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    policy.base = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(
        _with_own_origin_refusal(_build_app(fake), policy),
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


def _run(refusal_server, detail):
    fake, policy, base = refusal_server
    fake.reset("good")
    policy.detail = detail
    policy.refused = 0
    res = run_client(base, timeout="4")
    assert policy.refused >= 1, "the own-Origin upgrade was never refused"
    assert res.returncode != 0, res.stdout + res.stderr
    assert ("end", fake.sid, True) in fake.events, "the session must still be ended"
    assert_clean(res, fake)
    return reason_line(res)


def test_an_origin_not_allowed_403_names_lf_public_origin(refusal_server):
    assert "LF_PUBLIC_ORIGIN" in _run(refusal_server, "origin_not_allowed")


def test_a_403_without_a_body_names_lf_public_origin(refusal_server):
    assert "LF_PUBLIC_ORIGIN" in _run(refusal_server, None)


def test_a_not_your_session_403_does_not_name_lf_public_origin(refusal_server):
    line = _run(refusal_server, "not_your_session")
    assert "LF_PUBLIC_ORIGIN" not in line, line
    assert "not_your_session" in line, line
