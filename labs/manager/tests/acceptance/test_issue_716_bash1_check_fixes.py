"""Acceptance tests for issue #716: bash-1 checks never fail a correct script for an unstated reason.

Follow-up of #677 (lab bash/bash-1). The older exam test_issue_677_bash_scripting_lab.py stays as it
is; its fixtures and helpers are reused. Choices pinned here (the builder follows them):

1. Task 07 (backup). Checks run in the POSIX locale, where `tar tzf` prints a Hebrew file name as
   escaped octal, which no longer matches what `find` prints. The check lists the archive with
   `tar --quoting-style=literal -tzf` (static case) and a correct backup.sh passes when ~/notes
   holds a file with a Hebrew name (docker case). A script that leaves the Hebrew-named file out of
   the archive still fails.
2. Task 08 (space trap). CHOICE: the check stops requiring exit code 0, as task 06 does. The task
   text never asked for an exit code, so no text changes; the item "prints the first line" is judged
   by the output alone, and a script that prints the right line but exits 3 passes. This keeps every
   older #677 case valid (none of them says task 08 must fail on an exit code; the solution still
   exits 0 and the unquoted near misses still fail by their output).
3. Tasks 04 to 08. A script whose first line is not `#!` gets its own FAIL line whose hint tells the
   student to start the script with `#!/bin/bash` (the hints of the checks are English only, so one
   substring serves both languages). A missing shebang fails the check even where dash would run the
   script correctly.

Static cases run everywhere; docker cases (marker `docker`) run in CI.
"""
# ruff: noqa: F811, E501
import re

import pytest
from test_issue_12_linux_1 import (  # noqa: F401  (fixtures are used by name)
    _as_root,
    _docker,
    _text,
    docker_available,
    image,
    machine,
    runtime_flag,
)
from test_issue_677_bash_scripting_lab import (  # noqa: F401
    _alternative,
    _assert_failing_with_hints,
    _assert_passing,
    _check,
    _near_miss,
    _setup,
    _solve,
    _student,
    _task_dir,
    _write_script,
)

SHEBANG_TASKS = [
    ("04-exists", "exists.sh"),
    ("05-usage", "usage.sh"),
    ("06-count-lines", "count_lines.sh"),
    ("07-backup", "backup.sh"),
    ("08-space-trap", "firstline.sh"),
]
# a Hebrew word as octal UTF-8 bytes (so no Hebrew literal and no encoding trouble in the exam)
HEBREW_NAME_PRINTF = r"\327\251\327\234\327\225\327\235"


def _check_body(task_id: str) -> str:
    """The check script without its own first line (the interpreter line)."""
    return "\n".join(_text(_task_dir(task_id) / "check.sh").splitlines()[1:])


# ---------------------------------------------------------------- criterion 1: task 07, non-ASCII names


def test_task_07_check_lists_the_archive_with_literal_quoting():
    body = _check_body("07-backup")
    assert "--quoting-style=literal" in body, (
        "the 07 check must list the archive with `tar --quoting-style=literal -tzf`"
    )
    listings = [ln for ln in body.splitlines() if re.search(r"\btar\b[^\n]*\b-?t[a-z]*f\b", ln)]
    assert listings, "the 07 check no longer lists the archive"
    for line in listings:
        assert "--quoting-style=literal" in line, f"a tar listing without literal quoting: {line.strip()}"


@pytest.mark.docker
def test_task_07_correct_script_passes_when_notes_holds_a_hebrew_file_name(machine):
    _setup(machine, "07-backup")
    _solve(machine, "07-backup")
    r = _student(machine, f"printf x > ~/notes/$(printf '{HEBREW_NAME_PRINTF}').txt")
    assert r.returncode == 0, r.stderr
    _assert_passing(machine, "07-backup", "the solution with a Hebrew-named file in ~/notes")


@pytest.mark.docker
def test_task_07_hebrew_named_file_in_a_subfolder_also_passes(machine):
    _setup(machine, "07-backup")
    _solve(machine, "07-backup")
    code = f"mkdir -p ~/notes/sub && printf x > ~/notes/sub/$(printf '{HEBREW_NAME_PRINTF}')"
    assert _student(machine, code).returncode == 0
    _assert_passing(machine, "07-backup", "a Hebrew-named file in a subfolder of ~/notes")


@pytest.mark.docker
def test_task_07_script_that_leaves_the_hebrew_named_file_out_still_fails(machine):
    _setup(machine, "07-backup")
    assert _student(machine, f"printf x > ~/notes/$(printf '{HEBREW_NAME_PRINTF}').txt").returncode == 0
    body = "#!/bin/bash\ncd ~ || exit 1\ntar -czf backup.tar.gz --exclude='*.txt' notes"
    _student(machine, _write_script("backup.sh", body))
    _assert_failing_with_hints(machine, "07-backup", "for a backup.sh that skips the Hebrew-named file")


# ---------------------------------------------------------------- criterion 2: task 08, exit code


def test_task_08_check_does_not_require_exit_code_0():
    body = _check_body("08-space-trap")
    assert not re.search(r'"\$r[ab]?"\s*=\s*0\b', body), (
        "the 08 check must judge the output only (as task 06), not the exit code"
    )


@pytest.mark.docker
@pytest.mark.parametrize("exit_code", [1, 3])
def test_task_08_script_that_prints_the_right_line_but_exits_nonzero_passes(machine, exit_code):
    body = f'#!/bin/bash\nhead -n 1 "$1"\nexit {exit_code}'
    _alternative(machine, "08-space-trap", "firstline.sh", body, f"a correct script exiting {exit_code}")


@pytest.mark.docker
def test_task_08_wrong_output_and_the_unquoted_variable_still_fail(machine):
    _near_miss(machine, "08-space-trap", "firstline.sh", "#!/bin/bash\nhead -n 1 $1", "an unquoted $1")
    _near_miss(machine, "08-space-trap", "firstline.sh", '#!/bin/bash\nhead -n 2 "$1"', "two lines")
    _near_miss(machine, "08-space-trap", "firstline.sh", '#!/bin/bash\necho "$1"', "the file name")


@pytest.mark.docker
def test_task_08_solution_still_passes(machine):
    _setup(machine, "08-space-trap")
    _solve(machine, "08-space-trap")
    _assert_passing(machine, "08-space-trap", "the solution")


# ---------------------------------------------------------------- criterion 3: missing shebang


@pytest.mark.parametrize(("task_id", "script"), SHEBANG_TASKS)
def test_check_has_a_shebang_hint_that_says_to_start_with_bin_bash(task_id, script):
    body = _check_body(task_id)
    hint_lines = [ln for ln in body.splitlines() if re.search(r"\bfail\b", ln) and "#!/bin/bash" in ln]
    assert hint_lines, f"{task_id}/check.sh has no FAIL hint that says to start the script with #!/bin/bash"


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), SHEBANG_TASKS)
def test_script_without_a_shebang_gets_its_own_hint(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    first = _student(machine, f"head -n 1 ~/{script}").stdout.strip()
    assert first.startswith("#!"), f"the solution of {task_id} has no shebang to remove: {first!r}"
    assert _student(machine, f"sed -i 1d ~/{script}").returncode == 0
    _assert_failing_with_hints(machine, task_id, "for a script without a #! first line")
    res, _ = _check(machine, task_id)
    fails = [ln for ln in res.stdout.splitlines() if ln.startswith("FAIL")]
    assert any("#!/bin/bash" in ln for ln in fails), (
        f"a FAIL hint must tell the student to start the script with #!/bin/bash:\n{res.stdout}"
    )


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), SHEBANG_TASKS)
def test_a_comment_first_line_is_not_a_shebang(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    assert _student(machine, f"sed -i '1s/.*/# my script/' ~/{script}").returncode == 0
    res, _ = _check(machine, task_id)
    assert res.returncode != 0, res.stdout
    fails = [ln for ln in res.stdout.splitlines() if ln.startswith("FAIL")]
    assert any("#!/bin/bash" in ln for ln in fails), res.stdout


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), SHEBANG_TASKS)
def test_the_shebang_hint_is_absent_when_the_script_has_one(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    res, _ = _check(machine, task_id)
    assert "FAIL" not in res.stdout, res.stdout
    assert "#!/bin/bash" not in res.stdout
