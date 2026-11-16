"""Acceptance tests for issue #119: Linux 1 checks keep working when the student's home is private.

Why: root in the lab container has no DAC_READ_SEARCH / DAC_OVERRIDE (D45), so a check.sh that
tests existence or modes as root (`[ -f ]`, `[ -d ]`, `stat`) cannot walk through a folder that
others may not enter. A student who runs `chmod 700 ~` (a fair reading of task 07, "nobody else
may touch it") or `chmod 700 ~/notes` then got false FAILs with wrong hints, and the home mode
carries over to every later task. check.sh must give the same verdict, with the same hints, as
on a machine with an open home.

Covered here (the harness, fixtures and helpers are those of the #12 and #106 exams):

  * `chmod 700 ~` plus the right answer passes every hands-on task (02 to 09: 02, 03, 04, 05,
    06, 07, 09), the mode set before and after the answer, and also across the whole lab in
    sequence on one machine (the home mode carries over);
  * `chmod 700 ~/notes` plus the right answer passes task 03; the same for the 04 folders;
  * near-misses on a private home keep the hints they have on an open home;
  * the smaller gaps named in the issue and its review comment:
      - 05, `find ... >> ~/found.txt` run twice: found.txt holds the right path twice. Rule
        pinned here: item 2 FAILS and its hint mentions "duplicate" (case-insensitive), and it
        is not the "search for the exact name" hint, which blames the search pattern.
      - 05, `find . -name secret-recipe.txt` run in the home folder prints
        `./archive/2023/spring/misc/secret-recipe.txt`. The task asks for the full path, "it
        starts with /home/student", so this is a FAIL of item 2 with the existing full-path hint
        ("does not hold the full path"), not a PASS and not the "more than one path" hint. Item 1
        (the file exists) passes.
      - 04 item 3 checks the content of ~/pictures/photo.txt (a different file or an empty one
        at that place is not a moved photo); the hint names photo.txt and ~/pictures;
      - 05 `find / -name ... 2>/dev/null` and 06 `grep -i error` (right answers) pass;
      - 07 `chmod 300 ~/scripts/hello.sh` (x without r) gets the x-without-r hint;
      - 06 lowercase `grep error` that finds nothing, written `|| [ $? -eq 1 ]` so only grep's
        no-match exit is tolerated (the strict form of the #106 case, which is not edited).

No content or app code is touched by this exam. Docker tests skip only when Docker is absent.
"""
# ruff: noqa: F811
import pytest
from test_issue_12_linux_1 import (  # noqa: F401
    HANDS_ON_IDS,
    _as_student,
    _check,
    _docker,
    _n_items,
    _setup,
    _task_dir,
    docker_available,
    image,
    machine,
    runtime_flag,
)

P = "PASS"
FULL = "does not hold the full path"
MORE_THAN_ONE = "more than one path"
EXACT_NAME = "search for the exact name"
NOT_ERR = "empty or has lines that are not ERROR"
ALL4 = "misses 4 of the 4 ERROR lines"
FIRST = "first line does not contain the word linux"
FEWER = "fewer than two lines"
X_NO_R = "has x on ~/scripts/hello.sh but not r"
OTHERS = "other people can still access"
NOT_TEAM = "group of team-notes.txt is not team"
PRIVATE_HOME = "chmod 700 ~"


def _student(machine: str, commands: str):
    """Run commands as `student` in a login shell, stopping at the first failing command."""
    res = _docker(
        "exec", "-i", machine, "su", "-", "student", "-c", "sh -s",
        check=False, timeout=120, stdin="set -e\n" + commands + "\n",
    )
    assert res.returncode == 0, f"the student commands failed: {res.stdout}\n{res.stderr}"
    return res


def _solve(machine: str, task_id: str):
    res = _as_student(machine, _task_dir(task_id) / "solution.sh")
    assert res.returncode == 0, f"solution.sh of {task_id} failed: {res.stdout}\n{res.stderr}"


def _assert_verdict(machine: str, task_id: str, expected: dict):
    """expected: {item: "PASS" | fragment of the hint}; every item of the task must be named."""
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert set(expected) == set(range(1, n + 1)), "the case must say what every item shows"
    assert set(parsed.items) == set(range(1, n + 1)), (
        f"expected one PASS/FAIL line per item 1..{n}:\n{res.stdout}"
    )
    if any(want != P for want in expected.values()):
        assert res.returncode != 0 and not parsed.passed, (
            f"{task_id}: check.sh accepted a wrong answer:\n{res.stdout}"
        )
    else:
        assert res.returncode == 0 and parsed.passed, f"{task_id}: wrongly failed:\n{res.stdout}"
    for i, want in sorted(expected.items()):
        item = parsed.items[i]
        if want == P:
            assert item.passed, f"item {i} should PASS:\n{res.stdout}"
        else:
            assert not item.passed, f"item {i} should FAIL:\n{res.stdout}"
            assert item.hint, f"FAIL {i} has no hint:\n{res.stdout}"
            assert want in item.hint, (
                f"FAIL {i} hint should mention {want!r}, got {item.hint!r}:\n{res.stdout}"
            )
    return res, parsed


# ---------------------------------------------------------------- criterion 2: private home


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_private_home_set_before_the_right_answer_passes_every_item(machine, task_id):
    _setup(machine, task_id)
    _student(machine, PRIVATE_HOME)
    _solve(machine, task_id)
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert set(parsed.items) == set(range(1, n + 1)), res.stdout
    assert all(it.passed for it in parsed.items.values()), res.stdout
    assert "FAIL" not in res.stdout
    assert res.returncode == 0 and parsed.passed, res.stdout


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_private_home_set_after_the_right_answer_passes_every_item(machine, task_id):
    _setup(machine, task_id)
    _solve(machine, task_id)
    _student(machine, PRIVATE_HOME)
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert set(parsed.items) == set(range(1, n + 1)), res.stdout
    assert all(it.passed for it in parsed.items.values()), res.stdout
    assert "FAIL" not in res.stdout
    assert res.returncode == 0 and parsed.passed, res.stdout


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_private_home_and_nothing_done_still_fails_with_a_hint_per_failing_item(machine, task_id):
    _setup(machine, task_id)
    _student(machine, PRIVATE_HOME)
    res, parsed = _check(machine, task_id)
    assert res.returncode != 0 and not parsed.passed, res.stdout
    assert set(parsed.items) == set(range(1, _n_items(task_id) + 1)), res.stdout
    failing = [i for i, it in parsed.items.items() if not it.passed]
    assert failing, res.stdout
    for i in failing:
        assert parsed.items[i].hint, f"FAIL {i} has no hint:\n{res.stdout}"


@pytest.mark.docker
def test_private_home_carries_over_the_whole_lab_in_sequence(machine):
    """One machine, home made private once, then every task: setup, right answer, check."""
    _setup(machine, HANDS_ON_IDS[0])
    _student(machine, PRIVATE_HOME)
    for task_id in HANDS_ON_IDS:
        _setup(machine, task_id)
        _solve(machine, task_id)
        res, parsed = _check(machine, task_id)
        assert res.returncode == 0 and parsed.passed, f"{task_id} on a private home:\n{res.stdout}"
        assert "FAIL" not in res.stdout, res.stdout


@pytest.mark.docker
def test_private_notes_folder_chmod_700_and_the_right_answer_passes_task_03(machine):
    task_id = "03-create-file"
    _setup(machine, task_id)
    _solve(machine, task_id)
    _student(machine, "chmod 700 ~/notes")
    res, parsed = _check(machine, task_id)
    assert set(parsed.items) == {1, 2, 3}, res.stdout
    assert res.returncode == 0 and parsed.passed, res.stdout
    assert "FAIL" not in res.stdout


@pytest.mark.docker
def test_private_notes_folder_and_private_home_together_pass_task_03(machine):
    task_id = "03-create-file"
    _setup(machine, task_id)
    _solve(machine, task_id)
    _student(machine, "chmod 700 ~/notes; chmod 700 ~")
    res, parsed = _check(machine, task_id)
    assert res.returncode == 0 and parsed.passed, res.stdout


@pytest.mark.docker
def test_private_inbox_backup_and_pictures_folders_still_pass_task_04(machine):
    task_id = "04-copy-move"
    _setup(machine, task_id)
    _solve(machine, task_id)
    _student(machine, "chmod 700 ~/inbox ~/backup ~/pictures")
    res, parsed = _check(machine, task_id)
    assert set(parsed.items) == {1, 2, 3}, res.stdout
    assert res.returncode == 0 and parsed.passed, res.stdout


# (task, case name, student commands, {item: "PASS" | fragment of the expected hint})
# Near-misses on a private home keep the verdicts and hints they have on an open home (#106).
PRIVATE_HOME_NEAR_MISSES = [
    ("03-create-file", "empty_file_from_touch",
     "touch ~/notes/todo.txt",
     {1: P, 2: FIRST, 3: FEWER}),
    ("03-create-file", "file_in_home_not_in_notes",
     'echo "learn linux" > ~/todo.txt; echo "practice" >> ~/todo.txt',
     {1: "does not exist", 2: FIRST, 3: FEWER}),
    ("04-copy-move", "only_the_copy_done",
     "cp ~/inbox/report.txt ~/backup/",
     {1: P, 2: P, 3: "photo.txt must end up in ~/pictures"}),
    ("04-copy-move", "both_files_moved_report_not_copied",
     "mv ~/inbox/report.txt ~/backup/; mv ~/inbox/photo.txt ~/pictures/",
     {1: P, 2: "report.txt is gone from ~/inbox", 3: P}),
    ("05-find-file", "relative_path_instead_of_full_path",
     "echo archive/2023/spring/misc/secret-recipe.txt > ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "find_with_secret_wildcard_lists_two_files",
     "find ~ -name 'secret*' > ~/found.txt",
     {1: P, 2: MORE_THAN_ONE}),
    ("06-grep-log", "whole_log_copied",
     "cp ~/logs/app.log ~/errors.txt",
     {1: P, 2: P, 3: NOT_ERR}),
    ("06-grep-log", "file_saved_inside_logs_folder",
     "grep ERROR ~/logs/app.log > ~/logs/errors.txt",
     {1: "does not exist", 2: ALL4, 3: NOT_ERR}),
    ("07-chmod", "only_the_script_made_executable",
     "chmod +x ~/scripts/hello.sh",
     {1: P, 2: OTHERS, 3: P}),
    ("09-chgrp", "nothing_changed_group_still_student",
     "true",
     {1: P, 2: NOT_TEAM, 3: P}),
]


@pytest.mark.docker
@pytest.mark.parametrize("case", PRIVATE_HOME_NEAR_MISSES, ids=lambda c: f"{c[0]}-{c[1]}")
def test_near_miss_on_a_private_home_gets_the_same_verdict_and_hints(machine, case):
    task_id, _name, commands, expected = case
    _setup(machine, task_id)
    _student(machine, commands)
    _student(machine, PRIVATE_HOME)
    _assert_verdict(machine, task_id, expected)


# ---------------------------------------------------------------- criterion 3: smaller gaps


@pytest.mark.docker
def test_05_find_appended_twice_gets_a_duplicates_hint_not_the_exact_name_hint(machine):
    _setup(machine, "05-find-file")
    _student(
        machine,
        "find ~ -name secret-recipe.txt >> ~/found.txt\n"
        "find ~ -name secret-recipe.txt >> ~/found.txt",
    )
    res, parsed = _check(machine, "05-find-file")
    assert set(parsed.items) == {1, 2}, res.stdout
    assert res.returncode != 0 and not parsed.passed, res.stdout
    assert parsed.items[1].passed, res.stdout
    item = parsed.items[2]
    assert not item.passed and item.hint, res.stdout
    assert "duplicate" in item.hint.lower(), (
        f"the hint must say the path is in found.txt twice (the word 'duplicate'): {item.hint!r}"
    )
    assert EXACT_NAME not in item.hint, (
        f"the exact-name hint is wrong here, the search was exact: {item.hint!r}"
    )


@pytest.mark.docker
def test_05_find_appended_once_to_a_fresh_file_passes(machine):
    _setup(machine, "05-find-file")
    _student(machine, "find ~ -name secret-recipe.txt >> ~/found.txt")
    _assert_verdict(machine, "05-find-file", {1: P, 2: P})


@pytest.mark.docker
def test_05_find_from_dot_prints_a_relative_path_and_fails_the_full_path_item(machine):
    _setup(machine, "05-find-file")
    _student(machine, "cd ~; find . -name secret-recipe.txt > ~/found.txt")
    res, _parsed = _assert_verdict(machine, "05-find-file", {1: P, 2: FULL})
    assert MORE_THAN_ONE not in res.stdout and EXACT_NAME not in res.stdout, res.stdout


@pytest.mark.docker
def test_05_find_from_the_root_folder_with_errors_hidden_passes(machine):
    _setup(machine, "05-find-file")
    _student(machine, "find / -name secret-recipe.txt 2>/dev/null > ~/found.txt || [ $? -eq 1 ]")
    _assert_verdict(machine, "05-find-file", {1: P, 2: P})


@pytest.mark.docker
def test_05_find_from_the_root_folder_with_errors_hidden_passes_on_a_private_home(machine):
    _setup(machine, "05-find-file")
    _student(
        machine,
        "find / -name secret-recipe.txt 2>/dev/null > ~/found.txt || [ $? -eq 1 ]; " + PRIVATE_HOME,
    )
    _assert_verdict(machine, "05-find-file", {1: P, 2: P})


@pytest.mark.docker
def test_04_item3_wrong_content_in_pictures_photo_txt_fails(machine):
    _setup(machine, "04-copy-move")
    _student(
        machine,
        "cp ~/inbox/report.txt ~/backup/; rm ~/inbox/photo.txt; "
        "echo 'not the photo' > ~/pictures/photo.txt",
    )
    res, parsed = _assert_verdict(machine, "04-copy-move", {1: P, 2: P, 3: "photo.txt"})
    assert "~/pictures" in parsed.items[3].hint, res.stdout


@pytest.mark.docker
def test_04_item3_empty_pictures_photo_txt_made_with_touch_fails(machine):
    _setup(machine, "04-copy-move")
    _student(
        machine,
        "cp ~/inbox/report.txt ~/backup/; rm ~/inbox/photo.txt; touch ~/pictures/photo.txt",
    )
    _assert_verdict(machine, "04-copy-move", {1: P, 2: P, 3: "photo.txt"})


@pytest.mark.docker
def test_04_item3_wrong_content_fails_also_on_a_private_home(machine):
    _setup(machine, "04-copy-move")
    _student(
        machine,
        "cp ~/inbox/report.txt ~/backup/; rm ~/inbox/photo.txt; "
        "echo 'not the photo' > ~/pictures/photo.txt; " + PRIVATE_HOME,
    )
    _assert_verdict(machine, "04-copy-move", {1: P, 2: P, 3: "photo.txt"})


@pytest.mark.docker
def test_04_item3_moved_photo_with_chmod_600_still_passes(machine):
    _setup(machine, "04-copy-move")
    _student(
        machine,
        "cp ~/inbox/report.txt ~/backup/; mv ~/inbox/photo.txt ~/pictures/; "
        "chmod 600 ~/pictures/photo.txt",
    )
    _assert_verdict(machine, "04-copy-move", {1: P, 2: P, 3: P})


@pytest.mark.docker
@pytest.mark.parametrize(
    "grep_cmd", ["grep -i error", "grep -i ERROR"], ids=["grep_i_lowercase", "grep_i_uppercase"]
)
def test_06_case_insensitive_grep_is_a_right_answer(machine, grep_cmd):
    _setup(machine, "06-grep-log")
    _student(machine, f"{grep_cmd} ~/logs/app.log > ~/errors.txt")
    _assert_verdict(machine, "06-grep-log", {1: P, 2: P, 3: P})


@pytest.mark.docker
def test_06_lowercase_grep_matches_nothing_strict_no_match_exit_only(machine):
    """The #106 case `lowercase_pattern_matches_nothing`, tolerating only grep's exit 1."""
    _setup(machine, "06-grep-log")
    _student(machine, "grep error ~/logs/app.log > ~/errors.txt || [ $? -eq 1 ]")
    _assert_verdict(machine, "06-grep-log", {1: P, 2: ALL4, 3: NOT_ERR})


@pytest.mark.docker
def test_07_execute_for_the_owner_without_read_300_gets_the_x_without_r_hint(machine):
    _setup(machine, "07-chmod")
    _student(machine, "chmod 300 ~/scripts/hello.sh; chmod 600 ~/secret.txt")
    _assert_verdict(machine, "07-chmod", {1: X_NO_R, 2: P, 3: P})


@pytest.mark.docker
def test_07_execute_for_the_owner_without_read_300_on_a_private_home(machine):
    _setup(machine, "07-chmod")
    _student(machine, "chmod 300 ~/scripts/hello.sh; chmod 600 ~/secret.txt; " + PRIVATE_HOME)
    _assert_verdict(machine, "07-chmod", {1: X_NO_R, 2: P, 3: P})
