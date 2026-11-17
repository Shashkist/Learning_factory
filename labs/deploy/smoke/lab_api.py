"""Login and session start shared by the smoke clients terminal.py and tasks.py (issue #138).

Both clients are run as `python labs/deploy/smoke/<name>.py`, so they import this sibling module.
Never prints or logs the password or the session cookie.
"""
import asyncio
import http.client
import http.cookies
import json
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Coroutine
from typing import Any

COOKIE = "lf_session"
HTTP_TIMEOUT = 30
# total deadline of the /end sent on SIGTERM; it must fit in the 10 s of `timeout -k 10`
END_TIMEOUT = 5
STOPPED = "the smoke was stopped by SIGTERM"


class Fail(Exception):
    pass


def request(
    base: str, method: str, path: str, cookie: str | None, body: dict | None = None,
    timeout: float = HTTP_TIMEOUT,
):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if cookie:
        headers["Cookie"] = f"{COOKIE}={cookie}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, b""
    except (urllib.error.URLError, OSError) as exc:
        raise Fail(f"{method} {path}: server unreachable ({type(exc).__name__})") from None
    except http.client.HTTPException as exc:
        raise Fail(f"{method} {path}: bad http answer ({type(exc).__name__})") from None


def login(base: str, student: str, password: str) -> str:
    """Log in; return the session cookie value or raise Fail with the reason."""
    status, headers, _ = request(
        base, "POST", "/api/auth/login", None, {"id": student, "password": password}
    )
    if status != 200:
        raise Fail(f"login failed (http {status})")
    jar: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
    for value in headers.get_all("Set-Cookie") or []:
        jar.load(value)
    if COOKIE not in jar:
        raise Fail("login set no session cookie")
    return jar[COOKIE].value


def start(base: str, cookie: str, lab_id: str) -> str:
    """Start a session of `lab_id`; return its id or raise Fail with the reason."""
    status, _, raw = request(base, "POST", "/api/sessions", cookie, {"lab_id": lab_id})
    if status not in (200, 201):
        raise Fail(f"starting the lab failed (http {status})")
    try:
        return str(json.loads(raw)["id"])
    except (ValueError, KeyError, TypeError):
        raise Fail("starting the lab returned no session id") from None


def end_session(base: str, sid: str, cookie: str, timeout: float = HTTP_TIMEOUT) -> str | None:
    """End the session; return the reason it failed, or None."""
    try:
        status, _, _ = request(base, "POST", f"/api/sessions/{sid}/end", cookie, timeout=timeout)
    except Fail as exc:
        return str(exc)
    return None if status in (200, 204) else f"ending the session failed (http {status})"


def _end_within(base: str, sid: str, cookie: str, total: float) -> str | None:
    """`end_session` bounded as a whole (a socket timeout is per operation: DNS, connect, TLS)."""
    out: list[str | None] = []
    worker = threading.Thread(
        target=lambda: out.append(end_session(base, sid, cookie, total)), daemon=True
    )
    worker.start()
    worker.join(total)
    return out[0] if out else "ending the session timed out"


def run_then_end(
    base: str, sid: str, cookie: str, coro: Coroutine[Any, Any, None],
    on_failure: Callable[[str, str], None],
) -> None:
    """Run `coro`, then end the session once: after it, after an error, or at once on SIGTERM.

    `on_failure(step, reason)` is called with ("stopped", ...) on SIGTERM and with ("end", reason)
    when the end call fails; the caller keeps the first one. An error of `coro` propagates after
    the session was ended. After a stop the caller must report and `leave()`.
    """
    ended = False

    def end(total: float | None) -> None:
        nonlocal ended
        ended = True
        reason = end_session(base, sid, cookie) if total is None else _end_within(
            base, sid, cookie, total
        )
        if reason is not None:
            on_failure("end", reason)

    def stop() -> None:
        try:
            on_failure("stopped", STOPPED)
        finally:
            end(END_TIMEOUT)

    try:
        run_stoppable(coro, stop)
    finally:
        if not ended:
            end(None)


def run_stoppable(coro: Coroutine[Any, Any, None], on_stop: Callable[[], None]) -> None:
    """Run `coro`; on SIGTERM call `on_stop()` at once, inside the running loop.

    Everything after SIGTERM must fit in the 10 s grace of `timeout -k 10`, so the loop is not
    shut down on a stop (that would wait for a worker thread stuck in a 30 s request): the caller
    reports and ends the process with `leave()`. `on_stop` can never raise. A second SIGTERM is
    ignored, and so is one after a normal finish (the end call must not be skipped). Where the
    loop cannot take signal handlers (Windows) this just runs `coro`.
    """

    async def main() -> bool:
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        try:
            loop.add_signal_handler(signal.SIGTERM, stop.set)
        except (NotImplementedError, AttributeError, ValueError, RuntimeError):
            pass
        task = asyncio.ensure_future(coro)
        waiter = asyncio.ensure_future(stop.wait())
        stopped = False
        try:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                task.result()
                return False
            stopped = True
            task.cancel()
            try:
                loop.add_signal_handler(signal.SIGTERM, lambda: None)
            except (NotImplementedError, AttributeError, ValueError, RuntimeError):
                pass
            try:
                on_stop()
            except Exception:  # noqa: BLE001
                pass
            return True
        finally:
            waiter.cancel()
            if not stopped:
                # remove the loop handler (that restores the default) and ignore at once: there
                # must be no gap in which a SIGTERM kills the process before /end
                try:
                    loop.remove_signal_handler(signal.SIGTERM)
                except (NotImplementedError, AttributeError, ValueError, RuntimeError):
                    pass
                if hasattr(signal, "SIGTERM"):
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    runner = asyncio.Runner()
    stopped = False
    try:
        stopped = runner.run(main())
    finally:
        if not stopped:
            runner.close()


def leave(code: int) -> None:
    """Exit now, without waiting for worker threads (used after a stop)."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
