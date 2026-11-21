"""Acceptance tests for issue #428 (review of #425; origin check of #373/#410/#416, D119).

The startup check normalises the host before the IPv4 checks. Exactly one trailing dot is
dropped (that is what the WHATWG URL parser a browser uses does); two or more are not.

Measured with Node `new URL(u).host` (WHATWG URL parser), the form a browser sends:

    http://1.2.3.4.            -> 1.2.3.4            (one dot dropped, it IS an IPv4 address)
    http://127.1.              -> 127.0.0.1          (one dot dropped, then IPv4 rewriting)
    http://0x1.2.3.4.          -> 1.2.3.4
    http://1.2.3.4..           -> 1.2.3.4..          (NOT an IPv4 address: empty last label)
    http://1.2.3.4...          -> 1.2.3.4...
    http://0x1.2.3.4..         -> 0x1.2.3.4..
    http://127.1..             -> 127.1..
    http://labs.shashkist.com.. -> labs.shashkist.com..
    http://a..b                -> a..b

So a browser does NOT refuse a host with an empty label (the issue text says it does; the
measurement wins). It sends such a host unchanged, hence the chosen behaviour for two or more
trailing dots: accepted silently, no startup warning, which is what the forbidden-host rule does
for empty labels. A single trailing dot on an IPv4 literal is rewritten by the browser, so that
configured value must keep warning (IPv4 host not in the dotted form a browser sends).

The fake manager is the #69/#373 exams'; no Docker.
"""
# ruff: noqa: F811  (imported fixtures are used by name)
import logging

import pytest
from test_issue_69_ws_origin_check import (  # noqa: F401, F811
    _origin_warnings,
    listener,
    make_client,
)


def _warnings_for(make_client, caplog, value):
    with caplog.at_level(logging.INFO, logger="lab-manager"):
        make_client(value)
        return _origin_warnings(caplog)


# ---------------------------------------------------------------- AC1: exactly one dot removed


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("http://1.2.3.4.", id="canonical-ipv4-one-dot"),
        pytest.param("http://127.1.", id="short-ipv4-one-dot"),
        pytest.param("http://0x1.2.3.4.", id="hex-octet-one-dot"),
    ],
)
def test_exactly_one_trailing_dot_is_removed_before_the_ipv4_checks(make_client, caplog, value):
    warnings = _warnings_for(make_client, caplog, value)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "not set" not in warnings[0]
    assert "IPv4" in warnings[0], warnings[0]


def test_one_trailing_dot_on_a_hostname_stays_quiet(make_client, caplog):
    assert _warnings_for(make_client, caplog, "https://labs.shashkist.com.") == []


# ---------------------------------------------------------------- AC2: two or more dots


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("http://1.2.3.4..", id="ipv4-two-dots"),
        pytest.param("http://1.2.3.4...", id="ipv4-three-dots"),
        pytest.param("http://0x1.2.3.4..", id="hex-octet-two-dots"),
        pytest.param("http://127.1..", id="short-ipv4-two-dots"),
        pytest.param("https://labs.shashkist.com..", id="hostname-two-dots"),
        pytest.param("https://labs.shashkist.com...", id="hostname-three-dots"),
        pytest.param("http://a..b", id="empty-inner-label"),
    ],
)
def test_two_or_more_trailing_dots_do_not_warn_as_a_rewritten_ipv4_form(
    make_client, caplog, value
):
    warnings = _warnings_for(make_client, caplog, value)
    assert not any("IPv4" in w for w in warnings), warnings
    # the browser sends these hosts unchanged: accepted silently, like any empty label
    assert warnings == [], [r.getMessage() for r in caplog.records]
