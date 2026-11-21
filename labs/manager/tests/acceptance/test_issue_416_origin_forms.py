"""Acceptance tests for issue #416 (review leftovers of #410; spec section 5, D117, D119).

Contract this exam fixes, on top of the #69, #373, #399 and #410 exams:

* IPv4-mapped IPv6 hosts: the browser form `https://[::ffff:c0a8:1]` logs no startup warning, the
  dotted form `https://[::ffff:192.168.0.1]` warns. The answers must not depend on how this
  Python's `ipaddress` prints mapped addresses (3.13 prints them dotted), so the expected forms
  are written literally and a case forces the 3.13 spelling on any Python.
* The smoke's own-Origin 403 line names `LF_PUBLIC_ORIGIN` only for an empty body or the JSON
  detail `origin_not_allowed`. Any other body (an nginx HTML page, JSON without a string detail)
  says the 403 did not come from the manager. A detail is printed with `!r`: one line.
* The startup check warns for IPv4 forms a browser rewrites and for a host with a forbidden host
  code point. Canonical values stay quiet.
* CI lints `labs/deploy/smoke`, and the files there pass the manager's ruff config.

The fake manager is the #373/#399/#410 exams'; ids and passwords are fake. No Docker.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import ipaddress
import json
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_issue_69_ws_origin_check import (  # noqa: F401, F811
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

REPO = Path(__file__).resolve().parents[4]
MANAGER = REPO / "labs" / "manager"
SMOKE = REPO / "labs" / "deploy" / "smoke"

# ---------------------------------------------------------------- startup check: AC1


def _warnings_for(make_client, caplog, value):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        return _origin_warnings(caplog)


MAPPED_QUIET = "https://[::ffff:c0a8:1]"
MAPPED_WARN = "https://[::ffff:192.168.0.1]"


def test_the_browser_form_of_a_mapped_ipv6_host_logs_no_startup_warning(make_client, caplog):
    assert _warnings_for(make_client, caplog, MAPPED_QUIET) == []


def test_the_dotted_form_of_a_mapped_ipv6_host_warns(make_client, caplog):
    warnings = _warnings_for(make_client, caplog, MAPPED_WARN)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "not set" not in warnings[0]


_original_str = ipaddress.IPv6Address.__str__


def _dotted_mapped(self) -> str:
    """The Python 3.13+ spelling, written by hand (never from the library's own output)."""
    packed = self.packed
    if packed[:12] == b"\x00" * 10 + b"\xff\xff":
        return "::ffff:" + ".".join(str(b) for b in packed[12:])
    return _original_str(self)


@pytest.fixture
def python_313_ipaddress(monkeypatch):
    monkeypatch.setattr(ipaddress.IPv6Address, "__str__", _dotted_mapped)
    monkeypatch.setattr(ipaddress.IPv6Address, "compressed", property(_dotted_mapped))


@pytest.mark.parametrize(
    "value,warns",
    [
        pytest.param(MAPPED_QUIET, False, id="browser-form-quiet"),
        pytest.param(MAPPED_WARN, True, id="dotted-form-warns"),
        pytest.param("https://[::1]", False, id="loopback-quiet"),
        pytest.param("https://[2001:db8::1]", False, id="compressed-quiet"),
        pytest.param("https://[0:0::1]", True, id="not-compressed-warns"),
    ],
)
def test_the_mapped_answers_hold_when_ipaddress_prints_mapped_addresses_dotted(
    make_client, caplog, python_313_ipaddress, value, warns
):
    warnings = _warnings_for(make_client, caplog, value)
    assert len(warnings) == (1 if warns else 0), [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------- startup check: AC3


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("http://127.1", id="short-ipv4"),
        pytest.param("http://0x7f.0.0.1", id="hex-octet"),
        pytest.param("http://2130706433", id="single-integer"),
        pytest.param("http://010.0.0.1", id="octal-octet"),
        pytest.param("http://127.000.0.1", id="leading-zeros"),
        pytest.param("http://lab s.example", id="space-in-host"),
        pytest.param("http://lab<s.example", id="less-than-in-host"),
        pytest.param("http://256.0.0.1", id="octet-out-of-range"),
        pytest.param("http://999.999.999.999", id="all-octets-out-of-range"),
        pytest.param("http://lab\x7fs.example", id="del-in-host"),
    ],
)
def test_the_startup_check_warns_for_rewritten_ipv4_forms_and_forbidden_host_characters(
    make_client, caplog, value
):
    warnings = _warnings_for(make_client, caplog, value)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "not set" not in warnings[0]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("http://127.0.0.1", id="ipv4"),
        pytest.param("https://labs.shashkist.com", id="hostname"),
        pytest.param("http://localhost:8080", id="localhost-port"),
        pytest.param("https://[::1]", id="ipv6-loopback"),
        pytest.param("https://[2001:db8::1]", id="ipv6-compressed"),
        pytest.param("https://lab1.example.com", id="digit-in-a-label"),
        pytest.param("https://labs.shashkist.com.", id="trailing-dot"),
    ],
)
def test_canonical_hosts_still_log_no_startup_warning(make_client, caplog, value):
    assert _warnings_for(make_client, caplog, value) == []


# ---------------------------------------------------------------- smoke: AC2


class Refusal:
    """Refuses the smoke's own Origin with a 403 whose raw body and content type are set."""

    def __init__(self) -> None:
        self.base = ""
        self.body: bytes = b""
        self.content_type: bytes | None = None
        self.refused = 0


def _with_raw_refusal(app, policy: Refusal):
    async def wrapped(scope, receive, send):
        if scope["type"] == "websocket":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            origin = headers.get("origin")
            if origin is not None and origin.lower().rstrip("/") == policy.base.lower():
                policy.refused += 1
                await receive()  # websocket.connect
                if not policy.body:
                    await send({"type": "websocket.close", "code": 1008})  # 403, no body
                    return
                hdrs = [(b"content-length", str(len(policy.body)).encode())]
                if policy.content_type is not None:
                    hdrs.append((b"content-type", policy.content_type))
                await send(
                    {"type": "websocket.http.response.start", "status": 403, "headers": hdrs}
                )
                await send({"type": "websocket.http.response.body", "body": policy.body})
                return
        await app(scope, receive, send)

    return wrapped


@pytest.fixture(scope="module")
def refusal_server():
    import uvicorn

    fake = Fake()
    policy = Refusal()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    policy.base = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(
        _with_raw_refusal(_build_app(fake), policy),
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


def _run(refusal_server, body: bytes, content_type: bytes | None = b"application/json"):
    fake, policy, base = refusal_server
    fake.reset("good")
    policy.body = body
    policy.content_type = content_type
    policy.refused = 0
    res = run_client(base, timeout="4")
    assert policy.refused >= 1, "the own-Origin upgrade was never refused"
    assert res.returncode != 0, res.stdout + res.stderr
    assert ("end", fake.sid, True) in fake.events, "the session must still be ended"
    assert_clean(res, fake)
    return reason_line(res)


def _detail(value) -> bytes:
    return json.dumps({"detail": value}).encode()


def test_an_origin_not_allowed_403_names_lf_public_origin(refusal_server):
    assert "LF_PUBLIC_ORIGIN" in _run(refusal_server, _detail("origin_not_allowed"))


def test_a_403_with_an_empty_body_names_lf_public_origin(refusal_server):
    assert "LF_PUBLIC_ORIGIN" in _run(refusal_server, b"", None)


def test_an_html_403_says_it_did_not_come_from_the_manager(refusal_server):
    body = b"<html><head><title>403 Forbidden</title></head><body>nginx</body></html>"
    line = _run(refusal_server, body, b"text/html")
    assert "LF_PUBLIC_ORIGIN" not in line, line
    assert "manager" in line, line


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b'{"error": "forbidden"}', id="json-without-detail"),
        pytest.param(b'{"detail": 7}', id="json-detail-not-a-string"),
        pytest.param(b'{"detail": ["origin_not_allowed"]}', id="json-detail-a-list"),
        pytest.param(b'["origin_not_allowed"]', id="json-not-an-object"),
    ],
)
def test_a_json_403_without_a_string_detail_says_it_did_not_come_from_the_manager(
    refusal_server, body
):
    line = _run(refusal_server, body)
    assert "LF_PUBLIC_ORIGIN" not in line, line
    assert "manager" in line, line


def test_a_server_detail_with_a_control_character_stays_on_one_line(refusal_server):
    line = _run(refusal_server, _detail("x\nOK"))
    assert "\n" not in line and "\r" not in line, repr(line)
    assert "LF_PUBLIC_ORIGIN" not in line, line
    assert "x\\nOK" in line, line


# ---------------------------------------------------------------- CI lint: AC4


def test_ci_runs_ruff_check_over_the_smoke_directory():
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    commands = [ln for ln in text.splitlines() if re.search(r"\bruff\s+check\b", ln)]
    assert any(re.search(r"deploy/smoke", ln) for ln in commands), (
        f"no ruff check command mentions labs/deploy/smoke: {commands}"
    )


def test_the_smoke_scripts_pass_ruff_check_with_the_manager_config():
    res = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(SMOKE)],
        cwd=MANAGER,
        capture_output=True,
        text=True,
    )
    if "No module named ruff" in res.stderr:
        pytest.skip("ruff is not installed in this environment")
    assert res.returncode == 0, res.stdout + res.stderr
