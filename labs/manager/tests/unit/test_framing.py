import asyncio

import pytest

from app import framing


@pytest.mark.parametrize(
    ("path", "framed"),
    [
        ("/s/abc/term", True),
        ("/s/abc/term/", True),
        ("/s/abc/term/token", True),
        ("/s/abc/terminal", False),
        ("/s/abc/other", False),
        ("/api/sessions", False),
    ],
)
def test_framed_paths(path, framed):
    assert framing.is_framed_path(path) is framed


def _names(headers, name):
    return [v.decode() for k, v in headers if k.lower() == name]


def test_upstream_framing_headers_are_replaced():
    merged = framing.merge_headers([
        (b"X-Frame-Options", b"ALLOWALL"),
        (b"Content-Security-Policy", b"frame-ancestors *"),
        (b"x-stub", b"1"),
    ])
    assert _names(merged, b"x-frame-options") == ["SAMEORIGIN"]
    assert _names(merged, b"content-security-policy") == ["frame-ancestors 'self'"]
    assert _names(merged, b"x-stub") == ["1"]


def test_other_csp_directives_are_kept():
    merged = framing.merge_headers(
        [(b"content-security-policy", b"default-src 'none'; Frame-Ancestors a.com")]
    )
    assert _names(merged, b"content-security-policy") == [
        "default-src 'none'",
        "frame-ancestors 'self'",
    ]


def test_middleware_guards_only_lab_paths():
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def run(path):
        sent = []

        async def send(m):
            sent.append(m)

        await framing.FrameGuard(inner)({"type": "http", "path": path}, None, send)
        return sent[0]["headers"]

    assert _names(asyncio.run(run("/s/x/term/")), b"x-frame-options") == ["SAMEORIGIN"]
    assert asyncio.run(run("/api/x")) == []


def test_frame_ancestors_after_a_comma_is_dropped():
    merged = framing.merge_headers(
        [(b"Content-Security-Policy", b"default-src 'self', frame-ancestors *")]
    )
    assert _names(merged, b"content-security-policy") == [
        "default-src 'self'",
        "frame-ancestors 'self'",
    ]
