"""Live terminal smoke client (issue #86, decision D36).

Logs in as the synthetic smoke student, starts the sample lab, opens the terminal WebSocket
through the public URL, waits for the prompt, types `echo hi`, sees `hi` and ends the session.

    SMOKE_ID=... SMOKE_PASSWORD=... [SMOKE_TIMEOUT=60] python terminal.py [BASE_URL]

Prints the session id on stdout; on failure exits non-zero with one reason line on stderr.
Never prints the password, the cookie or a traceback. Needs the `websockets` package (>=14).

A SIGTERM before the stop handler is installed (during login and start, up to opening the
terminal) kills the process at once: a session the server already started is left to the
janitor (its id may not be known yet, so it cannot be ended).
"""
import asyncio
import json
import os
import re
import sys

from lab_api import (
    COOKIE,
    STOPPED,
    Fail,
    leave,
    login,
    run_then_end,
    start,
)

DEFAULT_BASE = "https://labs.shashkist.com"
LAB_ID = "hello"
# a line that is just `hi`, after the newline of the typed command (bash may emit CSI and \r first)
HI_LINE = re.compile(r"\n(?:\x1b\[[0-9;?]*[ -/]*[@-~]|\r)*hi\r?\n")


def _output(msg: bytes | str) -> str:
    """Terminal output of a ttyd frame ('0' + bytes), or '' for any other frame."""
    data = msg if isinstance(msg, bytes) else msg.encode()
    return data[1:].decode("utf-8", "replace") if data[:1] == b"0" else ""


async def _open(url: str, cookie: str, timeout: float):  # type: ignore[no-untyped-def]
    """Open the terminal WebSocket.

    Right after the lab starts, ttyd may not listen yet and the manager answers 502 (or 503/504
    from nginx): retry those until `timeout`, fail at once on anything else."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            return await connect(
                url,
                subprotocols=["tty"],
                additional_headers={"Cookie": f"{COOKIE}={cookie}"},
                open_timeout=timeout,
            )
        except InvalidStatus as exc:
            if exc.response.status_code not in (502, 503, 504) or loop.time() >= deadline:
                raise
        await asyncio.sleep(1)


async def _terminal(base: str, sid: str, cookie: str, timeout: float) -> None:
    url = ("wss" if base.startswith("https") else "ws") + base[base.index("://") :]
    try:
        async with await _open(f"{url}/s/{sid}/term/ws", cookie, timeout) as ws:
            await ws.send(json.dumps({"AuthToken": "", "columns": 80, "rows": 24}).encode())
            buf = ""
            try:
                async with asyncio.timeout(timeout):
                    while not buf.endswith("$ "):  # input before the prompt is dropped (D33)
                        buf += _output(await ws.recv())
            except TimeoutError:
                raise Fail("no shell prompt in the terminal") from None
            await ws.send(b"0echo hi\r")
            buf = ""
            try:
                async with asyncio.timeout(timeout):
                    while not HI_LINE.search(buf):
                        buf += _output(await ws.recv())
            except TimeoutError:
                raise Fail("the terminal never answered hi to echo hi") from None
    except Fail:
        raise
    except Exception as exc:  # noqa: BLE001
        raise Fail(f"terminal websocket failed ({type(exc).__name__})") from None


def run(base: str, student: str, password: str, timeout: float) -> str | None:
    """Run the smoke; return the reason it failed, or None."""
    cookie = login(base, student, password)
    sid = start(base, cookie, LAB_ID)
    print(sid, flush=True)
    failure: str | None = None

    def on_failure(_step: str, reason: str) -> None:
        nonlocal failure
        failure = failure or reason

    try:
        run_then_end(base, sid, cookie, _terminal(base, sid, cookie, timeout), on_failure)
    except Fail as exc:
        failure = str(exc)
    return failure


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: terminal.py [BASE_URL] (credentials: environment only)", file=sys.stderr)
        return 2
    base = (argv[1] if len(argv) == 2 else DEFAULT_BASE).rstrip("/")
    student = os.environ.get("SMOKE_ID", "")
    password = os.environ.get("SMOKE_PASSWORD", "")
    for name, value in (("SMOKE_ID", student), ("SMOKE_PASSWORD", password)):
        if not value:
            print(f"{name} is not set", file=sys.stderr)
            return 2
    try:
        timeout = float(os.environ.get("SMOKE_TIMEOUT", "60"))
    except ValueError:
        print("SMOKE_TIMEOUT must be a number of seconds", file=sys.stderr)
        return 2
    if not base.startswith(("http://", "https://")):
        print("BASE_URL must start with http:// or https://", file=sys.stderr)
        return 2
    try:
        failure = run(base, student, password, timeout)
    except Fail as exc:
        failure = str(exc)
    if failure:
        print(f"smoke failed: {failure}", file=sys.stderr)
        if failure == STOPPED:
            leave(1)  # do not wait for a worker thread or a websocket close
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
