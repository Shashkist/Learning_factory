"""Acceptance tests for issue #366: validator follow-ups of #355 / #365.

Observable behaviour: the warnings and errors `load_labs` reports for a lab's setup.sh / check.sh.

AC1: `>&\\N` behaves exactly like `>&N` and `>&\\-` like `>&-` (bash dups it): the background
     verdict of each backslash form equals its unquoted twin. Warn for `>&\\2 2>/dev/null &`
     and `>&\\2 2>&1 &`; quiet for `2>/dev/null >&\\2 &` and `>&\\- 2>/dev/null &` (D56).
AC2: a `su ... -c 'cat'` that is fed by a pipe (not the first command of a pipeline) reads that
     pipe, not the script: no stdin warning. The first command of a line still warns.
AC3 (D115): no line-length cap: the long lines of the timing cases load cleanly, and a long
     line that holds a real stdin reader still warns.
AC4: the per-command su path is linear: `su x -c '' ` and `su x -c 'echo' </dev/null; `
     repeated 20000 times validate fast.
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

REPS = 20000
TIME_BOUND_S = 2.0


def _elapsed(tmp_path, head, body, name="setup.sh"):
    start = time.perf_counter()
    result = lab_with_script(tmp_path, f"{head}{body}\nexit 0\n", name)
    return time.perf_counter() - start, result


def _loads_cleanly(result):
    return "good" in result.labs and result.errors == {}


# ------------------------------------------------------------------ AC1: >&\2 plus another redirect


# (unquoted twin, backslash form, warns). Checked with real shells (Git Bash `bash -c`, dash):
# - `>&\2 2>/dev/null`: `echo hi >&\2 2>/dev/null` prints "hi" on stderr (stdout copied the
#   exec's stderr before it moved): warn.
# - `>&\2 2>&1`: stdout and stderr both stay on the exec's stderr: warn.
# - `2>/dev/null >&\2`: `echo hi 2>/dev/null >&\2` prints nothing (stderr went to /dev/null
#   first, stdout copies that): quiet.
# - `>&\- 2>/dev/null`: closes stdout like `>&-` (dash: `echo hi >&\-` -> "I/O error"; bash:
#   bad file descriptor) and stderr goes to /dev/null: nothing stays open: quiet.
TWINS = [
    ("sleep 600 >&2 2>/dev/null &", r"sleep 600 >&\2 2>/dev/null &", True),
    ("sleep 600 >&2 2>&1 &", r"sleep 600 >&\2 2>&1 &", True),
    ("sleep 600 2>/dev/null >&2 &", r"sleep 600 2>/dev/null >&\2 &", False),
    ("sleep 600 >&- 2>/dev/null &", r"sleep 600 >&\- 2>/dev/null &", False),
]


def _warns(tmp_path, tag, line, name):
    result = lab_with_script(tmp_path / tag, f"{BASH}{line}\nexit 0\n", name)
    return bool(background_warnings(result))


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(("plain", "escaped", "warns"), TWINS)
def test_ac1_gt_amp_backslash_copy_follows_its_unquoted_twin(tmp_path, name, plain, escaped, warns):
    # pins: `>&\N` is `>&N` and `>&\-` is `>&-`: equal verdicts, and the expected one.
    got_plain = _warns(tmp_path, "plain", plain, name)
    got_escaped = _warns(tmp_path, "escaped", escaped, name)
    assert got_plain == warns, f"unquoted {plain!r}: warns={got_plain}, expected {warns}"
    assert got_escaped == got_plain, f"{escaped!r} (warns={got_escaped}) differs from {plain!r}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "line",
    [
        "sleep 600 >&2 2>/dev/null &",
        "sleep 600 >& 2 2>/dev/null &",
        "sleep 600 2>&1 >/dev/null &",
    ],
)
def test_ac1_neighbour_descriptor_copies_keep_warning(tmp_path, name, line):
    # pins: the neighbours (#343) keep their result: a copy of fd 2 is not a file.
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert background_warnings(result), f"missed warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "line",
    [
        "sleep 600 >&/dev/null &",
        "sleep 600 >& /dev/null &",
        "sleep 600 >&/tmp/app.log 2>&1 &",
        "sleep 600 >/dev/null 2>&1 &",
        "sleep 600 >/dev/null 2>/dev/null &",
    ],
)
def test_ac1_neighbour_real_files_stay_quiet(tmp_path, name, line):
    # pins: the fix did not make real file redirects noisy.
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert background_warnings(result) == [], f"false warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "line", ["sleep 600 >&- &", "fd=3; sleep 600 >&$fd &", 'sleep 600 >&"$LOGFD" &']
)
def test_ac1_neighbour_closed_or_expanded_descriptors_keep_warning(tmp_path, name, line):
    # pins: #343 results for `>&-`, `>&$fd`, `>&"$X"` stay (warn).
    result = lab_with_script(tmp_path, f"{BASH}{line}\nexit 0\n", name)
    assert background_warnings(result), f"missed warning for {line!r}: {result.warnings}"


# ------------------------------------------------------------------ AC2: su fed by a pipe


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
@pytest.mark.parametrize(
    "line",
    [
        "su student -c 'true' | su student -c 'cat'",
        "printf 'x' | su student -c 'cat > notes'",
        "printf 'x' | su student -c 'read line'",
        "echo a | su student -c 'cat' | su student -c 'cat'",
        "su student -c 'true' </dev/null | su student -c 'cat'",
    ],
)
def test_ac2_a_su_fed_by_a_pipe_reads_the_pipe_not_the_script(tmp_path, name, head, line):
    # pins: the stdin of a later pipeline command is the pipe; no `add </dev/null` warning.
    result = lab_with_script(tmp_path, f"{head}{line}\nexit 0\n", name)
    assert stdin_warnings(result) == [], f"false warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
@pytest.mark.parametrize(
    "line",
    [
        "su student -c 'cat'",
        "su student -c 'cat' | tee /tmp/out",
        "su student -c 'cat' | su student -c 'cat'",
        "true; su student -c 'cat'",
        "true && su student -c 'cat'",
        "printf 'x' | cat; su student -c 'cat'",
        "su student -c 'true' </dev/null; su student -c 'cat'",
    ],
)
def test_ac2_the_first_command_of_a_pipeline_still_warns(tmp_path, name, head, line):
    # pins: only a command that follows a `|` is fed by a pipe; the first one reads the script.
    result = lab_with_script(tmp_path, f"{head}{line}\nexit 0\n", name)
    assert stdin_warnings(result), f"missed stdin warning for {line!r}: {result.warnings}"


# ------------------------------------------------------------------ AC3 (D115): no line cap


@pytest.mark.parametrize("head", [BASH, SH])
@pytest.mark.parametrize("unit", ["su -c ", "su "], ids=["su-dash-c", "su"])
def test_ac3_a_long_run_of_su_loads_cleanly(tmp_path, head, unit):
    # pins: D115, a long line is not a lab error and not dropped as an error.
    _, result = _elapsed(tmp_path, head, unit * REPS)
    assert _loads_cleanly(result), result.errors


@pytest.mark.parametrize("unit", ["su -c ", "su "], ids=["su-dash-c", "su"])
def test_ac3_a_long_su_run_with_an_ampersand_loads_cleanly(tmp_path, unit):
    _, result = _elapsed(tmp_path, BASH, unit * REPS + "&")
    assert _loads_cleanly(result), result.errors


@pytest.mark.parametrize("head", [BASH, SH])
def test_ac3_a_long_run_of_arithmetic_openers_loads_cleanly(tmp_path, head):
    _, result = _elapsed(tmp_path, head, "$((" * REPS)
    assert _loads_cleanly(result), result.errors


def test_ac3_a_long_mixed_line_loads_cleanly(tmp_path):
    body = "echo $((1+2)) su student -c " + "$(( su " * REPS
    _, result = _elapsed(tmp_path, BASH, body)
    assert _loads_cleanly(result), result.errors


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [BASH, SH])
def test_ac3_a_long_line_with_a_real_reader_still_warns(tmp_path, name, head):
    # pins: no silent skip of long lines: a stdin reader at the end of a long line is found.
    took, result = _elapsed(tmp_path, head, "su " * REPS + "x -c 'cat'", name)
    assert _loads_cleanly(result), result.errors
    assert stdin_warnings(result), result.warnings
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("name", NAMES)
def test_ac3_a_long_line_with_a_real_background_job_still_warns(tmp_path, name):
    body = "echo x " * REPS + "; sleep 600 &"
    _, result = _elapsed(tmp_path, BASH, body, name)
    assert _loads_cleanly(result), result.errors
    assert background_warnings(result), result.warnings


# ------------------------------------------------------------------ AC4: per-command su speed


@pytest.mark.parametrize("head", [BASH, SH])
@pytest.mark.parametrize(
    "unit",
    ["su x -c '' ", "su x -c 'echo' </dev/null; "],
    ids=["empty-command", "echo-with-devnull"],
)
def test_ac4_a_long_run_of_complete_su_commands_validates_fast(tmp_path, head, unit):
    # pins: each complete `su ... -c '...'` is found in linear time (no per-command slicing:
    # ~188 s at 20000 reps on the first #355 version, 0.16 s on the linear one).
    took, result = _elapsed(tmp_path, head, unit * REPS)
    assert took < TIME_BOUND_S, f"{REPS} x {unit!r} took {took:.1f}s (bound {TIME_BOUND_S}s)"
    assert _loads_cleanly(result), result.errors
    assert stdin_warnings(result) == [], result.warnings
