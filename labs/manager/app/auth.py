"""Login for students (id + class password) and admins; server-side sessions (D23).

The raw student id is only ever held in memory while a request is handled: it is
normalized, hashed with HMAC-SHA256 and dropped. Logs name at most 8 hex chars of a hash.
"""
import asyncio
import contextlib
import datetime as dt
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from app import db

log = logging.getLogger("lab-manager")

COOKIE = "lf_session"
SESSION_SECONDS = 12 * 3600
MAX_FAILURES = 10
WINDOW_SECONDS = 15 * 60
MAX_KEYS = 10_000
SWEEP_AT = 1_000
SWEEP_INTERVAL = 5.0
VERIFY_CONCURRENCY = 2
VERIFY_WAIT = 2.0
MAX_ID_LEN = 32
MAX_USERNAME_LEN = 64
T = db.SCHEMA

_hasher = PasswordHasher()
_dummy_hash: str | None = None


# ---------------------------------------------------------------- ids and passwords


def normalize(raw_id: str) -> str:
    """Remove all whitespace, then leading zeros."""
    return "".join(raw_id.split()).lstrip("0")


def id_hash(raw_id: str, secret: str) -> str:
    """Lowercase hex HMAC-SHA256 of the normalized id, or "" when nothing is left."""
    norm = normalize(raw_id)
    if not norm:
        return ""
    return hmac.new(secret.encode(), norm.encode(), hashlib.sha256).hexdigest()


def valid_id(raw_id: str) -> bool:
    """A student id is valid when, once normalized, it is 1..32 ASCII digits.

    Cohort/student import must apply the same rule, or a student could not log in."""
    norm = normalize(raw_id)
    return norm.isascii() and norm.isdigit() and len(norm) <= MAX_ID_LEN


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _verify_sync(stored: str | None, password: str) -> bool:
    """Verify against the stored hash; a missing one is checked against a dummy (same cost)."""
    global _dummy_hash
    if stored is None:
        if _dummy_hash is None:
            _dummy_hash = _hasher.hash(secrets.token_hex(16))
        try:
            _hasher.verify(_dummy_hash, password)
        except (VerificationError, InvalidHashError):
            pass
        return False
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


_verify_gate: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def _gate() -> asyncio.Semaphore:
    """One semaphore per running event loop (tests start several loops)."""
    global _verify_gate
    loop = asyncio.get_running_loop()
    if _verify_gate is None or _verify_gate[0] is not loop:
        _verify_gate = (loop, asyncio.Semaphore(VERIFY_CONCURRENCY))
    return _verify_gate[1]


async def verify_password(stored: str | None, password: str) -> bool:
    """Argon2 check with bounded concurrency; 503 busy when no slot frees up in time."""
    gate = _gate()
    try:
        await asyncio.wait_for(gate.acquire(), VERIFY_WAIT)
    except TimeoutError:
        raise HTTPException(503, "busy") from None
    try:
        return await asyncio.to_thread(_verify_sync, stored, password)
    finally:
        gate.release()


# ---------------------------------------------------------------- rate limiter


class RateLimiter:
    """In-process sliding window of attempts per key, with per-key limits."""

    def __init__(
        self,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_KEYS,
        sweep_at: int = SWEEP_AT,
        sweep_interval: float = SWEEP_INTERVAL,
    ) -> None:
        self.window = window
        self._clock = clock
        self.max_keys = max_keys
        self.sweep_at = sweep_at
        self.sweep_interval = sweep_interval
        self._last_sweep = float("-inf")
        self._events: dict[str, deque[float]] = {}
        self._limits: dict[str, int] = {}

    def _drop(self, key: str) -> None:
        self._events.pop(key, None)
        self._limits.pop(key, None)

    def _count(self, key: str) -> int:
        q = self._events.get(key)
        if q is None:
            return 0
        cutoff = self._clock() - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            self._drop(key)
        return len(q)

    _CLIENT_KEY_PREFIXES = ("ip:", "gcb:", "gstart:")

    def _sweep(self) -> None:
        """Drop expired keys; over the cap, evict per-client keys (ip:, gcb:, gstart:) first,
        oldest first: anyone can mint them cheaply. A key at or above its limit with a live
        window is never evicted (that would unblock it)."""
        for key in list(self._events):
            self._count(key)
        for prefer_ip in (True, False):
            for key in list(self._events):
                if len(self._events) <= self.max_keys:
                    return
                if prefer_ip != key.startswith(self._CLIENT_KEY_PREFIXES):
                    continue
                if len(self._events[key]) >= self._limits.get(key, 1 << 30):
                    continue
                self._drop(key)

    def hit(self, *keys: tuple[str, int]) -> bool:
        """Check and record in one synchronous step. False (nothing recorded) when over a limit."""
        live = [(k, limit) for k, limit in keys if k]
        if any(self._count(k) >= limit for k, limit in live):
            return False
        now = self._clock()
        for k, limit in live:
            self._events.setdefault(k, deque()).append(now)
            self._limits[k] = limit
        if len(self._events) > self.sweep_at and now - self._last_sweep >= self.sweep_interval:
            self._last_sweep = now
            self._sweep()
        return True

    def forgive(self, *keys: tuple[str, int]) -> None:
        """Remove the most recent attempt of each key (a success or a busy answer)."""
        for k, _ in keys:
            q = self._events.get(k)
            if q:
                q.pop()
                if not q:
                    self._drop(k)


def _env_limit(name: str, default: int = MAX_FAILURES) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


limiter = RateLimiter()


def _group_ip(ip: str) -> str:
    """IPv6 addresses count per /64 (one host owns a whole /64); anything else as is."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return ip


def client_ip(request: HTTPConnection) -> str:
    """The socket peer; X-Forwarded-For (last hop) only when the peer is LF_TRUSTED_PROXY.

    IPv6 addresses are grouped by /64."""
    peer = request.client.host if request.client else "unknown"
    trusted = os.environ.get("LF_TRUSTED_PROXY")
    if trusted and peer == trusted:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
        hops = [h for h in hops if h]
        if hops:
            return _group_ip(hops[-1])
    return _group_ip(peer)


# ---------------------------------------------------------------- google admin config (D39)


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str
    emails: frozenset[str]
    redirect_uri: str


_BOM = "\ufeff"


def _clean(value: str) -> str:
    """Whitespace and a byte-order mark removed (PowerShell 5.1 often saves values with one)."""
    return value.strip().lstrip(_BOM).strip()


# The allow-list value whose dropped entries were last logged (compared as is: hashing it
# would raise on undecodable environment bytes, which Python keeps as lone surrogates).
_dropped_logged_for: str | None = None


def google_config() -> GoogleConfig | None:
    """Configured means client id, client secret and a non-empty admin allow-list are all set."""
    cid = _clean(os.environ.get("LF_GOOGLE_CLIENT_ID", ""))
    secret = _clean(os.environ.get("LF_GOOGLE_CLIENT_SECRET", ""))
    raw = _clean(os.environ.get("LF_ADMIN_EMAILS", ""))
    entries = [e for e in (_clean(e) for e in raw.split(",")) if e]
    emails = frozenset(e.lower() for e in entries if e.isascii())
    dropped = len(entries) - sum(1 for e in entries if e.isascii())
    global _dropped_logged_for
    if dropped and _dropped_logged_for != raw:
        _dropped_logged_for = raw
        log.warning("LF_ADMIN_EMAILS: %d entries with non-ASCII characters were ignored", dropped)
    if not (cid and secret and emails):
        return None
    redirect = _clean(os.environ.get("LF_GOOGLE_REDIRECT_URI", ""))
    return GoogleConfig(
        cid, secret, emails, redirect or "https://labs.shashkist.com/auth/google/callback"
    )


def password_login_allowed() -> bool:
    """Admin password login: on unless Google is configured, then only with the break-glass flag."""
    # The flag first: break-glass must not depend on the Google settings being readable.
    return os.environ.get("LF_ADMIN_PASSWORD_LOGIN") == "1" or google_config() is None


# ---------------------------------------------------------------- sessions


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@contextlib.contextmanager
def db_guard() -> Iterator[None]:
    """Turn database failures after startup into 503; log class and SQLSTATE only."""
    try:
        yield
    except (asyncpg.PostgresError, OSError, asyncpg.InterfaceError, TimeoutError) as exc:
        sqlstate = getattr(exc, "sqlstate", None)
        log.error("auth: database error %s sqlstate=%s", type(exc).__name__, sqlstate)
        raise HTTPException(503, "database unavailable") from None


def _pool(request: HTTPConnection) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(503, "database unavailable")
    return pool


async def _start_session(pool: Any, response: Response, kind: str, subject: str) -> None:
    token = secrets.token_urlsafe(32)
    await pool.execute(f"delete from {T}.auth_sessions where expires_at <= now()")
    await pool.execute(
        f"insert into {T}.auth_sessions (token_hash, kind, subject, expires_at) "
        "values ($1, $2, $3, now() + make_interval(secs => $4))",
        _token_hash(token),
        kind,
        subject,
        float(SESSION_SECONDS),
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


_STUDENT_SQL = f"""
select s.name, s.disabled, c.password_hash,
       coalesce(s.access_until, c.access_until) as until
from {T}.students s join {T}.cohorts c on c.id = s.cohort_id
where s.id_hash = $1
"""


@dataclass(frozen=True)
class Principal:
    kind: str  # "student" | "admin"
    name: str
    subject: str  # student id_hash or admin username


async def current_principal(request: HTTPConnection) -> Principal:
    """The logged-in student or admin; disabled/expired are re-checked on every call."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "not_authenticated")
    pool = _pool(request)
    with db_guard():
        row = await pool.fetchrow(
            f"select kind, subject from {T}.auth_sessions "
            "where token_hash = $1 and expires_at > now()",
            _token_hash(token),
        )
        if row is None:
            raise HTTPException(401, "not_authenticated")
        if row["kind"] == "admin":
            if row["subject"].startswith("google:"):  # no admins row; only while Google is on
                if google_config() is None:
                    raise HTTPException(401, "not_authenticated")
                return Principal("admin", "admin", row["subject"])
            if not password_login_allowed():  # a password session made earlier stops at once
                raise HTTPException(401, "not_authenticated")
            exists = await pool.fetchval(
                f"select 1 from {T}.admins where username = $1", row["subject"]
            )
            if not exists:
                raise HTTPException(401, "not_authenticated")
            return Principal("admin", row["subject"], row["subject"])
        s = await pool.fetchrow(_STUDENT_SQL, row["subject"])
    if s is None or s["disabled"] or s["until"] < _today():
        raise HTTPException(401, "not_authenticated")
    return Principal("student", s["name"], row["subject"])

async def current_student(p: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if p.kind != "student":
        raise HTTPException(403, "student_only")
    return p


async def current_admin(p: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if p.kind != "admin":
        raise HTTPException(403, "admin_only")
    return p


# ---------------------------------------------------------------- routes

router = APIRouter(prefix="/api/auth")


class StudentLogin(BaseModel):
    id: str = Field(max_length=100)
    password: str = Field(max_length=1000)


class AdminLogin(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=1000)


def _invalid() -> HTTPException:
    return HTTPException(401, "invalid_credentials")


@router.post("/login")
async def login(body: StudentLogin, request: Request, response: Response) -> dict[str, str]:
    pool = _pool(request)
    secret = os.environ.get("LF_ID_SECRET")
    if not secret:
        raise HTTPException(503, "login not configured")
    h = id_hash(body.id, secret) if valid_id(body.id) else ""
    keys = (
        (f"ip:{client_ip(request)}", _env_limit("LF_LOGIN_IP_LIMIT")),
        (f"id:{h}" if h else "", _env_limit("LF_LOGIN_ID_LIMIT")),
    )
    # Reserve the attempt before any await, so parallel bursts cannot slip past the limit.
    if not limiter.hit(*keys):
        raise HTTPException(429, "too_many_attempts")
    if not h:
        log.info("student login failed (invalid id)")
        raise _invalid()
    try:
        with db_guard():
            row = await pool.fetchrow(_STUDENT_SQL, h)
        ok = await verify_password(row["password_hash"] if row else None, body.password)
    except HTTPException:
        limiter.forgive(*keys)
        raise
    if row is None or not ok:
        log.info("student login failed (id %s)", h[:8])
        raise _invalid()
    limiter.forgive(*keys)
    if row["disabled"]:
        raise HTTPException(403, "account_disabled")
    if row["until"] < _today():
        raise HTTPException(403, "access_expired")
    with db_guard():
        await _start_session(pool, response, "student", h)
    log.info("student login (id %s)", h[:8])
    return {"kind": "student", "name": row["name"]}


@router.post("/admin/login")
async def admin_login(body: AdminLogin, request: Request, response: Response) -> dict[str, str]:
    if not password_login_allowed():
        raise HTTPException(403, "password_login_disabled")
    pool = _pool(request)
    name = body.username
    valid = bool(name) and len(name) <= MAX_USERNAME_LEN and name.isprintable() and ":" not in name
    keys = (
        (f"ip:{client_ip(request)}", _env_limit("LF_LOGIN_IP_LIMIT")),
        (f"admin:{name}" if valid else "", _env_limit("LF_LOGIN_ID_LIMIT")),
    )
    if not limiter.hit(*keys):
        raise HTTPException(429, "too_many_attempts")
    if not valid:
        log.info("admin login failed (invalid username)")
        raise _invalid()
    try:
        with db_guard():
            row = await pool.fetchrow(
                f"select password_hash from {T}.admins where username = $1", name
            )
        ok = await verify_password(row["password_hash"] if row else None, body.password)
    except HTTPException:
        limiter.forgive(*keys)
        raise
    if row is None or not ok:
        log.info("admin login failed")
        raise _invalid()
    limiter.forgive(*keys)
    with db_guard():
        await _start_session(pool, response, "admin", name)
    log.info("admin login")
    return {"kind": "admin", "name": name}


@router.get("/me")
async def me(p: Annotated[Principal, Depends(current_principal)]) -> dict[str, str]:
    return {"kind": p.kind, "name": p.name}


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE)
    if token:
        with db_guard():
            await _pool(request).execute(
                f"delete from {T}.auth_sessions where token_hash = $1", _token_hash(token)
            )
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return resp
