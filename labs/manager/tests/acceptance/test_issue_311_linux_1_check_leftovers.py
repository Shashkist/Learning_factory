"""Acceptance tests for issue #311: Linux 1 check leftovers of #126 (items 1, 3, 4, 5).

Item 2 (forged fact lines) is accepted risk by D101 and is not tested here.

  1. Task 06: the student-side reader reads at most 201 lines. An errors.txt that holds the 4
     right ERROR lines repeated past that cap and then junk used to pass items 2 and 3, because
     root never saw the junk. Root must now fail item 3 for it. The correct answer must keep
     passing (a long file of the 4 lines repeated is duplicates, #325, and fails). Docker cases
     (CI, runsc).
  3. Cleanup: a check.sh that defines the reader `c()` or the root-side `rd` / `body` helpers
     must call them. Pinned by text on every hands-on task (02, 07 and 09 define them unused
     today). Plain pytest, runs anywhere.
  4. The #126 /proc sampler runs a wrong-answer state per task too (cases taken from the #106
     near-miss table), so the fail branches of every check are sampled for decision logic on a
     command line, not only the untouched and solved states. Docker cases.
  5. `fresh_box` stays in the #126 exam: the #306 exam imports it, so there are only two
     bodies (#9 `box` and #126 `fresh_box`), no third copy to share. Nothing to test.

Students are fake. Docker tests skip only when Docker is absent.
"""
# ruff: noqa: F811
import re
import time

import pytest
from test_issue_12_linux_1 import (  # noqa: F401  (fixtures are used by name)
    HANDS_ON_IDS,
    _check,
    _docker,
    _setup,
    _task_dir,
    _text,
    docker_available,
    image,
    machine,
    runtime_flag,
)
from test_issue_106_linux_1_wrong_answers import CASES, _as_student_login
from test_issue_126_linux_1_check_privacy_docker import (
    ANSWERS,
    CANARY,
    CHECKS,
    SAMPLER,
    _detached_as_student,
    _hint_fragments,
    _read_hits,
    _solve,
    _stop_marker,
)

TASK = "06-grep-log"
CAP = 200  # lines root accepts; the student-side reader of 06 reads one more (201)


# ------------------------------------------------------------ 1. 06 truncation (docker)


def _write_errors(machine: str, repeats: int, junk: bool) -> int:
    """~/errors.txt = the 4 right ERROR lines `repeats` times, then (optionally) junk lines."""
    script = (
        "grep ERROR ~/logs/app.log > /tmp/seed311; : > ~/errors.txt; i=0\n"
        f"while [ $i -lt {repeats} ]; do cat /tmp/seed311 >> ~/errors.txt; i=$((i+1)); done\n"
        + (
            "echo 'this is not an error line' >> ~/errors.txt\n"
            "echo 'neither is this one' >> ~/errors.txt\n"
            if junk
            else ""
        )
        + "rm -f /tmp/seed311\n"
    )
    res = _docker(
        "exec", "-i", machine, "su", "-", "student", "-c", "sh -s",
        check=False, timeout=60, stdin="set -e\n" + script,
    )
    assert res.returncode == 0, f"could not build errors.txt: {res.stdout}\n{res.stderr}"
    n = _docker("exec", "-u", "root", machine, "wc", "-l", "/home/student/errors.txt").stdout
    return int(n.split()[0])


@pytest.mark.docker
def test_06_the_right_lines_repeated_past_the_reader_cap_then_junk_does_not_pass(machine):
    _setup(machine, TASK)
    lines = _write_errors(machine, repeats=60, junk=True)  # 242 lines, the junk after line 200
    assert lines > CAP + 10, lines
    res, parsed = _check(machine, TASK)
    assert res.returncode != 0 and not parsed.passed, (
        f"a 06 errors.txt with junk past line {CAP} passed:\n{res.stdout}"
    )
    assert not parsed.items[3].passed, f"item 3 must fail for the cut file:\n{res.stdout}"
    assert parsed.items[3].hint, f"FAIL 3 has no hint:\n{res.stdout}"
    assert "far longer" in parsed.items[3].hint, (
        f"item 3 must give the truncation hint, not the duplicate one (D106):\n{res.stdout}"
    )


@pytest.mark.docker
def test_06_a_file_longer_than_the_reader_cap_is_not_passed_on_its_first_lines(machine):
    # Every line is a right ERROR line, but root saw only the first 200 and cannot vouch for
    # the rest. Truncation is a fact root fails item 3 on.
    _setup(machine, TASK)
    lines = _write_errors(machine, repeats=60, junk=False)  # 240 lines, all ERROR lines
    assert lines > CAP + 10, lines
    res, parsed = _check(machine, TASK)
    assert not parsed.items[3].passed, (
        f"a file longer than the reader cap was passed on its first {CAP} lines:\n{res.stdout}"
    )
    assert "far longer" in parsed.items[3].hint, (
        f"item 3 must give the truncation hint, not the duplicate one (D106):\n{res.stdout}"
    )


@pytest.mark.docker
def test_06_the_correct_answer_still_passes(machine):
    _setup(machine, TASK)
    _solve(machine, TASK)
    res, parsed = _check(machine, TASK)
    assert res.returncode == 0 and parsed.passed, res.stdout
    assert set(parsed.items) == {1, 2, 3}, res.stdout


# test_06_a_long_file_under_the_cap_still_passes is gone (#325): 06 fails duplicates, so 40 copies
# of the 4 ERROR lines are not a correct file. See test_issue_325_one_read_truncation.py.


# ------------------------------------------------------------ 3. unused helpers (text)


@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_a_check_does_not_define_helpers_it_never_calls(task_id):
    text = _text(_task_dir(task_id) / "check.sh")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    # (helper, how its definition starts a line, how a call looks)
    helpers = [
        ("c", r"^c\(\)", r"^c \w+ /"),  # reader call: `c key /home/student/...`
        ("rd", r"^rd\(\)", r"(^|[\s;&|!(])rd \w+"),
        ("body", r"^body\(\)", r"(^|[\s;&|!($(])body \w+"),
    ]
    for name, defined, called in helpers:
        if any(re.match(defined, ln) for ln in code):
            uses = [
                ln for ln in code if re.search(called, ln) and not re.match(defined, ln)
            ]
            assert uses, f"{task_id}: check.sh defines {name}() but never calls it"


# ------------------------------------------------------------ 4. sampler on wrong answers

SAMPLED = {
    "02-directory-tree": ["css_only_images_missing", "tree_built_outside_projects"],
    "03-create-file": [
        "word_on_the_second_line_only", "right_answer_but_chmod_000_is_unreadable",
    ],
    "04-copy-move": [
        "both_files_moved_report_not_copied", "empty_file_instead_of_a_copy",
        "copy_made_unreadable_with_chmod_000",
    ],
    "05-find-file": [
        "relative_path_instead_of_full_path", "right_path_plus_a_decoy_line",
        "right_answer_but_chmod_000_is_unreadable",
    ],
    "06-grep-log": [
        "whole_log_copied", "only_the_first_two_errors",
        "right_answer_but_chmod_000_is_unreadable",
    ],
    "07-chmod": [
        "only_the_script_made_executable", "secret_made_read_only_400_owner_cannot_write",
        "execute_only_100_cannot_actually_run_a_script",
    ],
    "09-chgrp": ["nothing_changed_group_still_student", "file_deleted_instead_of_changed"],
}
SAMPLED_CASES = [c for c in CASES if c[1] in SAMPLED.get(c[0], [])]


def test_the_sampled_wrong_answer_states_exist_in_the_106_table_for_every_task():
    assert set(SAMPLED) == set(HANDS_ON_IDS)
    found = {(c[0], c[1]) for c in SAMPLED_CASES}
    for task_id, names in SAMPLED.items():
        for name in names:
            assert (task_id, name) in found, f"{task_id}/{name} is not a #106 case"


@pytest.mark.docker
@pytest.mark.parametrize("case", SAMPLED_CASES, ids=lambda c: f"{c[0]}-{c[1]}")
def test_no_process_command_line_shows_the_decision_logic_in_a_wrong_answer_state(machine, case):
    task_id, name, commands, expected = case
    needles = sorted(_hint_fragments() | {a for lst in ANSWERS.values() for a in lst} | {CANARY})
    assert all(n.strip() for n in needles), "an empty needle would match every command line"
    _docker(
        "exec", "-i", "-u", "student", machine, "sh", "-c", "cat > /tmp/needles126",
        stdin="\n".join(needles) + "\n",
    )
    _setup(machine, task_id)
    # The near-miss is applied before the sampler starts: the student's own commands are no noise.
    done = _as_student_login(machine, commands)
    assert done.returncode == 0, f"the student commands failed: {done.stdout}\n{done.stderr}"
    _detached_as_student(machine, SAMPLER)
    time.sleep(1.0)
    _docker(
        "exec", "-u", "student", machine, "sh", "-c",
        f"i=0; while [ $i -lt 24 ]; do grep -qF {CANARY} /dev/null; i=$((i+1)); done",
        timeout=120,
    )
    time.sleep(0.5)
    assert any(CANARY in h for h in _read_hits(machine)), (
        "the /proc sampler did not see a short-lived student process; it cannot vouch for a check"
    )

    res = None
    for _ in range(CHECKS):
        res, _parsed = _check(machine, task_id)
    wrong = [i for i, want in expected.items() if want != "PASS"]
    assert bool(wrong) == (res.returncode != 0), (
        f"the state is not the one the case names:\n{res.stdout}"
    )

    _stop_marker(machine, "/tmp/stop-sampler126")
    time.sleep(1.5)
    leaked = sorted({h for h in _read_hits(machine) if h != CANARY})
    assert not leaked, f"{task_id}/{name}: decision logic visible in /proc cmdline: {leaked}"
