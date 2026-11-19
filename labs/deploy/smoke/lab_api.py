"""Login and session start shared by the smoke clients terminal.py and tasks.py (issue #138).

Both clients are run as `python labs/deploy/smoke/<name>.py`, so they import this sibling module.
Never prints or logs the password or the session cookie.
"""
import asyncio
import contextlib
import http.client
import http.cookies
import json
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Coroutine, Iterator
from typing import Any

COOKIE = "lf_session"
HTTP_TIMEOUT = 30
# total deadline of the /end sent on SIGTERM; it must fit in the 10 s of `timeout -k 10`
END_TIMEOUT = 5
# total deadline of the /end after a normal finish or an error: a SIGTERM that lands meanwhile is
# swallowed, so it must fit in the same 10 s (a bit more room: this one decides the result)
FINAL_END_TIMEOUT = 8
STOPPED = "the smoke was stopped by SIGTERM"
# the step name `run_then_end` reports a SIGTERM stop with
STOP_STEP = "stopped"


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


@contextlib.contextmanager
def _sigterm_blocked() -> Iterator[None]:
    """Block SIGTERM in this thread for the body (a no-op where there is no pthread_sigmask)."""
    mask = None
    if hasattr(signal, "pthread_sigmask") and hasattr(signal, "SIGTERM"):
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        yield
    finally:
        if mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def _end_within(base: str, sid: str, cookie: str, total: float) -> str | None:
    """`end_session` bounded as a whole (a socket timeout is per operation: DNS, connect, TLS)."""
    out: list[str | None] = []
    worker = threading.Thread(
        target=lambda: out.append(end_session(base, sid, cookie, total)), daemon=True
    )
    # a thread starts with its creator's signal mask: this one never takes a SIGTERM, even when it
    # outlives the bound (`_ignore_sigterm` relies on that after the final end)
    try:
        with _sigterm_blocked():
            worker.start()
    except RuntimeError:  # can't start new thread: end inline, unbounded but still ends the session
        return end_session(base, sid, cookie, total)
    worker.join(total)
    return out[0] if out else "ending the session timed out"


def run_then_end(
    base: str, sid: str, cookie: str, coro: Coroutine[Any, Any, None],
    on_failure: Callable[[str, str], None],
) -> None:
    """Run `coro`, then end the session once: after it, after an error, or at once on SIGTERM.

    `on_failure(step, reason)` is called with (STOP_STEP, STOPPED) on SIGTERM and with
    ("end", reason) when the end call fails; the caller keeps the first one. An error of `coro`
    propagates after the session was ended. After a stop the caller must report and `leave()`.
    Each end call is bounded as a whole: END_TIMEOUT on a stop, FINAL_END_TIMEOUT otherwise.
    """
    ended = False

    def end(total: float) -> None:
        nonlocal ended
        if ended:
            return
        ended = True
        reason = _end_within(base, sid, cookie, total)
        if reason is not None:
            on_failure("end", reason)

    def stop() -> None:
        try:
            on_failure(STOP_STEP, STOPPED)
        finally:
            end(END_TIMEOUT)

    try:
        run_stoppable(coro, stop, lambda: end(FINAL_END_TIMEOUT))
    finally:
        if not ended:
            # the loop is gone: a SIGTERM during this end must not take the default action
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
        end(FINAL_END_TIMEOUT)  # only if the runner failed before it got there


def run_stoppable(
    coro: Coroutine[Any, Any, None], on_stop: Callable[[], None],
    on_done: Callable[[], None] | None = None,
) -> None:
    """Run `coro`; on SIGTERM call `on_stop()` at once, inside the running loop.

    Everything after SIGTERM must fit in the 10 s grace of `timeout -k 10`, so the loop is not
    shut down on a stop (that would wait for a worker thread stuck in a 30 s request): the caller
    reports and ends the process with `leave()`. `on_stop` can never raise. After a normal finish
    or an error, `on_done()` runs first, still inside the loop; then the loop's executor threads
    are joined, which can wait for a worker `coro` left behind (a timed-out request, a DNS lookup
    of a websocket connect), and only then is the loop shut down. A SIGTERM during `on_done` or
    the join is swallowed, so `on_done` must be bounded to fit in the grace. A second SIGTERM is
    ignored too. Where the loop cannot take signal handlers (Windows) this just runs `coro`.
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
                try:
                    if on_done is not None:
                        on_done()  # before the join below: nothing bounds how long that takes
                finally:
                    # join the executor threads, so that only this thread can take a SIGTERM
                    # while the loop handler gives way to SIG_IGN. They are not always idle: a
                    # worker `coro` abandoned (still in a request or a DNS lookup) is waited
                    # for, and a SIGTERM meanwhile is swallowed, which is harmless once
                    # `on_done` has ended the session.
                    await loop.shutdown_default_executor()
                    _ignore_sigterm(loop)

    runner = asyncio.Runner()
    stopped = False
    try:
        stopped = runner.run(main())
    finally:
        if not stopped:
            runner.close()


def _ignore_sigterm(loop: asyncio.AbstractEventLoop) -> None:
    """Replace the loop's SIGTERM handler with SIG_IGN, leaving no moment for the default action.

    `remove_signal_handler` restores the default before SIG_IGN can be set, so SIGTERM is blocked
    in this thread meanwhile: one that arrives then stays pending and is dropped once ignored. The
    caller has joined the executor threads, and the worker of an end call blocks SIGTERM, so no
    other smoke thread is there to take it instead.
    """
    if not hasattr(signal, "SIGTERM"):
        return
    with _sigterm_blocked():
        try:
            loop.remove_signal_handler(signal.SIGTERM)
        except (NotImplementedError, AttributeError, ValueError, RuntimeError):
            pass
        signal.signal(signal.SIGTERM, signal.SIG_IGN)


def leave(code: int) -> None:
    """Exit now, without waiting for worker threads (used after a stop)."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
