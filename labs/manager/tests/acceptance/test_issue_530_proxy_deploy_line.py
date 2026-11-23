"""Exam for #530: the trusted-proxy line a deploy prints names the resolved addresses.

`labs/deploy/deploy.sh` prints the last "trusted proxy" line the manager logged. When lookup
thread starts fail before the first lookup completes, the settled-run line ("start failures ended
(N similar warnings suppressed)") must not be that last line. Either fix is accepted: the manager
logs the settled count before the info line, or deploy.sh skips such lines. The selection is
derived from the script itself (its `grep` stages between `docker logs` and `tail -n1`), so the
exam follows whichever fix is made. Seams are those of the #521 exam.
"""

import logging
import re
import socket
from pathlib import Path

import pytest
from test_issue_93_trusted_proxy_name import NAME, A, Resolver
from test_issue_521_proxy_warn_run import (
    _counts,
    _failing_run,
    _one_failed_start,
    _successful_lookup,
    _warnings,
    clock,  # noqa: F401  (fixture)
)

from app import auth

DEPLOY_SH = Path(__file__).resolve().parents[4] / "labs" / "deploy" / "deploy.sh"
HOURS = 3 * 3600


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("LF_TRUSTED_PROXY", NAME)
    monkeypatch.setenv("LF_TRUSTED_PROXY_TTL_S", "60")
    reset = getattr(auth, "reset_trusted_proxy_cache", lambda: None)
    reset()
    yield
    reset()


def _selection_stages() -> list[tuple[bool, str]]:
    """(invert, pattern) of every grep between `docker logs` and the `tail -n1` in deploy.sh."""
    text = DEPLOY_SH.read_text(encoding="utf-8")
    start = text.index('"trusted proxy"')  # the grep that picks the proxy lines
    start = text.rindex("logs", 0, start)
    assert "lf-manager" in text[start : start + 120], "deploy.sh no longer reads the manager logs"
    chunk = text[start:]
    chunk = chunk[: chunk.index("tail -n1")]
    stages = re.findall(
        r"grep\s+((?:-[a-zA-Z]+\s+)*)(?:-e\s+)?(?:\"([^\"]*)\"|'([^']*)'|(\.))", chunk
    )
    out = []
    for flags, dq, sq, dot in stages:
        out.append(("v" in flags, dq or sq or dot))
    assert any(not inv and "trusted proxy" in pat for inv, pat in out), out
    return out


def deploy_line(lines: list[str]) -> str | None:
    """The line deploy.sh would print: its grep stages applied in order, then the last one."""
    picked = list(lines)
    for invert, pattern in _selection_stages():
        rx = re.compile(pattern)
        picked = [ln for ln in picked if bool(rx.search(ln)) != invert]
    return picked[-1] if picked else None


def _manager_lines(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.auth" and r.levelno >= logging.INFO
    ]


def test_the_deploy_selection_is_derived_from_the_script():
    stages = _selection_stages()
    assert deploy_line(["x", f"trusted proxy {NAME} -> {A}"]) == f"trusted proxy {NAME} -> {A}"
    assert stages


def test_after_start_failures_and_a_completed_lookup_the_deploy_line_names_the_addresses(
    monkeypatch, caplog, clock  # noqa: F811
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    caplog.set_level(logging.INFO, logger="app.auth")
    attempts = _failing_run(monkeypatch, clock, 30)
    assert attempts >= 25, f"the run of failures did not happen: {attempts}"
    assert _warnings(caplog), "the run logged no warning at all"

    _successful_lookup(monkeypatch, clock)

    lines = _manager_lines(caplog)
    picked = deploy_line(lines)
    assert picked is not None, f"deploy.sh would print nothing: {lines}"
    assert A in picked, f"the deploy line does not name the resolved address: {picked!r} of {lines}"


def test_a_run_of_failures_then_a_lookup_in_a_single_window_still_names_the_addresses(
    monkeypatch, caplog, clock  # noqa: F811
):
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    caplog.set_level(logging.INFO, logger="app.auth")
    attempts = _failing_run(monkeypatch, clock, 5)
    assert attempts >= 5
    _successful_lookup(monkeypatch, clock)

    picked = deploy_line(_manager_lines(caplog))
    assert picked is not None and A in picked, f"deploy line: {picked!r}"


def test_a_completed_lookup_that_failed_still_ends_the_run(monkeypatch, caplog, clock):  # noqa: F811
    monkeypatch.setattr(socket, "getaddrinfo", Resolver(OSError("no such host")))
    caplog.set_level(logging.INFO, logger="app.auth")
    attempts = _failing_run(monkeypatch, clock, 30)
    assert attempts >= 25, f"the run of failures did not happen: {attempts}"

    _successful_lookup(monkeypatch, clock)  # the lookup completes, getaddrinfo raises
    assert any("lookup failed" in m for m in _manager_lines(caplog)), "the lookup did not fail"
    picked = deploy_line(_manager_lines(caplog))
    assert picked is not None and (
        "(none)" in picked or "lookup failed" in picked
    ), f"the deploy line should report the failed lookup: {picked!r}"

    clock[0] += HOURS
    caplog.clear()
    _one_failed_start(monkeypatch)
    later = _warnings(caplog)
    assert len(later) == 1, f"one failed start, one warning expected: {later}"
    assert _counts(later[0]) == [], f"count from the earlier run leaked: {later[0]}"



def test_unchanged_addresses_after_a_run_of_start_failures_the_deploy_line_still_names_them(
    monkeypatch, caplog, clock  # noqa: F811
):
    # Production: the warm-up lookup completes at startup and logs the info line first.
    monkeypatch.setattr(socket, "getaddrinfo", Resolver((A,)))
    caplog.set_level(logging.INFO, logger="app.auth")
    _successful_lookup(monkeypatch, clock)
    assert any(f"-> {A}" in m for m in _manager_lines(caplog)), "the warm-up logged no info line"

    clock[0] += HOURS  # past the TTL: the next request wants a fresh lookup
    attempts = _failing_run(monkeypatch, clock, 30)
    assert attempts >= 25, f"the run of failures did not happen: {attempts}"
    assert _warnings(caplog), "the run logged no warning at all"

    _successful_lookup(monkeypatch, clock)  # same addresses as the warm-up

    lines = _manager_lines(caplog)
    picked = deploy_line(lines)
    assert picked is not None, f"deploy.sh would print nothing: {lines}"
    assert "-> " in picked and A in picked, (
        f"the deploy line does not name the resolved address: {picked!r} of {lines}"
    )
