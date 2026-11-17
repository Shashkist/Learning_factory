"""SIGTERM while a request or the terminal is stuck (#156): /end once, inside the grace."""
import base64
import hashlib
import http.server
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
SMOKE = ROOT / "labs" / "deploy" / "smoke"

SID = "44444444-4444-4444-8444-444444444444"
PROMPT_OUT = b"0u@lab:~$ "
HI_OUT = b"0echo hi\r\nhi\r\nu@lab:~$ "
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs a catchable SIGTERM")


def _frame(payload: bytes) -> bytes:
    return bytes([0x82, len(payload)]) + payload


def _read_frame(rfile) -> bytes | None:
    head = rfile.read(2)
    if len(head) < 2:
        return None
    n = head[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", rfile.read(2))[0]
    mask = rfile.read(4)
    data = rfile.read(n)
    return bytes(b ^ mask[i % 4] for i, b in enumerate(data))


class FakeLab:
    """Login, start, a hand-made ttyd WebSocket, and a task API whose show request is held."""

    def __init__(self, answer_hi: bool = True, hold_show: float = 20) -> None:
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.release = threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, status, body=b"", headers=()):
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                with outer.lock:
                    outer.calls.append(self.path)
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/api/auth/login":
                    self._send(200, b"{}", [("Set-Cookie", "lf_session=x; Path=/")])
                elif self.path == "/api/sessions":
                    self._send(200, json.dumps({"id": SID}).encode())
                elif self.path.endswith("/show"):
                    outer.ready.set()
                    outer.release.wait(hold_show)
                    self._send(200, b"{}")
                else:
                    self._send(200, b"{}")

            def do_GET(self):  # noqa: N802
                key = self.headers["Sec-WebSocket-Key"]
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
                ).decode()
                self.wfile.write(
                    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                    b"Connection: Upgrade\r\nSec-WebSocket-Protocol: tty\r\n"
                    + f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
                )
                self.wfile.flush()
                _read_frame(self.rfile)  # the auth json
                self.wfile.write(_frame(PROMPT_OUT))
                self.wfile.flush()
                while (msg := _read_frame(self.rfile)) is not None:
                    if msg.startswith(b"0echo hi"):
                        if not answer_hi:
                            outer.ready.set()
                            outer.release.wait(60)
                            return
                        self.wfile.write(_frame(HI_OUT))
                        self.wfile.flush()
                self.close_connection = True

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def ends(self) -> int:
        with self.lock:
            return self.calls.count(f"/api/sessions/{SID}/end")

    def close(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


def _sigterm(script: str, lab: FakeLab):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SMOKE_")}
    env.update(SMOKE_ID="990000000000000000000000000001", SMOKE_PASSWORD="synthetic")
    env["SMOKE_TIMEOUT"] = "60"
    proc = subprocess.Popen(
        [sys.executable, str(SMOKE / script), lab.base],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert lab.ready.wait(20), "the client never reached the stuck step"
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.send_signal(signal.SIGTERM)  # a second stop is ignored
        try:
            _, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("the client did not exit within 10 s of SIGTERM")
        return proc.returncode, time.monotonic() - t0, err.decode(errors="replace")
    finally:
        if proc.poll() is None:
            proc.kill()


@posix_only
def test_sigterm_during_a_stuck_task_request_ends_the_session_once_within_the_grace():
    lab = FakeLab(hold_show=20)
    try:
        rc, took, err = _sigterm("tasks.py", lab)
        assert lab.ends() == 1, lab.calls
    finally:
        lab.close()
    assert rc != 0 and took < 10
    assert "stopped" in err and "Traceback" not in err


@posix_only
def test_terminal_smoke_sigterm_with_a_hung_websocket_ends_the_session_once():
    lab = FakeLab(answer_hi=False)
    try:
        rc, took, err = _sigterm("terminal.py", lab)
        assert lab.ends() == 1, lab.calls
    finally:
        lab.close()
    assert rc != 0 and took < 10
    assert "Traceback" not in err
