"""Acceptance tests for issue #308: validator follow-ups (decision D102).

Observable behaviour: the warnings that `load_labs` reports for a lab's setup.sh / check.sh
(spec sections 5-7, D56). Items 1, 2, 3 and 8; the kill items moved to #320.

Item 1: `2>&1` is quiet only after stdout went to a file, or stderr goes to a file itself.
Item 2: no warning for a pipeline split over two lines, nor for lines inside here-document bodies.
Item 3: `su ... -c 'read x; ...'`, `/bin/sh -s` without a redirect, `then while read`, and a `#`
        inside quotes is not a comment.
Item 8: `<&0` is not a stdin redirect; a `while read` loop in a function warns wherever the header
        sits unless it has a real redirect (here-document); shipped Linux 1 tasks are clean.

Cases that are green today are guards against a fix that over-reaches; they must stay green.
"""

from pathlib import Path

import pytest
from test_issue_117_kill_timeouts import (
    background_warnings,
    lab_with_script,
    stdin_warnings,
)

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
CONTENT = REPO_ROOT / "labs" / "content"
LINUX_1 = CONTENT / "linux" / "linux-1"
NAMES = ["setup.sh", "check.sh"]


def _script(tmp_path, body, name):
    return lab_with_script(tmp_path, f"#!/bin/sh\n{body}\nexit 0\n", name)


def _must_warn_stdin(tmp_path, body, name):
    result = _script(tmp_path, body, name)
    found = stdin_warnings(result)
    assert found, f"no </dev/null warning for {body!r}: {result.warnings}"
    assert any(name in w and "02-make-file" in w for w in found), found


def _must_not_warn(tmp_path, body, name):
    result = _script(tmp_path, body, name)
    assert result.warnings.get("test/good", []) == [], (
        f"false positive for {body!r}: {result.warnings}"
    )


# ------------------------------------------------------------------ item 1

BG_WARN_ORDER = [
    "sleep 600 2>&1 >/dev/null &",
    "sleep 600 2>&1 >/tmp/log &",
    "sleep 600 2>&1 > /tmp/log &",
]
BG_QUIET = [
    "sleep 600 >/dev/null 2>&1 &",
    "sleep 600 >/tmp/log 2>&1 &",
    "sleep 600 >>/tmp/log 2>&1 &",
    "sleep 600 &>/dev/null &",
    "sleep 600 >/tmp/out 2>/tmp/err &",
    "sleep 600 2>/tmp/err >/tmp/out &",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", BG_WARN_ORDER)
def test_item1_stderr_duplicated_before_stdout_moves_still_warns(tmp_path, name, line):
    result = _script(tmp_path, line, name)
    found = background_warnings(result)
    assert found, f"no warning for {line!r}: {result.warnings}"
    assert any(name in w and "02-make-file" in w for w in found), found


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("line", BG_QUIET)
def test_item1_a_stdout_file_then_2_to_1_or_stderr_to_a_file_is_quiet(tmp_path, name, line):
    _must_not_warn(tmp_path, line, name)


# ------------------------------------------------------------------ item 2

PIPE_SPLIT = [
    "ls /etc |\nwhile read f; do echo $f; done",
    "ls /etc |\n  while IFS= read -r f; do echo $f; done",
    "ls /etc |\nwhile read f\ndo\n  echo $f\ndone",
    "cat /etc/hostname |\nread x",
]
HEREDOC_BODIES = [
    "cat <<EOF\nwhile read x; do echo $x; done\nEOF",
    "cat <<EOF\nx=$(cat)\nsu student -c 'read x'\nEOF",
    "cat <<'EOF'\nsleep 600 &\nEOF",
    'cat <<"END"\nwhile read x; do :; done\nEND',
    "cat <<-EOF\n\twhile read x; do :; done\n\tEOF",
    "cat <<-'EOF'\n\tsleep 600 &\n\tEOF",
    "cat >/tmp/helper.sh <<WORD\nwhile read x; do :; done\nWORD",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", PIPE_SPLIT)
def test_item2_a_pipeline_split_over_two_lines_gets_no_warning(tmp_path, name, body):
    _must_not_warn(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", HEREDOC_BODIES)
def test_item2_lines_inside_a_here_document_body_get_no_warning(tmp_path, name, body):
    _must_not_warn(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
def test_item2_a_reader_after_the_here_document_ended_still_warns(tmp_path, name):
    _must_warn_stdin(tmp_path, "cat <<EOF\nhello\nEOF\nwhile read x; do echo $x; done", name)


@pytest.mark.parametrize("name", NAMES)
def test_item2_a_pipe_ending_one_line_does_not_excuse_a_reader_two_lines_later(tmp_path, name):
    _must_warn_stdin(tmp_path, "ls /etc |\nsort >/tmp/s\nwhile read x; do echo $x; done", name)


# ------------------------------------------------------------------ item 3

ITEM3_WARN = [
    "su student -c 'read x; echo $x'",
    'su student -c "read x; echo $x"',
    "su student -s /bin/sh -c '/bin/sh -s'",
    "su student -s /bin/sh -c '/bin/sh -s' 2>/dev/null",
    "if true; then while read x; do echo $x; done; fi",
    "if [ -f /etc/hostname ]; then while IFS= read -r x; do echo $x; done; fi",
    "if true\nthen\n  while read x; do echo $x; done\nfi",
]
ITEM3_QUIET = [
    "su student -c 'read x; echo $x' </dev/null",
    "su student -s /bin/sh -c '/bin/sh -s' <<'READER'\necho hi\nREADER",
    "su student -s /bin/sh -c '/bin/sh -s' </dev/null",
    "if true; then while read x; do echo $x; done </dev/null; fi",
    "if true; then while read x; do echo $x; done < /etc/hostname; fi",
    "if true; then ls | while read x; do echo $x; done; fi",
    "sh -c 'echo a #b' >/dev/null 2>&1 &",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", ITEM3_WARN)
def test_item3_these_stdin_readers_warn(tmp_path, name, body):
    _must_warn_stdin(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", ITEM3_QUIET)
def test_item3_the_same_commands_with_a_real_redirect_or_quiet_job_get_no_warning(
    tmp_path, name, body
):
    _must_not_warn(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "line",
    ["sh -c 'echo a #b' &", 'sh -c "echo a #b" &', "echo 'a # b' &"],
)
def test_item3_a_hash_inside_quotes_is_not_a_comment_so_the_background_warning_stays(
    tmp_path, name, line
):
    result = _script(tmp_path, line, name)
    found = background_warnings(result)
    assert found, f"no background warning for {line!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
def test_item3_a_real_trailing_comment_still_hides_what_it_says(tmp_path, name):
    _must_not_warn(tmp_path, "echo hi  # sleep 600 &", name)


# ------------------------------------------------------------------ item 8

FUNCTION_LOOPS_WARN = [
    "f() { while read x; do echo $x; done; }",
    "f() { while IFS= read -r x; do echo $x; done; }",
    "f() {\n  while read x; do echo $x; done\n}",
    "f()\n{\n  while read x; do echo $x; done\n}",
    "f() { while read x; do echo $x; done <&0; }",
    "f() {\n  while IFS= read -r x; do echo $x; done <&0\n}",
    "f()\n{\n  while read x\n  do\n    echo $x\n  done <&0\n}",
]
FUNCTION_LOOPS_QUIET = [
    "f() { while read x; do echo $x; done <<EOF\n$1\nEOF\n}",
    'f() {\n  while IFS= read -r l; do case $l in *"$1"*) return 0 ;; esac; done <<EOF\n$2\nEOF\n'
    "  return 1\n}",
    "f()\n{\n  while read x; do echo $x; done <<EOF\n$1\nEOF\n}",
    "f() { while read x; do echo $x; done </dev/null; }",
    "f() { while read x; do echo $x; done < /etc/hostname; }",
]


@pytest.mark.parametrize("name", NAMES)
def test_item8_a_top_level_done_from_fd_0_still_reads_the_script_and_warns(tmp_path, name):
    _must_warn_stdin(tmp_path, "while read x; do echo $x; done <&0", name)


@pytest.mark.parametrize("name", NAMES)
def test_item8_a_multi_line_top_level_loop_ending_in_fd_0_warns(tmp_path, name):
    _must_warn_stdin(tmp_path, "while read x\ndo\n  echo $x\ndone <&0", name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", FUNCTION_LOOPS_WARN)
def test_item8_a_read_loop_in_a_function_warns_wherever_the_header_sits(tmp_path, name, body):
    _must_warn_stdin(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", FUNCTION_LOOPS_QUIET)
def test_item8_a_read_loop_in_a_function_with_a_real_redirect_gets_no_warning(tmp_path, name, body):
    _must_not_warn(tmp_path, body, name)


def test_item8_the_shipped_linux_1_tasks_validate_with_no_warnings():
    # Green today and must stay green after the 04/05 rewrite.
    from app.content import load_labs

    assert LINUX_1.is_dir()
    result = load_labs(CONTENT)
    assert result.labs, result.errors
    assert result.warnings.get("linux/linux-1", []) == [], dict(result.warnings)
    assert not result.warnings, dict(result.warnings)


def test_item8_no_shipped_task_script_relies_on_the_fd_0_redirect_any_more():
    offenders = []
    for path in sorted(CONTENT.glob("*/*/tasks/*/*.sh")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "<&0" in line:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{n}")
    assert offenders == [], offenders


@pytest.mark.parametrize("task", ["04-copy-move", "05-find-file"])
def test_item8_the_04_and_05_check_scripts_exist_and_have_no_warnings(task):
    from app.content import load_labs

    assert (LINUX_1 / "tasks" / task / "check.sh").is_file()
    result = load_labs(CONTENT)
    assert not [w for w in result.warnings.get("linux/linux-1", []) if task in w]


# ------------------------------------------------------------------ review round 1

BG_AFTER_FAKE_HEREDOC = [
    'echo "use <<EOF" >/tmp/u; cat <<X\nhi\nX\nsleep 600 &',
    "mask=$((1 << 6))\nsleep 600 &",
    "cat >/tmp/n <<END-NOTE\nhi\nEND-NOTE\nsleep 600 &",
]
READER_AFTER_FAKE_HEREDOC = [
    "x=$((1<<n))\nwhile read x; do echo $x; done",
    "mask=$((1 << 6))\nwhile read x; do echo $x; done",
    'echo "use <<EOF" >/tmp/u\nwhile read x; do echo $x; done',
]
SU_SCOPE_WARN = [
    "su student -c 'read x'; echo done </dev/null",
    "su student -c 'read x'; echo '<'",
    "su student -c 'echo hi; read x'",
    'su student -c "echo hi; read x"',
    "su student -c 'read x' && echo ok </dev/null",
]
SU_SCOPE_QUIET = [
    "su student -c 'read x' </dev/null; echo hi",
    "su student -c 'echo hi; read x' </dev/null",
    "su student -c 'read x' </dev/null && echo ok",
]
ESCAPED_QUOTES = [
    "echo can\\'t; while read x; do echo $x; done; echo won\\'t",
    'echo say\\"hi; while read x; do echo $x; done; echo \\"',
]
FD0_SPACED = [
    "while read x; do echo $x; done <& 0",
    "while read x\ndo\n  echo $x\ndone <&  0",
]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", BG_AFTER_FAKE_HEREDOC)
def test_round1_a_background_job_after_a_not_a_here_document_still_warns(tmp_path, name, body):
    result = _script(tmp_path, body, name)
    found = background_warnings(result)
    assert found, f"no background warning for {body!r}: {result.warnings}"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", READER_AFTER_FAKE_HEREDOC)
def test_round1_a_reader_after_a_not_a_here_document_still_warns(tmp_path, name, body):
    _must_warn_stdin(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", SU_SCOPE_WARN)
def test_round1_su_reader_is_judged_by_its_own_redirect_only(tmp_path, name, body):
    _must_warn_stdin(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", SU_SCOPE_QUIET)
def test_round1_su_reader_with_its_own_redirect_is_quiet(tmp_path, name, body):
    _must_not_warn(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", ESCAPED_QUOTES)
def test_round1_an_escaped_quote_does_not_hide_the_loop(tmp_path, name, body):
    _must_warn_stdin(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("body", FD0_SPACED)
def test_round1_fd_0_with_a_space_is_still_not_a_redirect(tmp_path, name, body):
    _must_warn_stdin(tmp_path, body, name)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "body",
    [
        "while read x; do echo $x; done <<END-NOTE\nhi\nEND-NOTE",
        "while read x; do echo $x; done <<'END-NOTE'\nhi\nEND-NOTE",
        "echo \"a << b\"; while read x; do echo $x; done </dev/null",
        "mask=$((1 << 6)); while read x; do echo $x; done </dev/null",
    ],
)
def test_round1_real_here_documents_and_redirects_stay_quiet(tmp_path, name, body):
    _must_not_warn(tmp_path, body, name)
