"""Acceptance tests for issue #434 (review of #428; origin check, D119, D125).

A configured LF_PUBLIC_ORIGIN whose host has an empty label (other than one trailing dot) gets a
startup warning: the WHATWG parser keeps such a host, but name resolution rejects empty labels, so
no browser ever loads a page from it and every request would be refused by the origin check.

Measured with Node `new URL(u).host` (the form a browser sends), all kept unchanged:

    http://a..b                 -> a..b
    https://labs.shashkist.com.. -> labs.shashkist.com..
    http://1.2.3.4..            -> 1.2.3.4..
    http://..a                  -> ..a
    http://.a.b                 -> .a.b        (leading dot kept, so it is an empty label too)
    http://a.b..c               -> a.b..c

The warning is asserted by the stable fragment "empty label", not the full sentence.
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


# ---------------------------------------------------------------- AC1: empty label warns


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("http://a..b", id="empty-inner-label"),
        pytest.param("http://a.b..c", id="empty-inner-label-deeper"),
        pytest.param("https://labs.shashkist.com..", id="hostname-two-trailing-dots"),
        pytest.param("https://labs.shashkist.com...", id="hostname-three-trailing-dots"),
        pytest.param("http://1.2.3.4..", id="ipv4-two-trailing-dots"),
        pytest.param("http://1.2.3.4...", id="ipv4-three-trailing-dots"),
        pytest.param("http://0x1.2.3.4..", id="hex-octet-two-trailing-dots"),
        pytest.param("http://127.1..", id="short-ipv4-two-trailing-dots"),
        pytest.param("http://..a", id="leading-empty-labels"),
        pytest.param("http://.a.b", id="leading-dot"),
    ],
)
def test_host_with_an_empty_label_warns_at_startup(make_client, caplog, value):
    warnings = _warnings_for(make_client, caplog, value)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "not set" not in warnings[0]
    assert "empty label" in warnings[0], warnings[0]


# ---------------------------------------------------------------- AC2: controls


def test_one_trailing_dot_on_an_ipv4_literal_keeps_its_ipv4_warning(make_client, caplog):
    warnings = _warnings_for(make_client, caplog, "http://1.2.3.4.")
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "IPv4" in warnings[0], warnings[0]
    assert "empty label" not in warnings[0], warnings[0]


def test_one_trailing_dot_on_a_hostname_stays_quiet(make_client, caplog):
    assert _warnings_for(make_client, caplog, "https://labs.shashkist.com.") == []


def test_a_normal_hostname_stays_quiet(make_client, caplog):
    assert _warnings_for(make_client, caplog, "https://labs.shashkist.com") == []
