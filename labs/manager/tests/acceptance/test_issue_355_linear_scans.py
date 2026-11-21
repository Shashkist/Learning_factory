"""Acceptance tests for issue #355: linear su and arithmetic scans, plus the two #361 regressions.

Observable behaviour: the warnings `load_labs` reports for a lab's setup.sh / check.sh (D115).

AC1: a long line of repeated `su -c ` or `su ` validates fast (was 36 s and 12 s at 20000 reps).
AC2: a long line of repeated `$((` validates fast (was 7.6 s at 20000 reps).
AC3 (D115): no line-length cap, so these long lines are not a lab error.
Guards (D114): `cat 2>&1>/dev/null` warns about stdin again; `>&` plus a backslash is not quiet.
Kept (D115): the stdin warning stays on a `su ... -c ... &` job.
"""

import time

import pytest
from test_issue_117_kill_timeouts import (
    background_warnings,
    lab_with_script,
    stdin_warnings,
)

NAMES = ["setup.sh", "check.sh"]
BASH = "#!/bin/bash\n"
SH = "#!/bin/sh\n"

# Quadratic code needs seconds to tens of seconds at 20000 repeats; linear code needs
# milliseconds. 2 s keeps a slow CI runner from flaking while still separating the two.
REPS = 20000
TIME_BOUND_S = 2.0


def _elapsed(tmp_path, head, body, name="setup.sh"):
    start = time.perf_counter()
    result = lab_with_script(tmp_path, f"{head}{body}\nexit 0\n", name)
    return time.perf_counter() - start, result


# ------------------------------------------------------------------ AC1


@pytest.mark.parametrize("head", [BASH, SH])
@pytest.mark.parametrize("unit", ["su -c ", "su "], ids=["su-dash-c", "su"])
def test_ac1_a_long_run_of_su_validates_fast(tmp_path, head, unit):
    # pins: `_SU_COMMAND` is one linear pass; no restart of a lazy scan at every `su`.
    took, _ = _elapsed(tmp_path, head, unit * REPS)
    assert took < TIME_BOUND_S, f"{REPS} x {unit!r} took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("unit", ["su -c ", "su "], ids=["su-dash-c", "su"])
def test_ac1_a_long_su_run_with_an_ampersand_validates_fast(tmp_path, unit):
    # pins: the su scan stays linear when the line also ends a job.
    took, _ = _elapsed(tmp_path, BASH, unit * REPS + "&")
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


# ------------------------------------------------------------------ AC2


@pytest.mark.parametrize("head", [BASH, SH])
def test_ac2_a_long_run_of_arithmetic_openers_validates_fast(tmp_path, head):
    # pins: `_ARITHMETIC` is linear when no `))` ever closes.
    took, _ = _elapsed(tmp_path, head, "$((" * REPS)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


def test_ac2_a_long_mixed_line_validates_fast(tmp_path):
    # pins: unclosed `$((` after real text and an `su` does not make either scan quadratic.
    body = "echo $((1+2)) su student -c " + "$(( su " * REPS
    took, _ = _elapsed(tmp_path, BASH, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


# -------------------------------------------------- guard 1 (D114): stdin warning back


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "line",
    [
        "su student -c 'cat 2>&1>/dev/null'",
        "su student -c 'cat >&2>/dev/null'",
        "su student -c 'cat 2>&1>/dev/null' </dev/null; su student -c 'cat >&2>/dev/null'",
    ],
)
def test_guard_a_redirect_right_after_a_dup_does_not_hide_a_stdin_reader(tmp_path, name, line):
    # pins: `2>&1` followed at once by `>file` still strips both redirects, so `cat` has no
    # words left and reads the script from stdin (main warned before #361).
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert stdin_warnings(result), f"missed stdin warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
def test_guard_a_stdin_redirect_after_the_dup_still_silences_the_warning(tmp_path, name):
    # pins: the neighbour of the guard: with `</dev/null` on the su command there is no warning.
    line = "su student -c 'cat 2>&1>/dev/null' </dev/null"
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert stdin_warnings(result) == [], result.warnings


# -------------------------------------------------- guard 2 (D114): backslash after >&


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", [r"sleep 600 >&\2 &", r"sleep 600 >&\- &"])
def test_guard_gt_amp_backslash_word_is_not_quiet(tmp_path, name, line):
    # pins: bash reads `>&\2` as a dup and `>&\-` as an error, never a file: the job still warns.
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert background_warnings(result), f"missed warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
def test_guard_gt_amp_plain_word_is_still_quiet(tmp_path, name):
    # pins: the backslash exclusion did not break `>&file`.
    result = lab_with_script(tmp_path, f"{BASH}sleep 600 >&/dev/null &\nexit 0\n", name)
    assert background_warnings(result) == [], result.warnings


# -------------------------------------------------- D115 keep: stdin warning on & jobs


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
def test_keep_the_stdin_warning_stays_on_a_su_job_that_ends_in_an_ampersand(tmp_path, name, head):
    # pins: D115 keeps the conservative stdin warning for `su ... -c 'cat' &`.
    result = lab_with_script(tmp_path, f"{head}su student -c 'cat' &\nexit 0\n", name)
    assert stdin_warnings(result), result.warnings


# ------------------------------------------------------------------ positive neighbours


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
def test_neighbour_a_plain_su_command_is_quiet(tmp_path, name, head):
    # pins: a linear `_SU_COMMAND` still finds only readers: `echo hi` is no stdin reader.
    result = lab_with_script(tmp_path, f"{head}su student -c 'echo hi'\nexit 0\n", name)
    assert stdin_warnings(result) == [] and background_warnings(result) == [], result.warnings


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
def test_neighbour_a_su_reader_is_still_found(tmp_path, name, head):
    # pins: a too-broad rewrite would stop matching `su ... -c '<reader>'`.
    result = lab_with_script(tmp_path, f"{head}su -l student -c 'read x'\nexit 0\n", name)
    assert stdin_warnings(result), result.warnings


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
def test_neighbour_arithmetic_is_quiet_and_hides_its_ampersand(tmp_path, name, head):
    # pins: `$((1+2))` and the bitwise `&` of `$((1&2))` are not a background job.
    body = "echo $((1+2))\necho $((1&2))\nx=$((3&1)) su student -c 'echo hi'\n"
    result = lab_with_script(tmp_path, f"{head}{body}exit 0\n", name)
    assert stdin_warnings(result) == [] and background_warnings(result) == [], result.warnings


@pytest.mark.parametrize("name", NAMES)
def test_neighbour_a_su_after_arithmetic_is_still_found(tmp_path, name):
    # pins: blanking `$(( ))` keeps positions: a reader after arithmetic still warns.
    line = "echo $((1+2)); su student -c 'cat'"
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert stdin_warnings(result), result.warnings
