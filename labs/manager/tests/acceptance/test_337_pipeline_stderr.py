"""Acceptance tests for issue #337: stderr of earlier elements in a backgrounded pipeline (D56).

Observable behaviour: the warnings `load_labs` reports for a lab's setup.sh / check.sh.

AC1: in a backgrounded pipeline every element before a plain `|` needs its own stderr
     redirect, because the pipe carries only stdout: an element's stderr still points at
     the exec's output and holds it open after the script ends. `|&` covers the element
     before it under bash only (dash reads `|&` as `|` then `&`).
AC2: the exam cases from the issue.
"""

import pytest
from test_issue_117_kill_timeouts import background_warnings, lab_with_script

NAMES = ["setup.sh", "check.sh"]
SHEBANGS = ["#!/bin/sh\n", "#!/bin/bash\n"]


def _run(tmp_path, head, body, name):
    return lab_with_script(tmp_path, f"{head}{body}\nexit 0\n", name)


def _warns_bg(tmp_path, head, body, name):
    result = _run(tmp_path, head, body, name)
    found = background_warnings(result)
    assert found, f"no background warning for {head!r} {body!r}: {result.warnings}"
    assert any(name in w and "02-make-file" in w for w in found), found


def _quiet_bg(tmp_path, head, body, name):
    result = _run(tmp_path, head, body, name)
    assert result.warnings.get("test/good", []) == [], (
        f"false positive {head!r} {body!r}: {result.warnings}"
    )


# ------------------------------------------------------------------ AC2 (issue cases)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac2_earlier_element_without_stderr_redirect_warns(tmp_path, name, head):
    _warns_bg(tmp_path, head, "sleep 600 | cat >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac2_earlier_element_with_its_own_stderr_redirect_stays_quiet(tmp_path, name, head):
    _quiet_bg(tmp_path, head, "sleep 600 2>/dev/null | cat >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
def test_ac2_pipe_amp_covers_the_earlier_element_under_bash(tmp_path, name):
    _quiet_bg(tmp_path, "#!/bin/bash\n", "sleep 600 |& cat >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
def test_ac2_pipe_amp_warns_under_sh(tmp_path, name):
    _warns_bg(tmp_path, "#!/bin/sh\n", "sleep 600 |& cat >/dev/null 2>&1 &", name)


# ------------------------------------------------------------------ AC1


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac1_three_elements_only_the_middle_lacks_a_stderr_redirect_warns(
    tmp_path, name, head
):
    _warns_bg(
        tmp_path, head, "sleep 600 2>/dev/null | cat | cat >/dev/null 2>&1 &", name
    )


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac1_three_elements_only_the_first_lacks_a_stderr_redirect_warns(
    tmp_path, name, head
):
    _warns_bg(
        tmp_path, head, "sleep 600 | cat 2>/dev/null | cat >/dev/null 2>&1 &", name
    )


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac1_three_elements_all_redirected_stays_quiet(tmp_path, name, head):
    _quiet_bg(
        tmp_path,
        head,
        "sleep 600 2>/dev/null | cat 2>/dev/null | cat >/dev/null 2>&1 &",
        name,
    )


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac1_stderr_into_the_pipe_is_consumed_by_the_next_element(tmp_path, name, head):
    """`2>&1` on an earlier element sends its stderr into the pipe, not to the exec's output.

    D56: the exec's output stays held open only while an element's stderr still points at
    it. Here `sleep`'s stderr (and stdout) go down the pipe to `cat`, whose own stdout and
    stderr end in /dev/null, so nothing holds the exec's output open: quiet.
    """
    _quiet_bg(tmp_path, head, "sleep 600 2>&1 | cat >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac1_stderr_redirect_to_a_file_on_an_earlier_element_stays_quiet(
    tmp_path, name, head
):
    _quiet_bg(tmp_path, head, "sleep 600 2>/tmp/err.log | cat >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
def test_ac1_pipe_amp_in_the_middle_under_bash_covers_only_the_element_before_it(
    tmp_path, name
):
    _warns_bg(
        tmp_path,
        "#!/bin/bash\n",
        "sleep 600 |& cat | cat >/dev/null 2>&1 &",
        name,
    )
    # the middle `cat` is followed by a plain `|` and has no stderr redirect


# ------------------------------------------------- review round 1: no false positives


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize(
    "body",
    [
        "( sleep 600 | cat ) >/dev/null 2>&1 &",
        "(sleep 600 | cat | cat) >/dev/null 2>&1 &",
        'case "$1" in web|http) sleep 600 >/dev/null 2>&1 & ;; esac',
        "case $1 in a | b) sleep 600 >/dev/null 2>&1 & ;; esac",
        "sleep $(echo 600 | cat) >/dev/null 2>&1 &",
        "sleep `echo 600 | cat` >/dev/null 2>&1 &",
    ],
    ids=["subshell-group", "subshell-three", "case-pattern", "case-pattern-spaced",
         "command-substitution", "backticks"],
)
def test_review_a_pipe_inside_a_group_case_pattern_or_substitution_is_not_walked(
    tmp_path, name, head, body
):
    """A `|` inside `( ... )`, a `case` pattern or `$( ... )` is not a pipe of the
    backgrounded pipeline: the group's own redirect covers every element inside it."""
    _quiet_bg(tmp_path, head, body, name)


# ------------------------------------------------- review round 1: missed warnings


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_review_an_earlier_element_whose_stdout_goes_to_the_exec_stderr_warns(
    tmp_path, name, head
):
    """`>&2` makes sleep's stdout a copy of the exec's stderr: it holds the output open
    even though its own stderr is redirected."""
    _warns_bg(tmp_path, head, "sleep 600 >&2 2>/dev/null | cat >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
def test_review_pipe_amp_after_the_elements_own_stdout_redirect_to_stderr_warns(
    tmp_path, name
):
    """Bash applies `|&`'s implicit `2>&1` after the element's own redirects, so with
    `>&2` both of sleep's outputs end on the exec's stderr."""
    _warns_bg(tmp_path, "#!/bin/bash\n", "sleep 600 >&2 |& cat >/dev/null 2>&1 &", name)
