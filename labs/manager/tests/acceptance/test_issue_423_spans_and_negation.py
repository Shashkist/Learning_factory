"""Acceptance tests for issue #423: pipes inside substitutions, escaped delimiters and negated
jobs in a backgrounded pipeline (from #414; D121, D122).

Observable behaviour: the warnings `load_labs` reports for a lab's setup.sh / check.sh.

Every line below was timed in a real shell (WSL: dash as /bin/sh and bash 5.2) before it was
encoded: the line, then `echo done`, fed to `<shell> -s 2>&1 | cat`. If `cat` waits for the
backgrounded `sleep 3` the job holds the exec's output (D56) and the validator must warn; if
`cat` returns at once the line must stay quiet. In the issue's lines `sleep`'s stderr is the
exec's stderr (only the last element's output is redirected), which is the hold.

Measured (3 = hold for 3 s, 0 = returned at once):

  line                                                          dash  bash
  sleep 3 | tee $(ls | head -1) >/dev/null 2>&1 &                3     3
  sleep 3 | tee >(gzip | cat >x.gz) >/dev/null 2>&1 &            -     3   (dash: syntax error)
  ( sleep 3 | cat 2>/dev/null ) | cat >/dev/null 2>&1 &          3     3
  sleep 3 | grep \\) >/dev/null 2>&1 &                            3     3
  sleep 3 | head -c $((n*(m+1))) >/dev/null 2>&1 &               3     3
  echo \\` ; sleep 3 | cat \\` >/dev/null 2>&1 &                   3     3
  ! sleep 3 >/dev/null 2>&1 &                                    3     0
  ! sleep 3 2>/dev/null | cat >/dev/null 2>&1 &                  3     0
  the same jobs with stderr redirected on every element          0     0

CONTRADICTIONS with the issue text (the MEASURED behaviour is encoded):
  * The issue says the `!` line warns under sh. True, and under bash it is quiet (0 s: bash does
    not hold), so `! cmd >/dev/null 2>&1 &` under #!/bin/bash must stay quiet.
  * Under bash a `>(...)` whose body is a pipeline holds the exec's stdout and stderr by itself
    (the process-substitution subshell stays alive and inherited the outer fds), even when every
    element inside is redirected. So there is no fully redirected quiet control for `>(a | b)`;
    the quiet control for `>(...)` is a single simple command with its own redirects
    (`>(cat >x 2>/dev/null)`, measured 0 s).
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


# ------------------------------------------------------------------ AC1: warn (both shells)

# `sleep`'s stderr is the exec's stderr, so every one of these holds (3 s, dash and bash).
BOTH_WARN = [
    ("sleep 600 | tee $(ls | head -1) >/dev/null 2>&1 &", "pipe-in-dollar-paren"),
    ("( sleep 600 | cat 2>/dev/null ) | cat >/dev/null 2>&1 &", "subshell-then-pipe"),
    ("sleep 600 | grep \\) >/dev/null 2>&1 &", "escaped-close-paren"),
    ("sleep 600 | head -c $((n*(m+1))) >/dev/null 2>&1 &", "nested-arithmetic"),
    ("echo \\` ; sleep 600 | cat \\` >/dev/null 2>&1 &", "escaped-backtick"),
    # variations, timed the same way
    ("sleep 600 | tee `ls | head -1` >/dev/null 2>&1 &", "pipe-in-backticks"),
    ("sleep 600 | tee $(echo $(ls | head -1)) >/dev/null 2>&1 &", "nested-dollar-paren"),
    ('sleep 600 | tee "$(ls | head -1)" >/dev/null 2>&1 &', "quoted-dollar-paren"),
    ('sleep 600 | grep "\\)" >/dev/null 2>&1 &', "escaped-paren-in-double-quotes"),
    ('sleep 600 | grep ")" >/dev/null 2>&1 &', "paren-in-double-quotes"),
    ("sleep 600 | head -c $(( (1+2)*3 )) >/dev/null 2>&1 &", "arithmetic-with-spaced-group"),
    ('echo "\\`"; sleep 600 | cat >/dev/null 2>&1 &', "escaped-backtick-in-double-quotes"),
    ("sleep 600 | tee $(ls | head -1) | tee $(ls | tail -1) >/dev/null 2>&1 &", "two-substs"),
    ("echo \\` \\`; sleep 600 | cat >/dev/null 2>&1 &", "two-escaped-backticks"),
    ("echo \\$\\( ; sleep 600 | cat >/dev/null 2>&1 &", "escaped-dollar-paren"),
    ("x=$(echo a | cat); sleep 600 | cat >/dev/null 2>&1 &", "substitution-before-the-job"),
    ("( sleep 600 | cat ) | cat >/dev/null 2>&1 &", "subshell-pipe-unredirected"),
    ("x=a; case $x in\na) sleep 600 | cat >/dev/null 2>&1 & ;;\nesac", "case-arm-job"),
    ("echo \\` ; sleep 600 | cat \\` >/dev/null 2>&1 &\necho ok", "escaped-backtick-then-line"),
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize("body", [b for b, _ in BOTH_WARN], ids=[i for _, i in BOTH_WARN])
def test_ac1_each_line_the_walk_used_to_miss_warns_under_sh_and_bash(tmp_path, name, head, body):
    _warns_bg(tmp_path, head, body, name)


BASH_ONLY_WARN = [
    ("sleep 600 | tee >(gzip | cat >x.gz) >/dev/null 2>&1 &", "pipe-in-process-subst-out"),
    ("sleep 600 | diff <(ls | head -1) - >/dev/null 2>&1 &", "pipe-in-process-subst-in"),
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", [b for b, _ in BASH_ONLY_WARN], ids=[i for _, i in BASH_ONLY_WARN])
def test_ac1_a_pipe_inside_a_process_substitution_warns_under_bash(tmp_path, name, body):
    _warns_bg(tmp_path, BASH, body, name)


@pytest.mark.parametrize("name", NAMES)
def test_ac1_a_negated_job_holds_under_sh_and_warns(tmp_path, name):
    _warns_bg(tmp_path, SH, "! sleep 600 >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
def test_ac1_a_negated_redirected_pipeline_holds_under_sh_and_warns(tmp_path, name):
    _warns_bg(tmp_path, SH, "! sleep 600 2>/dev/null | cat >/dev/null 2>&1 &", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac1_a_negated_job_without_redirects_warns_under_both(tmp_path, name, head):
    _warns_bg(tmp_path, head, "! sleep 600 &", name)


# ------------------------------------------------------------------ AC2: quiet controls

# The same jobs with stderr redirected on every element: no hold, dash and bash (measured 0 s).
BOTH_QUIET = [
    ("sleep 600 2>/dev/null | tee $(ls | head -1) >/dev/null 2>&1 &", "pipe-in-dollar-paren"),
    (
        "( sleep 600 2>/dev/null | cat 2>/dev/null ) 2>/dev/null | cat >/dev/null 2>&1 &",
        "subshell-then-pipe",
    ),
    ("( sleep 600 | cat 2>/dev/null ) >/dev/null 2>&1 &", "subshell-redirected-as-a-whole"),
    ("sleep 600 2>/dev/null | grep \\) >/dev/null 2>&1 &", "escaped-close-paren"),
    (
        "n=2; m=3; sleep 600 2>/dev/null | head -c $((n*(m+1))) >/dev/null 2>&1 &",
        "nested-arithmetic",
    ),
    ("echo \\` ; sleep 600 2>/dev/null | cat \\` >/dev/null 2>&1 &", "escaped-backtick"),
    ("sleep 600 2>/dev/null | cat \\` >/dev/null 2>&1 &", "escaped-backtick-alone"),
    ("echo \\` ; sleep 600 >/dev/null 2>&1 &", "escaped-backtick-then-simple-job"),
    ("sleep 600 2>/dev/null | tee `ls | head -1` >/dev/null 2>&1 &", "pipe-in-backticks"),
    (
        "sleep 600 2>/dev/null | tee $(echo a | cat >/dev/null) >/dev/null 2>&1 &",
        "redirected-pipe-inside-substitution",
    ),
    ("sleep 600 2>/dev/null | tee $(ls 2>/dev/null | head -1) >/dev/null 2>&1 &", "inner-redirect"),
    ("echo `ls | head -1`; sleep 600 2>/dev/null | cat >/dev/null 2>&1 &", "substitution-before"),
    ("x=$(echo a | cat); sleep 600 >/dev/null 2>&1 &", "substitution-pipe-then-quiet-job"),
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize("body", [b for b, _ in BOTH_QUIET], ids=[i for _, i in BOTH_QUIET])
def test_ac2_the_same_jobs_with_every_element_redirected_stay_quiet(tmp_path, name, head, body):
    _quiet_bg(tmp_path, head, body, name)


# `>(...)` is bash only. A body that is a pipeline holds by itself (see the module docstring),
# so the quiet control is a single simple command with its own redirects (measured 0 s).
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "body",
    [
        "sleep 600 2>/dev/null | tee >(cat >/tmp/x.gz 2>/dev/null) >/dev/null 2>&1 &",
        "sleep 600 2>/dev/null | tee >(cat 2>/dev/null >/dev/null) >/dev/null 2>&1 &",
    ],
    ids=["process-subst-single-command", "process-subst-two-redirects"],
)
def test_ac2_a_redirected_single_command_in_a_process_substitution_stays_quiet(
    tmp_path, name, body
):
    _quiet_bg(tmp_path, BASH, body, name)


# `! cmd ... &` under bash does not hold (measured 0 s); under dash it does.
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "body",
    ["! sleep 600 >/dev/null 2>&1 &", "! sleep 600 2>/dev/null | cat >/dev/null 2>&1 &"],
    ids=["negated-simple", "negated-pipeline"],
)
def test_ac2_a_negated_redirected_job_under_bash_stays_quiet(tmp_path, name, body):
    _quiet_bg(tmp_path, BASH, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "body", ["! false && sleep 600 >/dev/null 2>&1 &", "! true || sleep 600 >/dev/null 2>&1 &"]
)
def test_ac2_under_sh_a_negated_instant_builtin_then_a_redirected_job_stays_quiet(
    tmp_path, name, body
):
    # measured: dash 0 s (D122 sh exception, `!` is stripped), bash 3 s (and-or list holds)
    _quiet_bg(tmp_path, SH, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "body", ["! false && sleep 600 >/dev/null 2>&1 &", "! true || sleep 600 >/dev/null 2>&1 &"]
)
def test_ac1_under_bash_the_same_negated_and_or_lists_warn(tmp_path, name, body):
    _warns_bg(tmp_path, BASH, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac2_a_case_pattern_close_paren_is_not_an_unmatched_paren_of_the_job(tmp_path, name, head):
    # `a)` opens no paren on its line: it must neither hide nor invent a warning.
    _quiet_bg(tmp_path, head, "x=a; case $x in\na) sleep 600 >/dev/null 2>&1 & ;;\nesac", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac2_a_case_pattern_does_not_hide_an_unredirected_job(tmp_path, name, head):
    _warns_bg(tmp_path, head, "x=a; case $x in\na) sleep 600 &\n;;\nesac", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize(
    "body",
    [
        "( sleep 600 >/dev/null 2>&1 & )",
        "( sleep 600 2>/dev/null | cat >/dev/null 2>&1 & )",
        "( cd /; sleep 600 >/dev/null 2>&1 & )",
    ],
    ids=["subshell-end", "subshell-end-pipeline", "subshell-end-after-cd"],
)
def test_ac2_a_job_that_ends_a_subshell_stays_quiet_when_redirected(tmp_path, name, head, body):
    _quiet_bg(tmp_path, head, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", SHEBANGS)
def test_ac2_a_job_that_ends_a_subshell_warns_when_it_holds(tmp_path, name, head):
    _warns_bg(tmp_path, head, "( sleep 600 & )", name)


# ------------------------------------------------------------------ AC3: linear (#343, #355)


def _elapsed(tmp_path, head, body, name="setup.sh"):
    start = time.perf_counter()
    result = _run(tmp_path, head, body, name)
    return time.perf_counter() - start, result


LINEAR_BODIES = [
    ("$(" * REPS + " &", "unbalanced-dollar-paren"),
    ("echo $(ls | head -1) " * REPS + "| cat &", "many-balanced-substitutions"),
    ("`" * REPS + " &", "unbalanced-backticks"),
    ("echo `ls | cat` " * REPS + "| cat &", "many-balanced-backtick-pairs"),
    ("\\`" * REPS + " | cat &", "escaped-backticks"),
    ("echo \\` x " * REPS + "; sleep 600 | cat &", "escaped-backticks-spaced"),
    ("((" * REPS + " &", "unbalanced-double-parens"),
    ("$((" * REPS + " &", "unbalanced-arithmetic"),
    ("$((1+(" * REPS + " &", "unbalanced-nested-arithmetic"),
    ("$(( (1+2)*3 )) " * REPS + "| cat &", "many-balanced-arithmetic"),
    ("<(" * REPS + " &", "unbalanced-process-subst-in"),
    (">(" * REPS + " &", "unbalanced-process-subst-out"),
    ("<(a | b) >(c | d) " * REPS + "| cat &", "many-balanced-process-substs"),
    (") " * REPS + "| cat &", "many-unmatched-close-parens"),
    ("\\) " * REPS + "| cat &", "many-escaped-close-parens"),
    ("sleep 1 | " * REPS + "tee $(ls | head -1) &", "long-pipeline-then-substitution"),
    ("$(" * (REPS // 2) + ")" * (REPS // 2) + " | cat &", "deeply-nested-balanced"),
    ("$(`$((" * (REPS // 3) + " | cat &", "mixed-openers"),
    ("! " * REPS + "sleep 1 &", "many-negations"),
    ("echo a $(b | c) `d | e` \\` \\) $((1+(2))) " * (REPS // 5) + "| cat &", "mixed-everything"),
]


@pytest.mark.parametrize("head", SHEBANGS)
@pytest.mark.parametrize("body", [b for b, _ in LINEAR_BODIES], ids=[i for _, i in LINEAR_BODIES])
def test_ac3_a_long_line_full_of_openers_and_closers_validates_fast(tmp_path, head, body):
    took, _ = _elapsed(tmp_path, head, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("head", SHEBANGS)
def test_ac3_many_separate_lines_with_substitutions_validate_fast(tmp_path, head):
    body = "sleep 600 2>/dev/null | tee $(ls | head -1) >/dev/null 2>&1 &\n" * (REPS // 4)
    took, _ = _elapsed(tmp_path, head, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"


@pytest.mark.parametrize("head", SHEBANGS)
def test_ac3_many_separate_escaped_backtick_lines_validate_fast(tmp_path, head):
    body = "echo \\` ; sleep 600 | cat \\` >/dev/null 2>&1 &\n" * (REPS // 4)
    took, _ = _elapsed(tmp_path, head, body)
    assert took < TIME_BOUND_S, f"took {took:.1f}s (bound {TIME_BOUND_S}s)"
