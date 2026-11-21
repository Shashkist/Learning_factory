"""Only this site may frame a lab page: `frame-ancestors 'self'` plus `X-Frame-Options: SAMEORIGIN`
on every HTTP response under a lab route prefix (the terminal today, the IDE later).

Set in the manager, on the way out, so it covers the proxied page and every refusal. Upstream
`X-Frame-Options` and `frame-ancestors` directives are dropped, never appended."""
import re

FRAME_ANCESTORS = "frame-ancestors 'self'"
FRAME_HEADERS = {"Content-Security-Policy": FRAME_ANCESTORS, "X-Frame-Options": "SAMEORIGIN"}
# Add the next lab route here (for example `ide`).
LAB_FRAME_PATTERNS = (re.compile(r"^/s/[^/]+/term(/|$)"),)


def is_framed_path(path: str) -> bool:
    return any(p.match(path) for p in LAB_FRAME_PATTERNS)


def merge_headers(raw: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """Response headers with the framing policy enforced: no upstream X-Frame-Options, upstream
    CSP kept except its frame-ancestors directives (also one after a comma, which starts a new
    policy), then ours."""
    out: list[tuple[bytes, bytes]] = []
    for k, v in raw:
        name = k.lower()
        if name == b"x-frame-options":
            continue
        if name == b"content-security-policy":
            policies = []
            for policy in v.decode("latin-1").split(","):
                kept = [
                    d.strip()
                    for d in policy.split(";")
                    if d.split() and d.split()[0].lower() != "frame-ancestors"
                ]
                if kept:
                    policies.append("; ".join(kept))
            if not policies:
                continue
            v = ", ".join(policies).encode("latin-1")
        out.append((k, v))
    out.append((b"content-security-policy", FRAME_ANCESTORS.encode()))
    out.append((b"x-frame-options", b"SAMEORIGIN"))
    return out


class FrameGuard:
    """ASGI middleware: applies `merge_headers` to HTTP responses of the lab prefixes."""

    def __init__(self, inner):  # type: ignore[no-untyped-def]
        self.inner = inner

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or not is_framed_path(scope.get("path", "")):
            await self.inner(scope, receive, send)
            return

        async def guarded(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                message = {**message, "headers": merge_headers(list(message.get("headers", [])))}
            await send(message)

        started = False

        async def tracking(message):  # type: ignore[no-untyped-def]
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await guarded(message)  # type: ignore[no-untyped-call]

        try:
            await self.inner(scope, receive, tracking)
        except Exception:
            if not started:
                # ServerErrorMiddleware is outside us, so frame its 500 ourselves, then re-raise.
                await guarded(  # type: ignore[no-untyped-call]
                    {"type": "http.response.start", "status": 500, "headers": [
                        (b"content-type", b"text/plain; charset=utf-8")]}
                )
                await guarded({"type": "http.response.body", "body": b"Internal Server Error"})  # type: ignore[no-untyped-call]
            raise
