"""Acceptance tests for issue #343: review leftovers of the #326 validator work (D56, D107).

Observable behaviour: the warnings `load_labs` reports for a lab's setup.sh / check.sh.

AC1: the redirect regexes stay linear on long digit runs (`"1"*50000 + " &"` took ~73 s).
AC2: `su ... -c` reads stdin is decided with the same job split as the background check, so
     under sh the `&` of `&>` ends the su job (dash: `cmd &>f` is `cmd &` then `>f`).
AC3: under bash, `cmd >&word` redirects stdout and stderr, so `cmd >&/dev/null &` is quiet.
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

# 20000 digits took a few seconds per regex before the fix (quadratic; 50000 took ~73 s).
# Linear code needs milliseconds, so the 1 s bound separates the two without a minute-long run.
LONG = "1" * 20000
TIME_BOUND_S = 1.0


def _elapsed(tmp_path, head, body, name):
    start = time.perf_counter()
    lab_with_script(tmp_path, f"{head}{body}\nexit 0\n", name)
    return time.perf_counter() - start


# ------------------------------------------------------------------ AC1


@pytest.mark.parametrize("head", [BASH, SH])
@pytest.mark.parametrize("body", [LONG + " &", "sleep " + LONG + " &"])
def test_ac1_a_long_digit_run_with_an_ampersand_validates_fast(tmp_path, head, body):
    name = "setup.sh"
    # pins: the background-job redirect scan (`_FD_REDIRECT`) and the stdin-reader scan
    # (`_REDIRECTS`) are linear in the line length, for both interpreters.
    took = _elapsed(tmp_path, head, body, name)
    assert took < TIME_BOUND_S, f"{len(body)} chars took {took:.1f}s (bound {TIME_BOUND_S}s)"


def test_ac1_a_long_digit_run_without_an_ampersand_validates_fast(tmp_path):
    name = "setup.sh"
    # pins: `_REDIRECTS` alone (no job split involved) is linear on a bare digit run.
    took = _elapsed(tmp_path, BASH, LONG, name)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("name", NAMES)
def test_ac1_real_redirects_after_a_digit_run_still_count(tmp_path, name):
    # pins: anchoring the digits did not change verdicts: `2>&1` and `2>file` still read right.
    quiet = lab_with_script(tmp_path, f"{BASH}sleep 600 >/dev/null 2>&1 &\nexit 0\n", name)
    assert background_warnings(quiet) == []
    loud = lab_with_script(tmp_path / "second", f"{BASH}sleep 600 >/dev/null &\nexit 0\n", name)
    assert background_warnings(loud)


# ------------------------------------------------------------------ AC2

# `su -c 'cat'` followed by `&>` and then `<in`: under sh the `&` ends the su job, so the
# `<in` belongs to a later empty command and su still reads the script from stdin (warn).
# Under bash `&>/dev/null` is a redirect of su itself, `<in` is su's input (no warning).
SU_AMP_REDIRECT_LINES = [
    "su student -c 'cat' &>/dev/null </tmp/in",
    "su student -c 'cat' &> /dev/null </tmp/in",
    "su student -c 'read x' &>/dev/null </tmp/in",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", SU_AMP_REDIRECT_LINES)
def test_ac2_under_sh_the_amp_of_amp_gt_ends_the_su_job(tmp_path, name, line):
    # pins: `_su_reads_stdin` splits at the `&` of `&>` under sh (via `_SH_AMP`).
    result = lab_with_script(tmp_path, f"{SH}{line}\nexit 0\n", name)
    assert stdin_warnings(result), f"no stdin warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", SU_AMP_REDIRECT_LINES)
def test_ac2_under_bash_amp_gt_stays_part_of_the_su_command(tmp_path, name, line):
    # pins: under bash the same lines have no stdin warning (`_BASH_AMP` does not split at `&>`).
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert stdin_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
def test_ac2_a_lone_amp_still_ends_the_su_job_in_both_shells(tmp_path, name, head):
    # pins: the plain `&` split of the su job is unchanged by the shared split.
    line = "su student -c 'cat' & echo hi </tmp/in"
    result = lab_with_script(tmp_path, f"{head}{line}\nexit 0\n", name)
    assert stdin_warnings(result), result.warnings


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
def test_ac2_a_dup_amp_does_not_end_the_su_job(tmp_path, name, head):
    # pins: the `&` of `2>&1` is not a job end, so `</dev/null` after it is su's own input.
    line = "su student -c 'cat' 2>&1 </dev/null"
    result = lab_with_script(tmp_path, f"{head}{line}\nexit 0\n", name)
    assert stdin_warnings(result) == [], result.warnings


# ------------------------------------------------------------------ AC3

BASH_WORD_LINES = [
    "sleep 600 >&/dev/null &",
    "sleep 600 >& /dev/null &",
    "nohup sleep 600 >&/tmp/app.log &",
    "echo x; sleep 600 >&/dev/null &",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", BASH_WORD_LINES)
def test_ac3_under_bash_gt_amp_word_counts_as_quiet(tmp_path, name, line):
    # pins: bash `>&word` sends stdout and stderr to the file, so the job is quiet.
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert background_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
def test_ac3_gt_amp_digit_is_still_a_dup_not_a_file(tmp_path, name):
    # pins: `>&2` is a descriptor copy (stdout to stderr), so it is not a quiet job.
    result = lab_with_script(tmp_path, f"{BASH}sleep 600 >&2 &\nexit 0\n", name)
    assert background_warnings(result)


@pytest.mark.parametrize("name", NAMES)
def test_ac3_stdout_only_redirect_before_dup_is_unchanged(tmp_path, name):
    # pins: ordering still matters: `2>&1 >/dev/null` leaves stderr on the exec's output.
    result = lab_with_script(tmp_path, f"{BASH}sleep 600 2>&1 >/dev/null &\nexit 0\n", name)
    assert background_warnings(result)


# ------------------------------------------------------------------ AC3 guards (review round 1)

# Under bash `>&word` sends both streams to a file only when the word is a plain filename-like
# word. `-` closes stdout (`>&-`), `$fd`/`"$LOGFD"` may expand to a descriptor number, so in all
# of these stderr stays open on the exec's output: the backgrounded job is not quiet (main warns).
BASH_NOT_A_FILE_WORD_LINES = [
    "sleep 600 >&- &",
    "sleep 600 1>&- &",
    "fd=3; sleep 600 >&$fd &",
    'sleep 600 >&"$LOGFD" &',
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", BASH_NOT_A_FILE_WORD_LINES)
def test_ac3_gt_amp_dash_or_expansion_is_not_a_file_and_still_warns(tmp_path, name, line):
    # pins: the `>&word` quiet rule excludes words starting with `-`, `$`, a backquote or a quote.
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert background_warnings(result), f"missed warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
def test_ac3_gt_amp_space_digit_is_a_dup_so_stdout_stays_loud(tmp_path, name):
    # `>& 2` (space before the digit) is the same descriptor copy as `>&2`: stdout goes to the
    # current stderr. The later `2>/dev/null` then silences only stderr; stdout still points at
    # the exec's output, so the job is not quiet (the check wants BOTH outputs in files, D107).
    # Reading `>& 2` as a file named `2` would wrongly call it quiet.
    line = "sleep 600 >& 2 2>/dev/null &"
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert background_warnings(result), f"missed warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
def test_ac3_gt_amp_space_digit_after_file_redirect_is_quiet(tmp_path, name):
    # `>/dev/null 2>& 1`: stderr copies stdout (already a file), so both are in files.
    line = "sleep 600 >/dev/null 2>& 1 &"
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert background_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"


# ------------------------------------------------------------------ AC2 guard (review round 1)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
def test_ac2_an_amp_inside_arithmetic_does_not_end_the_su_job(tmp_path, name, head):
    # pins: the su job split ignores the bitwise `&` of `$(( ))`, like the background check does
    # (D107), so `</dev/null` after it is su's own input and there is no stdin warning.
    line = "su student -c 'cat' $((1&2)) </dev/null"
    result = lab_with_script(tmp_path, f"{head}{line}\nexit 0\n", name)
    assert stdin_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"
