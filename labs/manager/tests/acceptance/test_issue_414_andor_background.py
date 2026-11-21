"""Acceptance tests for issue #414: and-or lists and continued lines in a backgrounded job (D56).

Observable behaviour: the warnings `load_labs` reports for a lab's setup.sh / check.sh.

AC1: `&` backgrounds a whole and-or list, and a pipeline continued on the next line (trailing
     `|` or a backslash) is one job: every element must carry its own redirects or it warns.
AC2: a foreground `a && b` gives no background warning.
AC3: the scans stay linear (#343, #355).
Plus quiet controls for the false positives earlier reviews kept finding.
"""

import time

import pytest
from test_issue_117_kill_timeouts import background_warnings, lab_with_script

NAMES = ["setup.sh", "check.sh"]
BASH = "#!/bin/bash\n"
SH = "#!/bin/sh\n"
SHEBANGS = [SH, BASH]

REPS = 20000
TIME_BOUND_S = 2.0


def _run(tmp_path, head, body, name):
    return lab_with_script(tmp_path, f"{head}{body}\nexit 0\n", name)


def _warns_bg(tmp_path, head, body, name):
    result = _run(tmp_path, head, body, name)
    found = background_warnings(result)
    assert found, f"no background warning for {head!r} {body!r}: {result.warnings}"


def _quiet_bg(tmp_path, head, body, name):
    result = _run(tmp_path, head, body, name)
    assert result.warnings.get("test/good", []) == [], (
        f"false positive {head!r} {body!r}: {result.warnings}"
    )


# ------------------------------------------------------------------ AC1: warn


WARN_BODIES = [
    "sleep 600 | cat >/dev/null 2>&1 && true >/dev/null 2>&1 &",
    "sleep 600 || cat >/dev/null 2>&1 &",
    "sleep 600 |\ncat >/dev/null 2>&1 &",
    "sleep 600 | \\\ncat >/dev/null 2>&1 &",
]
WARN_IDS = ["and-list", "or-list", "trailing-pipe", "trailing-backslash"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize("body", WARN_BODIES, ids=WARN_IDS)
def test_ac1_the_issue_shapes_warn(tmp_path, name, head, body):
    _warns_bg(tmp_path, head, body, name)


# ------------------------------------------------------------------ AC1: quiet


QUIET_BODIES = [
    "sleep 600 >/dev/null 2>&1 | cat >/dev/null 2>&1 && true >/dev/null 2>&1 &",
    "sleep 600 2>/dev/null | cat >/dev/null 2>&1 && true >/dev/null 2>&1 &",
    "sleep 600 >/dev/null 2>&1 || cat >/dev/null 2>&1 &",
    "sleep 600 2>/dev/null >/dev/null || cat >/dev/null 2>&1 &",
    "sleep 600 2>/dev/null |\ncat >/dev/null 2>&1 &",
    "sleep 600 2>/dev/null | \\\ncat >/dev/null 2>&1 &",
    "sleep 600 >/dev/null 2>&1 &&\ntrue >/dev/null 2>&1 &",
    "sleep 600 >/dev/null 2>&1 && \\\ntrue >/dev/null 2>&1 &",
]
QUIET_IDS = [
    "and-list-redirected",
    "and-list-stderr-only-on-pipe-head",
    "or-list-redirected",
    "or-list-two-redirects",
    "trailing-pipe-redirected",
    "trailing-backslash-redirected",
    "trailing-and-and-redirected",
    "backslash-and-and-redirected",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize("body", QUIET_BODIES, ids=QUIET_IDS)
def test_ac1_the_same_shapes_with_redirects_on_every_element_stay_quiet(
    tmp_path, name, head, body
):
    _quiet_bg(tmp_path, head, body, name)


# ------------------------------------------------------------------ AC2


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize(
    "body",
    ["true && sleep 1", "true || sleep 1", "sleep 1 | cat && true"],
    ids=["and", "or", "pipe-and"],
)
def test_ac2_a_foreground_and_or_list_gives_no_background_warning(tmp_path, name, head, body):
    result = _run(tmp_path, head, body, name)
    assert background_warnings(result) == [], result.warnings


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac2_only_the_backgrounded_last_command_counts_and_it_is_redirected(
    tmp_path, name, head
):
    _quiet_bg(tmp_path, head, "true && sleep 1; sleep 600 >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac2_an_unredirected_job_after_a_semicolon_still_warns(tmp_path, name, head):
    _warns_bg(tmp_path, head, "true && true; sleep 600 &", name)


# ------------------------------------------------------------------ AC3: linear


def _elapsed(tmp_path, head, body, name="setup.sh"):
    start = time.perf_counter()
    result = _run(tmp_path, head, body, name)
    return time.perf_counter() - start, result


@pytest.mark.parametrize("head", SHEBANGS)
def test_ac3_a_long_and_or_chain_validates_fast(tmp_path, head):
    body = " && ".join(["true >/dev/null 2>&1"] * REPS) + " &"
    took, _ = _elapsed(tmp_path, head, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("head", SHEBANGS)
def test_ac3_a_long_chain_of_mixed_operators_validates_fast(tmp_path, head):
    body = " || ".join(["true | cat"] * REPS) + " &"
    took, _ = _elapsed(tmp_path, head, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("head", SHEBANGS)
def test_ac3_many_lines_continued_by_a_trailing_pipe_validate_fast(tmp_path, head):
    body = "sleep 600 2>/dev/null |\n" * REPS + "cat >/dev/null 2>&1 &"
    took, _ = _elapsed(tmp_path, head, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("head", SHEBANGS)
def test_ac3_many_lines_continued_by_a_backslash_validate_fast(tmp_path, head):
    body = "echo a \\\n" * REPS + "echo b &"
    took, _ = _elapsed(tmp_path, head, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("head", SHEBANGS)
def test_ac3_many_separate_continued_jobs_validate_fast(tmp_path, head):
    body = "sleep 600 2>/dev/null |\ncat >/dev/null 2>&1 &\n" * (REPS // 4)
    took, _ = _elapsed(tmp_path, head, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


# ------------------------------------------------------------------ quiet controls


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize(
    "body",
    [
        "sleep 600 >/dev/null 2>&1 & echo 'a && b'",
        'sleep 600 >/dev/null 2>&1 & echo "a || b"',
        "echo ok # sleep 1 && sleep 2 &",
        "echo ok # a || b |",
        "sleep 600 >/dev/null 2>&1 & # && cat",
    ],
    ids=["single-quoted", "double-quoted", "comment-and", "comment-or-pipe", "trailing-comment"],
)
def test_control_and_or_inside_a_quote_or_comment_is_not_a_list(tmp_path, name, head, body):
    _quiet_bg(tmp_path, head, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "body",
    [
        "sleep 600 &>/dev/null",
        "sleep 600 >&/dev/null &",
        "sleep 600 >/dev/null 2>&1 || sleep 1 &>/dev/null &",
    ],
    ids=["amp-gt", "gt-amp-bg", "amp-gt-in-list"],
)
def test_control_amp_redirections_under_bash_cover_both_outputs(tmp_path, name, body):
    _quiet_bg(tmp_path, BASH, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", ["[[ -f /a && -f /b ]]", "[[ -f /a || -f /b ]] && true"])
def test_control_double_bracket_conditions_under_bash_are_not_jobs(tmp_path, name, body):
    result = _run(tmp_path, BASH, body, name)
    assert background_warnings(result) == [], result.warnings


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize(
    "body",
    [
        "( sleep 600 | cat ) >/dev/null 2>&1 &",
        "{ sleep 600; } >/dev/null 2>&1 &",
        "( sleep 600 && true ) >/dev/null 2>&1 &",
    ],
    ids=["subshell", "brace-group", "subshell-and-list"],
)
def test_control_a_group_whose_redirects_cover_it_stays_quiet(tmp_path, name, head, body):
    _quiet_bg(tmp_path, head, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize(
    "body",
    [
        "# note \\\nsleep 600 >/dev/null 2>&1 &",
        "echo ok # trailing \\\nsleep 600 >/dev/null 2>&1 &",
    ],
    ids=["whole-line-comment", "trailing-comment"],
)
def test_control_a_backslash_ending_a_comment_does_not_join_lines(tmp_path, name, head, body):
    _quiet_bg(tmp_path, head, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_control_a_comment_backslash_does_not_hide_a_real_warning(tmp_path, name, head):
    _warns_bg(tmp_path, head, "# note \\\nsleep 600 &", name)
