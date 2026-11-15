"""Admin sign-in with Google (D39): OpenID Connect authorization-code flow with PKCE.

Flow state lives in memory (one uvicorn process). Logs name the check that failed, never the
email, the code, the state, a token or the subject.
"""
import base64
import hashlib
import hmac
import logging
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import auth

log = logging.getLogger("lab-manager")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
COOKIE = "lf_oauth"
COOKIE_PATH = "/auth/google"
FLOW_SECONDS = 600
MAX_FLOWS = 1_000
JWKS_TTL = 3600
HTTP_TIMEOUT = 10.0

router = APIRouter()


class _StripQuery(logging.Filter):
    """uvicorn's access log prints the request path with its query string: drop the query so the
    OAuth code and state never reach the container logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].partition("?")[0], *args[3:])
        return True


def install_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _StripQuery) for f in access.filters):
        access.addFilter(_StripQuery())


install_log_filter()  # uvicorn configures its loggers before importing the app; filters survive


@dataclass
class _Flow:
    nonce: str
    verifier: str
    binding_hash: str
    expires: float


_flows: OrderedDict[str, _Flow] = OrderedDict()
_jwks: dict[str, Any] = {"keys": {}, "at": 0.0}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _page(status: int, text: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><title>{text}</title><p>{text}</p>", status_code=status)


def _deny(check: str) -> HTMLResponse:
    log.info("google sign-in refused: %s", check)
    return _page(403, "Sign-in refused")


@router.get("/api/auth/admin/methods")
def methods() -> dict[str, bool]:
    return {"google": auth.google_config() is not None, "password": auth.password_login_allowed()}


@router.get("/auth/google/start")
async def start(request: Request) -> Any:
    cfg = auth.google_config()
    if cfg is None:
        return _page(503, "Google sign-in is not configured")
    if not auth.limiter.hit(
        (f"gstart:{auth.client_ip(request)}", auth._env_limit("LF_GOOGLE_START_LIMIT", 30))
    ):
        raise HTTPException(429, "too_many_attempts")
    now = time.monotonic()
    for key in [k for k, f in _flows.items() if f.expires <= now]:
        _flows.pop(key, None)
    while len(_flows) >= MAX_FLOWS:
        _flows.popitem(last=False)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, binding = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    _flows[state] = _Flow(nonce, verifier, _sha(binding), now + FLOW_SECONDS)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    query = urlencode(
        {
            "client_id": cfg.client_id,
            "redirect_uri": cfg.redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge.rstrip(b"=").decode(),
            "code_challenge_method": "S256",
        }
    )
    resp = RedirectResponse(f"{AUTH_URL}?{query}", status_code=303)
    resp.set_cookie(
        COOKIE, binding, max_age=FLOW_SECONDS, httponly=True, secure=True, samesite="lax",
        path=COOKIE_PATH,
    )  # fmt: skip
    return resp


async def _fetch_keys(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.get(JWKS_URL)
    resp.raise_for_status()
    keys = {k["kid"]: k for k in resp.json()["keys"] if k.get("kid")}
    _jwks.update(keys=keys, at=time.monotonic())
    return keys


async def _verify(client: httpx.AsyncClient, token: str, client_id: str) -> dict[str, Any]:
    """Signature (RS256 against Google's JWKS), exp, aud and iss; PyJWT errors propagate."""
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str):
        raise jwt.InvalidTokenError("alg")
    fresh = time.monotonic() - _jwks["at"] < JWKS_TTL
    keys = _jwks["keys"] if fresh else await _fetch_keys(client)
    for attempt in (0, 1):
        if kid in keys:
            try:
                claims: dict[str, Any] = jwt.decode(
                    token,
                    jwt.PyJWK(keys[kid]).key,
                    algorithms=["RS256"],
                    audience=client_id,
                    options={"require": ["exp", "iss", "aud"]},
                )
                break
            except jwt.InvalidSignatureError:
                if attempt:
                    raise
        if attempt:
            raise jwt.InvalidTokenError("kid")
        keys = await _fetch_keys(client)  # unknown kid or rotated key: refetch once
    if claims["iss"] not in ISSUERS:
        raise jwt.InvalidIssuerError("iss")
    return claims


@router.get("/auth/google/callback")
async def callback(request: Request) -> Any:
    cfg = auth.google_config()
    if cfg is None:
        return _page(503, "Google sign-in is not configured")
    keys = ((f"gcb:{auth.client_ip(request)}", auth._env_limit("LF_LOGIN_IP_LIMIT")),)
    if not auth.limiter.hit(*keys):
        raise HTTPException(429, "too_many_attempts")
    q = request.query_params
    flow = _flows.pop(q.get("state", ""), None)
    cookie = request.cookies.get(COOKIE, "")
    if flow is None or flow.expires <= time.monotonic():
        return _deny("unknown or expired flow")
    if not cookie or not hmac.compare_digest(_sha(cookie), flow.binding_hash):
        return _deny("cookie does not match the flow")
    code = q.get("code")
    if q.get("error") or not code:
        return _deny("google returned an error or no code")
    form = {
        "grant_type": "authorization_code", "code": code, "client_id": cfg.client_id,
        "client_secret": cfg.client_secret, "redirect_uri": cfg.redirect_uri,
        "code_verifier": flow.verifier,
    }  # fmt: skip
    transport = getattr(request.app.state, "google_transport", None)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(TOKEN_URL, data=form)
            if resp.status_code != 200:
                return _deny("token exchange refused")
            claims = await _verify(client, resp.json()["id_token"], cfg.client_id)
    except (httpx.HTTPError, jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        return _deny(f"token exchange or id token invalid ({type(exc).__name__})")
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not hmac.compare_digest(nonce, flow.nonce):
        return _deny("nonce mismatch")
    if claims.get("email_verified") is not True:
        return _deny("email not verified")
    email, sub = claims.get("email"), claims.get("sub")
    if (
        not isinstance(email, str)
        or not email.isascii()
        or email.strip().lower() not in cfg.emails
    ):
        return _deny("address not on the allow-list")
    if not isinstance(sub, str) or not sub:
        return _deny("no subject")
    pool = auth._pool(request)
    out = RedirectResponse("/admin", status_code=303)
    with auth.db_guard():
        await auth._start_session(pool, out, "admin", f"google:{sub}")
    out.delete_cookie(COOKIE, path=COOKIE_PATH, secure=True, httponly=True, samesite="lax")
    auth.limiter.forgive(*keys)
    log.info("google admin sign-in")
    return out
