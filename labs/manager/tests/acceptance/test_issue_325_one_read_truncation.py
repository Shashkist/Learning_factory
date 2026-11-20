"""Acceptance tests for issue #325: one-read truncation in 05 and 06, boundary exam cases.

  1. 06 reads the truncation flag and the lines in one read; a 201st line counts as truncation
     by root. A race between two reads cannot be pinned by a deterministic case (it needs a
     student loop that swaps the file between two root reads), so that half is review-only.
     The observable half is pinned here: a 201st correct line is truncation (fails item 3 with
     the "far longer" hint, not as a duplicate).
  2. 06: 200 right lines, a blank line 201, junk on line 202 fails item 3 (the case b5e7302 fixed).
     The hint must be the truncation one, so the case still catches a revert of b5e7302 once 06
     also fails duplicated lines (200 right lines are 50 copies of the 4 ERROR lines).
  3. 06 fails duplicated lines like 05 does (D-entry of #325). The log has only 4 ERROR lines, so
     no long file without duplicates can be correct: the old "long file under the cap passes"
     case is gone, replaced by duplicates-fail cases and an unordered no-duplicates pass case.
  4. 05: the right path, 200 blank lines, then a decoy path on line 202 fails item 2.

Students are fake. Docker tests skip only when Docker is absent.
"""
# ruff: noqa: F811
import pytest
from test_issue_12_linux_1 import (  # noqa: F401  (fixtures are used by name)
    _check,
    _docker,
    _setup,
    docker_available,
    image,
    machine,
    runtime_flag,
)
from test_issue_126_linux_1_check_privacy_docker import _solve

T06 = "06-grep-log"
T05 = "05-find-file"
WANT = "/home/student/archive/2023/spring/misc/secret-recipe.txt"
DECOY = "/home/student/archive/2023/summer/secret-notes.txt"
TRUNCATION_HINT = "far longer"  # the wording 06 uses for a cut file


def _as_student_script(machine: str, script: str) -> None:
    res = _docker(
        "exec", "-i", machine, "su", "-", "student", "-c", "sh -s",
        check=False, timeout=60, stdin="set -e\n" + script,
    )
    assert res.returncode == 0, f"could not build the file: {res.stdout}\n{res.stderr}"


def _lines(machine: str, path: str) -> int:
    out = _docker("exec", "-u", "root", machine, "wc", "-l", path).stdout
    return int(out.split()[0])


def _errors_copies(machine: str, copies: int, tail: str = "") -> None:
    """~/errors.txt = the 4 right ERROR lines `copies` times, then `tail` (shell commands)."""
    _as_student_script(
        machine,
        "grep ERROR ~/logs/app.log > /tmp/seed325; : > ~/errors.txt; i=0\n"
        f"while [ $i -lt {copies} ]; do cat /tmp/seed325 >> ~/errors.txt; i=$((i+1)); done\n"
        f"{tail}rm -f /tmp/seed325\n",
    )


# ------------------------------------------------------------ 1 and 2. 06 boundary (docker)


@pytest.mark.docker
def test_06_a_201st_correct_line_counts_as_truncation(machine):
    _setup(machine, T06)
    _errors_copies(
        machine, 50, tail="echo '2025-11-14 09:04:09 ERROR retry failed' >> ~/errors.txt\n"
    )
    assert _lines(machine, "/home/student/errors.txt") == 201
    res, parsed = _check(machine, T06)
    assert res.returncode != 0 and not parsed.passed, res.stdout
    assert not parsed.items[3].passed, res.stdout
    assert TRUNCATION_HINT in parsed.items[3].hint, (
        f"a 201-line file must be reported as cut, not as something else:\n{res.stdout}"
    )


@pytest.mark.docker
def test_06_200_right_lines_a_blank_line_201_and_junk_on_202_fails_item_3(machine):
    _setup(machine, T06)
    _errors_copies(
        machine,
        50,  # lines 1..200, all right
        tail="echo '' >> ~/errors.txt\necho 'this is not an error line' >> ~/errors.txt\n",
    )
    assert _lines(machine, "/home/student/errors.txt") == 202
    res, parsed = _check(machine, T06)
    assert res.returncode != 0 and not parsed.passed, (
        f"junk behind a blank line 201 passed:\n{res.stdout}"
    )
    assert not parsed.items[3].passed, f"item 3 must fail:\n{res.stdout}"
    assert TRUNCATION_HINT in parsed.items[3].hint, (
        f"the failure must come from the truncation fact, not the duplicates rule:\n{res.stdout}"
    )


# ------------------------------------------------------------ 3. 06 duplicates (docker)


@pytest.mark.docker
def test_06_duplicated_correct_lines_under_the_cap_fail_item_3_as_duplicates(machine):
    _setup(machine, T06)
    _errors_copies(machine, 2)  # 8 lines: every right line twice, nothing cut
    res, parsed = _check(machine, T06)
    assert res.returncode != 0 and not parsed.passed, (
        f"05 fails duplicated lines; 06 must too:\n{res.stdout}"
    )
    assert not parsed.items[3].passed, res.stdout
    assert parsed.items[3].hint, res.stdout
    assert TRUNCATION_HINT not in parsed.items[3].hint, (
        f"a short file with duplicates is not a cut file:\n{res.stdout}"
    )


@pytest.mark.docker
def test_06_a_long_file_of_40_copies_is_not_pinned_as_correct(machine):
    _setup(machine, T06)
    _errors_copies(machine, 40)  # 160 lines, under the cap, all duplicates
    assert _lines(machine, "/home/student/errors.txt") < 200
    res, parsed = _check(machine, T06)
    assert res.returncode != 0 and not parsed.passed, res.stdout


@pytest.mark.docker
def test_06_the_four_right_lines_once_each_in_another_order_pass(machine):
    _setup(machine, T06)
    _as_student_script(machine, "grep ERROR ~/logs/app.log | sort -r > ~/errors.txt\n")
    assert _lines(machine, "/home/student/errors.txt") == 4
    res, parsed = _check(machine, T06)
    assert res.returncode == 0 and parsed.passed, res.stdout


# ------------------------------------------------------------ 4. 05 truncation (docker)


@pytest.mark.docker
def test_05_the_right_path_200_blank_lines_then_a_decoy_on_line_202_fails_item_2(machine):
    _setup(machine, T05)
    _as_student_script(
        machine,
        f"echo '{WANT}' > ~/found.txt\n"
        "i=0; while [ $i -lt 200 ]; do echo >> ~/found.txt; i=$((i+1)); done\n"
        f"echo '{DECOY}' >> ~/found.txt\n",
    )
    assert _lines(machine, "/home/student/found.txt") == 202
    res, parsed = _check(machine, T05)
    assert res.returncode != 0 and not parsed.passed, (
        f"a decoy path behind 200 blank lines passed:\n{res.stdout}"
    )
    assert not parsed.items[2].passed, f"item 2 must fail:\n{res.stdout}"
    assert parsed.items[2].hint, res.stdout


@pytest.mark.docker
def test_05_the_right_path_and_199_blank_lines_exactly_at_the_cap_still_passes(machine):
    _setup(machine, T05)
    _as_student_script(
        machine,
        f"echo '{WANT}' > ~/found.txt\n"
        "i=0; while [ $i -lt 199 ]; do echo >> ~/found.txt; i=$((i+1)); done\n",
    )
    assert _lines(machine, "/home/student/found.txt") == 200
    res, parsed = _check(machine, T05)
    assert parsed.items[2].passed, f"a file of exactly 200 lines is not cut:\n{res.stdout}"


@pytest.mark.docker
def test_05_the_correct_answer_still_passes(machine):
    _setup(machine, T05)
    _solve(machine, T05)
    res, parsed = _check(machine, T05)
    assert res.returncode == 0 and parsed.passed, res.stdout
