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
    """Login, start, a hand-made ttyd WebSocket, and a task API whose show request is held.

    The /end reply is held too (until `end_release`, by default at most 3 s, below END_TIMEOUT = 5),
    so a test can send a second SIGTERM while the client is surely still waiting for it.
    """

    def __init__(
        self, answer_hi: bool = True, hold_show: float = 20, hold_end: float = 3
    ) -> None:
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.release = threading.Event()
        self.end_seen = threading.Event()
        self.end_release = threading.Event()
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
                elif self.path.endswith("/end"):
                    outer.end_seen.set()
                    outer.end_release.wait(hold_end)
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
        self.end_release.set()
        self.server.shutdown()
        self.server.server_close()


def _sigterm(script: str, lab: FakeLab):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SMOKE_")}
    env.update(SMOKE_ID="990000000000000000000000000001", SMOKE_PASSWORD="synthetic")
    env["SMOKE_TIMEOUT"] = "60"
    proc = subprocess.Popen(
        [sys.executable, str(SMOKE / script), lab.base],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert lab.ready.wait(20), "the client never reached the stuck step"
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        assert lab.end_seen.wait(5), "the client did not send /end"
        # the /end reply is held: the client is still waiting for it when the second stop lands
        assert proc.poll() is None, "the client exited before the /end reply"
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.3)
        assert proc.poll() is None, "a second SIGTERM during /end killed the client"
        lab.end_release.set()
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
    assert rc == 1 and took < 10, (rc, took)
    assert "stopped" in err and "Traceback" not in err


@posix_only
def test_terminal_smoke_sigterm_with_a_hung_websocket_ends_the_session_once():
    lab = FakeLab(answer_hi=False)
    try:
        rc, took, err = _sigterm("terminal.py", lab)
        assert lab.ends() == 1, lab.calls
    finally:
        lab.close()
    assert rc == 1 and took < 10, (rc, took)
    assert "stopped" in err and "Traceback" not in err


def _lab_api():
    sys.path.insert(0, str(SMOKE))
    try:
        import lab_api
    finally:
        sys.path.remove(str(SMOKE))
    return lab_api


def _kill_self_later(delay: float = 0.3) -> None:
    threading.Timer(delay, os.kill, (os.getpid(), signal.SIGTERM)).start()


@posix_only
def test_on_stop_that_raises_does_not_make_the_runner_wait_for_the_stuck_worker(monkeypatch):
    import asyncio

    lab_api = _lab_api()
    old = signal.getsignal(signal.SIGTERM)
    runners: list[asyncio.Runner] = []
    real_run = asyncio.Runner.run

    def run(self, coro, **kwargs):
        runners.append(self)
        return real_run(self, coro, **kwargs)

    monkeypatch.setattr(asyncio.Runner, "run", run)
    unstick = threading.Event()

    def boom() -> None:
        raise RuntimeError("on_stop failed")

    async def stuck() -> None:
        await asyncio.to_thread(unstick.wait, 4)

    try:
        _kill_self_later()
        t0 = time.monotonic()
        lab_api.run_stoppable(stuck(), boom)  # must not raise
        assert time.monotonic() - t0 < 2
    finally:
        # a stop leaves the loop open (the process leaves at once); clean it up for the next tests
        unstick.set()
        for runner in runners:
            runner.close()
        signal.set_wakeup_fd(-1)
        signal.signal(signal.SIGTERM, old)


@posix_only
def test_after_a_normal_finish_sigterm_is_ignored_before_the_runner_closes(monkeypatch):
    import asyncio

    lab_api = _lab_api()
    old = signal.getsignal(signal.SIGTERM)
    at_close: list[object] = []
    real_close = asyncio.Runner.close

    def close(self) -> None:
        # the runner closes the loop, which may wait for a worker: SIGTERM must be ignored by then
        at_close.append(signal.getsignal(signal.SIGTERM))
        real_close(self)

    monkeypatch.setattr(asyncio.Runner, "close", close)

    async def quick() -> None:
        await asyncio.sleep(0)

    try:
        lab_api.run_stoppable(quick(), lambda: None)
        assert at_close == [signal.SIG_IGN]
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
        assert signal.SIGTERM not in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        os.kill(os.getpid(), signal.SIGTERM)  # would kill the test process if it were the default
        time.sleep(0.1)
    finally:
        signal.signal(signal.SIGTERM, old)


@pytest.mark.parametrize("fails", [False, True], ids=["normal-finish", "error"])
def test_a_held_final_end_still_returns_within_its_bound(monkeypatch, fails):
    """#199: the /end after a normal finish or an error is bounded as a whole, like on a stop."""
    import asyncio

    lab_api = _lab_api()
    # SIGTERM is swallowed while the final /end runs: it must fit in the 10 s of `timeout -k 10`
    assert lab_api.FINAL_END_TIMEOUT < 10
    monkeypatch.setattr(lab_api, "FINAL_END_TIMEOUT", 1)
    old = signal.getsignal(signal.SIGTERM)
    failures: list[tuple[str, str]] = []
    lab = FakeLab(hold_end=10)  # the reply comes only when the test releases it (or after 10 s)

    async def finish() -> None:
        await asyncio.sleep(0)
        if fails:
            raise lab_api.Fail("the lab broke")

    try:
        t0 = time.monotonic()
        try:
            lab_api.run_then_end(
                lab.base, SID, "x", finish(), lambda *f: failures.append(f)
            )
        except lab_api.Fail:
            assert fails
        took = time.monotonic() - t0
        assert lab.end_seen.is_set() and lab.ends() == 1, lab.calls
    finally:
        lab.close()
        signal.signal(signal.SIGTERM, old)
    assert took < 3, took
    assert failures == [("end", "ending the session timed out")]


@posix_only
def test_the_worker_of_an_end_call_never_takes_a_sigterm(monkeypatch):
    """#199: the final /end may outlive its bound; its thread must not take the SIGTERM that
    `_ignore_sigterm` keeps away from the default action."""
    lab_api = _lab_api()
    masks: list[set] = []

    def end_session(*_a, **_k) -> None:
        masks.append(signal.pthread_sigmask(signal.SIG_BLOCK, set()))

    monkeypatch.setattr(lab_api, "end_session", end_session)
    assert lab_api._end_within("http://127.0.0.1:9", SID, "x", 5) is None
    assert signal.SIGTERM in masks[0]
    assert signal.SIGTERM not in signal.pthread_sigmask(signal.SIG_BLOCK, set())


def test_the_end_is_sent_before_the_executor_join(monkeypatch):
    """#203: a worker `coro` left behind must not delay /end: it goes out before the join."""
    import asyncio

    lab_api = _lab_api()
    old = signal.getsignal(signal.SIGTERM)
    lab = FakeLab(hold_end=0.1)
    ev = threading.Event()
    seen_at_release: list[bool] = []

    def release() -> None:
        seen_at_release.append(lab.end_seen.is_set())
        ev.set()

    async def abandon() -> None:
        # the thread outlives the coroutine: only the executor join waits for it
        asyncio.get_running_loop().run_in_executor(None, ev.wait, 6)

    timer = threading.Timer(3, release)
    timer.start()
    try:
        lab_api.run_then_end(lab.base, SID, "x", abandon(), lambda *f: None)
    finally:
        timer.cancel()
        ev.set()
        lab.close()
        signal.signal(signal.SIGTERM, old)
    assert seen_at_release == [True], seen_at_release


def test_end_runs_inline_when_the_worker_thread_cannot_start(monkeypatch):
    lab_api = _lab_api()
    calls: list[tuple] = []

    def end_session(*a, **k) -> str | None:
        calls.append(a)
        return "inline"

    def cannot_start(self) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(lab_api, "end_session", end_session)
    monkeypatch.setattr(threading.Thread, "start", cannot_start)
    assert lab_api._end_within("http://127.0.0.1:9", SID, "x", 5) == "inline"
    assert len(calls) == 1


def test_sigterm_is_ignored_during_the_fallback_end(monkeypatch):
    """#203: the fallback end after a failed runner runs with SIGTERM ignored."""
    lab_api = _lab_api()
    old = signal.getsignal(signal.SIGTERM)
    during: list[object] = []

    def run_stoppable(coro, *_a, **_k) -> None:
        coro.close()
        raise lab_api.Fail("runner broke")

    def end_session(*_a, **_k) -> None:
        during.append(signal.getsignal(signal.SIGTERM))

    async def never() -> None:
        pass

    monkeypatch.setattr(lab_api, "run_stoppable", run_stoppable)
    monkeypatch.setattr(lab_api, "end_session", end_session)
    try:
        with pytest.raises(lab_api.Fail):
            lab_api.run_then_end("http://127.0.0.1:9", SID, "x", never(), lambda *f: None)
    finally:
        signal.signal(signal.SIGTERM, old)
    assert during == [signal.SIG_IGN]
