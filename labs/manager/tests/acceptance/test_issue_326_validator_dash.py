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
    assert background_warnings(result) == [], f"false positive {head!r} {body!r}: {result.warnings}"


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
