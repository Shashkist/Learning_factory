"""Login and session start shared by the smoke clients terminal.py and tasks.py (issue #138).

Both clients are run as `python labs/deploy/smoke/<name>.py`, so they import this sibling module.
Never prints or logs the password or the session cookie.
"""
import http.client
import http.cookies
import json
import urllib.error
import urllib.request

COOKIE = "lf_session"
HTTP_TIMEOUT = 30


class Fail(Exception):
    pass


def request(base: str, method: str, path: str, cookie: str | None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if cookie:
        headers["Cookie"] = f"{COOKIE}={cookie}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
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
