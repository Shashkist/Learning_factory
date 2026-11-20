"""Acceptance tests for issue #326: validator follow-ups from the #308 review (D56, D102).

Observable behaviour: the warnings `load_labs` reports for a lab's setup.sh / check.sh.

AC1: `&>` is quiet only under a bash shebang; the check runs as sh (dash in the lab image),
     where `cmd &>/dev/null &` means "background, then >/dev/null" and the job keeps the
     exec's output open. The `&>` cases of the #117, #151 and #308 exams moved here.
AC2: only the redirects of the backgrounded command itself count (the text after the last
     separator before the trailing `&`).
AC3: `exec N<&0` duplicates the script's stdin to another fd and must warn; `exec 3<file` not.
"""

import pytest
from test_issue_117_kill_timeouts import (
    background_warnings,
    lab_with_script,
    stdin_warnings,
)

NAMES = ["setup.sh", "check.sh"]
AMP_LINES = [
    "sleep 600 &>/dev/null &",
    "sleep 600 &> /dev/null &",
    "nohup sleep 600 &>/dev/null &",
    "nohup sleep 600 &> /dev/null &",
    "nohup sleep 600 &>/tmp/log &",
    "nohup sleep 600 &> /tmp/app.log &",
]
BASH_SHEBANGS = ["#!/bin/bash", "#!/usr/bin/env bash"]
NOT_BASH_HEADS = ["#!/bin/sh\n", "#!/usr/bin/env sh\n", ""]  # "" = no shebang


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


# ------------------------------------------------------------------ AC1


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("shebang", BASH_SHEBANGS)
@pytest.mark.parametrize("line", AMP_LINES)
def test_ac1_amp_redirect_is_quiet_under_a_bash_shebang(tmp_path, name, shebang, line):
    _quiet_bg(tmp_path, shebang + "\n", line, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", NOT_BASH_HEADS)
@pytest.mark.parametrize("line", AMP_LINES)
def test_ac1_amp_redirect_warns_under_sh_or_without_a_shebang(tmp_path, name, head, line):
    _warns_bg(tmp_path, head, line, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", NOT_BASH_HEADS)
def test_ac1_explicit_redirects_stay_quiet_under_sh(tmp_path, name, head):
    _quiet_bg(tmp_path, head, "sleep 600 >/dev/null 2>&1 &", name)


# ------------------------------------------------------------------ AC2

OTHER_COMMAND_WARN = [
    "echo x >/dev/null 2>&1; sleep 600 &",
    "true >/dev/null 2>&1 && sleep 600 &",
    "false >/dev/null 2>&1 || sleep 600 &",
    "echo x >/tmp/log 2>&1; sleep 600 &",
]
OWN_REDIRECT_QUIET = [
    "sleep 600 >/dev/null 2>&1 &",
    "echo x; sleep 600 >/dev/null 2>&1 &",
    "true && sleep 600 >/dev/null 2>&1 &",
    "echo x; sleep 600 >/tmp/log 2>&1 &",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", OTHER_COMMAND_WARN)
def test_ac2_redirects_of_another_command_do_not_quiet_the_background_job(
    tmp_path, name, line
):
    _warns_bg(tmp_path, "#!/bin/sh\n", line, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", OWN_REDIRECT_QUIET)
def test_ac2_the_backgrounded_commands_own_redirects_keep_it_quiet(tmp_path, name, line):
    _quiet_bg(tmp_path, "#!/bin/sh\n", line, name)


# ------------------------------------------------------------------ AC3

EXEC_DUP_WARN = ["exec 3<&0", "exec 4<&0", "exec 3<& 0", "exec 3<&  0", "exec 5<&0"]
EXEC_QUIET = ["exec 3<file", "exec 3</etc/hostname", "exec 3< /etc/hostname", "exec 3<&-"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", EXEC_DUP_WARN)
def test_ac3_exec_n_dup_of_stdin_warns(tmp_path, name, line):
    result = _run(tmp_path, "#!/bin/sh\n", line, name)
    found = stdin_warnings(result)
    assert found, f"no </dev/null warning for {line!r}: {result.warnings}"
    assert any(name in w and "02-make-file" in w for w in found), found


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", EXEC_QUIET)
def test_ac3_exec_n_from_a_file_gets_no_stdin_warning(tmp_path, name, line):
    result = _run(tmp_path, "#!/bin/sh\n", line, name)
    assert stdin_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"


# ------------------------------------------------------------------ review round 1

LONE_AMP_WARN = ["sleep 600 & echo x >/dev/null 2>&1 &"]
LONE_AMP_QUIET = ["sleep 600 >/dev/null 2>&1 & echo x >/dev/null 2>&1 &"]
FG_AMP_LINES = ["sleep 600 &>/dev/null", "apt-get install -y x &>/dev/null"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", LONE_AMP_WARN)
def test_r1_every_job_split_on_a_lone_amp_needs_its_own_redirects(tmp_path, name, line):
    _warns_bg(tmp_path, "#!/bin/sh\n", line, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", LONE_AMP_QUIET)
def test_r1_all_jobs_redirected_stays_quiet(tmp_path, name, line):
    _quiet_bg(tmp_path, "#!/bin/sh\n", line, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", NOT_BASH_HEADS)
@pytest.mark.parametrize("line", FG_AMP_LINES)
def test_r1_foreground_amp_redirect_is_a_background_job_under_sh(tmp_path, name, head, line):
    _warns_bg(tmp_path, head, line, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("shebang", BASH_SHEBANGS)
@pytest.mark.parametrize("line", FG_AMP_LINES)
def test_r1_foreground_amp_redirect_gives_no_background_warning_under_bash(
    tmp_path, name, shebang, line
):
    result = _run(tmp_path, shebang + "\n", line, name)
    assert background_warnings(result) == [], f"false positive {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", [" #!/bin/bash\n", "\t#!/bin/bash\n", "#!/bin/bashx\n"])
def test_r1_shebang_as_the_runtime_sees_it_is_not_bash(tmp_path, name, head):
    _warns_bg(tmp_path, head, "sleep 600 &>/dev/null &", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "head",
    ["#!/usr/bin/env -S bash\n", "#!/bin/bash\r\n", "#! /bin/bash\n", "#!/bin/bash -e\n"],
)
def test_r1_shebang_as_the_runtime_sees_it_is_bash(tmp_path, name, head):
    _quiet_bg(tmp_path, head, "sleep 600 &>/dev/null &", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", ["exec 10<&0", "exec 3<&0 </dev/null", "exec 4>/dev/null 3<&0"])
def test_r1_exec_dup_of_stdin_variants_warn(tmp_path, name, line):
    result = _run(tmp_path, "#!/bin/sh\n", line, name)
    assert stdin_warnings(result), f"no </dev/null warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", ["exec 3<&-", "exec 3<file"])
def test_r1_exec_quiet_variants(tmp_path, name, line):
    result = _run(tmp_path, "#!/bin/sh\n", line, name)
    assert stdin_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
def test_r1_clobber_redirect_is_not_a_pipe(tmp_path, name):
    _quiet_bg(tmp_path, "#!/bin/sh\n", "sleep 600 >|/tmp/log 2>&1 &", name)


# ------------------------------------------------------------------ review round 2

# An `&` inside $(( )) is a bitwise AND and an escaped `\&` is a literal: neither ends a job.
NOT_A_JOB = [
    "echo $((x & 1))",
    "[ $((n & 1)) -eq 0 ] && echo even",
    "x=$((a&b)); echo $x",
    "echo a \& b",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("head", ["#!/bin/sh\n", "#!/bin/bash\n"])
@pytest.mark.parametrize("line", NOT_A_JOB)
def test_r2_arithmetic_and_escaped_amp_are_not_background_jobs(tmp_path, name, head, line):
    _quiet_bg(tmp_path, head, line, name)


@pytest.mark.parametrize("name", NAMES)
def test_r2_a_real_job_after_arithmetic_still_warns(tmp_path, name):
    _warns_bg(tmp_path, "#!/bin/sh\n", "x=$((a & 1)); sleep 600 &", name)
