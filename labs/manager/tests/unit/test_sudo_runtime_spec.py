"""DockerRuntime.start only creates a sudo lab on a gVisor runtime (#652)."""
import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from app.runtime import DockerRuntime, container_spec


def _daemon(info):
    """A fake daemon; `info` is (status, raw bytes) or "hang". Returns (server, created bodies)."""
    created: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, raw=b""):
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if info == "hang":
                threading.Event().wait(2)
                return
            self._send(*info)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n)
            if self.path.split("?")[0].endswith("/containers/create"):
                created.append(json.loads(body))
                self._send(201, b'{"Id":"x"}')
            else:
                self._send(204)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, created


def _runtimes(**paths):
    return 200, json.dumps({"Runtimes": {k: {"path": v} for k, v in paths.items()}}).encode()


def _start(monkeypatch, caplog, info, wanted):
    monkeypatch.setenv("LF_SUDO_RUNTIME", wanted)
    srv, created = _daemon(info)
    try:
        monkeypatch.setenv("LF_DOCKER_HOST", f"tcp://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.setattr("app.runtime._TIMEOUT", 0.5)
        lab = SimpleNamespace(image="term", sudo=True, time_limit_min=5)
        spec = container_spec(lab, "s1")  # type: ignore[arg-type]
        with caplog.at_level(logging.INFO, logger="lab-manager"):
            caplog.clear()
            asyncio.run(DockerRuntime().start(spec))
        lines = [r for r in caplog.records if r.name == "lab-manager"]
        return created[0]["HostConfig"]["Runtime"], lines
    finally:
        srv.shutdown()
        srv.server_close()


def test_runc_override_never_reaches_the_create_body(monkeypatch, caplog):
    info = _runtimes(runc="/usr/bin/runc", runsc="/x/runsc")
    rt, lines = _start(monkeypatch, caplog, info, "runc")
    assert rt == "runsc"
    assert len(lines) == 1 and "runc" in lines[0].getMessage()


def test_listed_runtime_with_a_non_runsc_path_is_refused(monkeypatch, caplog):
    rt, lines = _start(monkeypatch, caplog, _runtimes(custom="/usr/bin/crun"), "custom")
    assert rt == "runsc"
    assert len(lines) == 1


def test_gvisor_runtime_with_runtime_args_is_used(monkeypatch, caplog):
    info = _runtimes(**{"runsc-suid": "/usr/local/bin/runsc"})
    rt, lines = _start(monkeypatch, caplog, info, "runsc-suid")
    assert rt == "runsc-suid" and lines == []


@pytest.mark.parametrize(
    "info",
    [(500, b"{}"), (200, b"not json"), (200, b"[]"), (200, b'{"Runtimes": []}'), "hang"],
    ids=["500", "non-json", "not-object", "runtimes-not-object", "timeout"],
)
def test_unreadable_info_degrades_to_runsc(monkeypatch, caplog, info):
    rt, lines = _start(monkeypatch, caplog, info, "runsc-suid")
    assert rt == "runsc"
    assert len(lines) == 1 and "runsc-suid" in lines[0].getMessage()
