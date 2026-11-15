"""Acceptance tests for issue #106: Linux 1 exam, wrong-answer cases for every hands-on task.

The #12 exam checks a fresh machine (every task fails) and a solved machine (every task
passes). It never checks a plausible wrong answer, which is how the round 1 content bugs of
PR #104 passed it. Here the student does something close but wrong; check.sh must then

  * not report the task as passed (exit code != 0, `passed` false),
  * print `FAIL <n> <hint>` for exactly the checklist items that are wrong, with the hint
    naming the real problem (a fragment of the hint text is pinned),
  * keep printing `PASS <n>` for the items the near-miss did satisfy.

Each case runs on a fresh machine (the `machine` fixture of the #12 exam), runs only the task's
setup.sh, applies the near-miss as `student` through a login shell, then runs check.sh as root.

"Unreadable file" cases: in production root has no DAC_OVERRIDE, so check.sh must read the
student's file as the student. `chmod 600` (owner only) is therefore still a correct answer
and must keep passing (regression of the round 1 bug); `chmod 000` makes the file unreadable
even for its owner and must produce the "cannot be read" hint.

No content or app code is touched by this exam. Docker tests skip only when Docker is absent.
"""
# ruff: noqa: F811
import pytest

# Re-use the #12 helpers and fixtures; do not duplicate them.
from test_issue_12_linux_1 import (  # noqa: F401
    HANDS_ON_IDS,
    _check,
    _docker,
    _n_items,
    _setup,
    docker_available,
    image,
    machine,
    runtime_flag,
)

P = "PASS"
RIGHT_03 = 'echo "learn linux" > ~/notes/todo.txt; echo "practice" >> ~/notes/todo.txt'
RIGHT_05 = "find ~ -name secret-recipe.txt > ~/found.txt"
RIGHT_06 = "grep ERROR ~/logs/app.log > ~/errors.txt"
FULL = "does not hold the full path"
NOT_ERR = "empty or has lines that are not ERROR"
ALL4 = "misses 4 of the 4 ERROR lines"
FIRST = "first line does not contain the word linux"
FEWER = "fewer than two lines"
NO_X = "x permission is missing"
OTHERS = "other people can still access"
NOT_TEAM = "group of team-notes.txt is not team"
SECRET_PATH = "/home/student/archive/2023/spring/misc/secret-recipe.txt"

# (task, case name, student commands, {item: "PASS" | fragment of the expected hint})
CASES = [
    # ---------------------------------------------------------------- 02-directory-tree
    ("02-directory-tree", "website_only_no_subfolders",
     "mkdir -p ~/projects/website",
     {1: P, 2: "no folder css", 3: "no folder images"}),
    ("02-directory-tree", "css_only_images_missing",
     "mkdir -p ~/projects/website/css",
     {1: P, 2: P, 3: "no folder images"}),
    ("02-directory-tree", "images_only_css_missing",
     "mkdir -p ~/projects/website/images",
     {1: P, 2: "no folder css", 3: P}),
    ("02-directory-tree", "css_and_images_next_to_website_not_inside",
     "mkdir -p ~/projects/website ~/projects/css ~/projects/images",
     {1: P, 2: "no folder css", 3: "no folder images"}),
    ("02-directory-tree", "singular_images_typo",
     "mkdir -p ~/projects/website/css ~/projects/website/image",
     {1: P, 2: P, 3: "no folder images"}),
    ("02-directory-tree", "capitalised_css",
     "mkdir -p ~/projects/website/CSS ~/projects/website/images",
     {1: P, 2: "no folder css", 3: P}),
    ("02-directory-tree", "tree_built_outside_projects",
     "mkdir -p ~/website/css ~/website/images",
     {1: "no folder ~/projects/website", 2: "no folder css", 3: "no folder images"}),
    ("02-directory-tree", "website_is_a_file_not_a_folder",
     "touch ~/projects/website",
     {1: "no folder ~/projects/website", 2: "no folder css", 3: "no folder images"}),

    # ---------------------------------------------------------------- 03-create-file
    ("03-create-file", "word_on_the_second_line_only",
     'echo "my list" > ~/notes/todo.txt; echo "learn linux" >> ~/notes/todo.txt',
     {1: P, 2: FIRST, 3: P}),
    ("03-create-file", "word_on_a_later_line_after_a_blank_first_line",
     'printf "\\nlinux\\nbasics\\n" > ~/notes/todo.txt',
     {1: P, 2: FIRST, 3: P}),
    ("03-create-file", "single_line_with_the_word",
     'echo "learn linux" > ~/notes/todo.txt',
     {1: P, 2: P, 3: FEWER}),
    ("03-create-file", "second_echo_used_gt_and_replaced_the_first_line",
     'echo "learn linux" > ~/notes/todo.txt; echo "practice" > ~/notes/todo.txt',
     {1: P, 2: FIRST, 3: FEWER}),
    ("03-create-file", "empty_file_from_touch",
     "touch ~/notes/todo.txt",
     {1: P, 2: FIRST, 3: FEWER}),
    ("03-create-file", "two_lines_without_the_word",
     'echo "buy milk" > ~/notes/todo.txt; echo "call ana" >> ~/notes/todo.txt',
     {1: P, 2: FIRST, 3: P}),
    ("03-create-file", "file_in_home_not_in_notes",
     'echo "learn linux" > ~/todo.txt; echo "practice" >> ~/todo.txt',
     {1: "does not exist", 2: FIRST, 3: FEWER}),
    ("03-create-file", "wrong_file_name_todo_text",
     'echo "learn linux" > ~/notes/todo.text; echo "practice" >> ~/notes/todo.text',
     {1: "does not exist", 2: FIRST, 3: FEWER}),
    ("03-create-file", "right_answer_but_chmod_600_still_passes",
     RIGHT_03 + "; chmod 600 ~/notes/todo.txt",
     {1: P, 2: P, 3: P}),
    ("03-create-file", "right_answer_but_chmod_000_is_unreadable",
     RIGHT_03 + "; chmod 000 ~/notes/todo.txt",
     {1: P, 2: "cannot be read", 3: "cannot be read"}),

    # ---------------------------------------------------------------- 04-copy-move
    ("04-copy-move", "both_files_moved_report_not_copied",
     "mv ~/inbox/report.txt ~/backup/; mv ~/inbox/photo.txt ~/pictures/",
     {1: P, 2: "report.txt is gone from ~/inbox", 3: P}),
    ("04-copy-move", "both_files_copied_photo_not_moved",
     "cp ~/inbox/report.txt ~/backup/; cp ~/inbox/photo.txt ~/pictures/",
     {1: P, 2: P, 3: "photo.txt must end up in ~/pictures and no longer be in ~/inbox"}),
    ("04-copy-move", "only_the_copy_done",
     "cp ~/inbox/report.txt ~/backup/",
     {1: P, 2: P, 3: "photo.txt must end up in ~/pictures"}),
    ("04-copy-move", "only_the_move_done",
     "mv ~/inbox/photo.txt ~/pictures/",
     {1: "no copy of report.txt in ~/backup", 2: P, 3: P}),
    ("04-copy-move", "photo_moved_to_backup_instead_of_pictures",
     "cp ~/inbox/report.txt ~/backup/; mv ~/inbox/photo.txt ~/backup/",
     {1: P, 2: P, 3: "photo.txt must end up in ~/pictures"}),
    ("04-copy-move", "photo_moved_to_pictures_under_another_name",
     "cp ~/inbox/report.txt ~/backup/; mv ~/inbox/photo.txt ~/pictures/pic.txt",
     {1: P, 2: P, 3: "photo.txt must end up in ~/pictures"}),
    ("04-copy-move", "report_copied_under_another_name",
     "cp ~/inbox/report.txt ~/backup/report-copy.txt; mv ~/inbox/photo.txt ~/pictures/",
     {1: "no copy of report.txt in ~/backup", 2: P, 3: P}),
    ("04-copy-move", "empty_file_instead_of_a_copy",
     "touch ~/backup/report.txt; mv ~/inbox/photo.txt ~/pictures/",
     {1: "is not a copy of report.txt", 2: P, 3: P}),
    ("04-copy-move", "copy_made_unreadable_with_chmod_000",
     "cp ~/inbox/report.txt ~/backup/; chmod 000 ~/backup/report.txt; "
     "mv ~/inbox/photo.txt ~/pictures/",
     {1: "cannot be read", 2: P, 3: P}),
    ("04-copy-move", "copy_with_chmod_600_still_passes",
     "cp ~/inbox/report.txt ~/backup/; chmod 600 ~/backup/report.txt; "
     "mv ~/inbox/photo.txt ~/pictures/",
     {1: P, 2: P, 3: P}),

    # ---------------------------------------------------------------- 05-find-file
    ("05-find-file", "relative_path_instead_of_full_path",
     "echo archive/2023/spring/misc/secret-recipe.txt > ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "tilde_path_written_literally",
     "echo '~/archive/2023/spring/misc/secret-recipe.txt' > ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "decoy_secret_notes_path",
     "echo /home/student/archive/2023/summer/secret-notes.txt > ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "decoy_recipe_txt_path",
     "echo /home/student/archive/2022/winter/recipe.txt > ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "decoy_old_recipe_path",
     "echo /home/student/archive/old/docs/old-recipe.txt > ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "right_path_plus_a_decoy_line",
     f"echo {SECRET_PATH} > ~/found.txt; "
     "echo /home/student/archive/2023/summer/secret-notes.txt >> ~/found.txt",
     {1: P, 2: "more than one path"}),
    ("05-find-file", "find_with_secret_wildcard_lists_two_files",
     "find ~ -name 'secret*' > ~/found.txt",
     {1: P, 2: "more than one path"}),
    ("05-find-file", "find_with_recipe_wildcard_lists_three_files",
     "find ~ -name '*recipe*' > ~/found.txt",
     {1: P, 2: "more than one path"}),
    ("05-find-file", "directory_of_the_file_instead_of_the_file",
     "echo /home/student/archive/2023/spring/misc > ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "right_path_with_trailing_space",
     f"echo '{SECRET_PATH} ' > ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "empty_found_file",
     "touch ~/found.txt",
     {1: P, 2: FULL}),
    ("05-find-file", "found_file_saved_in_the_wrong_folder",
     "find ~ -name secret-recipe.txt > ~/archive/found.txt",
     {1: "does not exist", 2: FULL}),
    ("05-find-file", "right_answer_but_chmod_600_still_passes",
     RIGHT_05 + "; chmod 600 ~/found.txt",
     {1: P, 2: P}),
    ("05-find-file", "right_answer_but_chmod_000_is_unreadable",
     RIGHT_05 + "; chmod 000 ~/found.txt",
     {1: P, 2: "cannot be read"}),

    # ---------------------------------------------------------------- 06-grep-log
    ("06-grep-log", "lowercase_pattern_matches_nothing",
     "grep error ~/logs/app.log > ~/errors.txt || true",  # no match: grep exits 1
     {1: P, 2: ALL4, 3: NOT_ERR}),
    ("06-grep-log", "whole_log_copied",
     "cp ~/logs/app.log ~/errors.txt",
     {1: P, 2: P, 3: NOT_ERR}),
    ("06-grep-log", "inverted_match_grep_v",
     "grep -v ERROR ~/logs/app.log > ~/errors.txt",
     {1: P, 2: ALL4, 3: NOT_ERR}),
    ("06-grep-log", "only_the_first_two_errors",
     "grep -m 2 ERROR ~/logs/app.log > ~/errors.txt",
     {1: P, 2: "misses 2 of the 4 ERROR lines", 3: P}),
    ("06-grep-log", "only_the_last_error_via_tail",
     "grep ERROR ~/logs/app.log | tail -n 1 > ~/errors.txt",
     {1: P, 2: "misses 3 of the 4 ERROR lines", 3: P}),
    ("06-grep-log", "errors_plus_one_warn_line",
     "grep ERROR ~/logs/app.log > ~/errors.txt; grep WARN ~/logs/app.log >> ~/errors.txt",
     {1: P, 2: P, 3: NOT_ERR}),
    ("06-grep-log", "output_to_the_screen_only_no_file",
     "grep ERROR ~/logs/app.log",
     {1: "does not exist", 2: ALL4, 3: NOT_ERR}),
    ("06-grep-log", "file_saved_inside_logs_folder",
     "grep ERROR ~/logs/app.log > ~/logs/errors.txt",
     {1: "does not exist", 2: ALL4, 3: NOT_ERR}),
    ("06-grep-log", "empty_file_from_touch",
     "touch ~/errors.txt",
     {1: P, 2: ALL4, 3: NOT_ERR}),
    ("06-grep-log", "right_answer_but_chmod_600_still_passes",
     RIGHT_06 + "; chmod 600 ~/errors.txt",
     {1: P, 2: P, 3: P}),
    ("06-grep-log", "right_answer_but_chmod_000_is_unreadable",
     RIGHT_06 + "; chmod 000 ~/errors.txt",
     {1: P, 2: "cannot be read", 3: "cannot be read"}),

    # ---------------------------------------------------------------- 07-chmod
    ("07-chmod", "only_the_script_made_executable",
     "chmod +x ~/scripts/hello.sh",
     {1: P, 2: OTHERS, 3: P}),
    ("07-chmod", "only_the_secret_made_private",
     "chmod 600 ~/secret.txt",
     {1: NO_X, 2: P, 3: P}),
    ("07-chmod", "secret_still_readable_by_group_640",
     "chmod +x ~/scripts/hello.sh; chmod 640 ~/secret.txt",
     {1: P, 2: OTHERS, 3: P}),
    ("07-chmod", "secret_still_readable_by_others_604",
     "chmod +x ~/scripts/hello.sh; chmod 604 ~/secret.txt",
     {1: P, 2: OTHERS, 3: P}),
    ("07-chmod", "secret_group_writable_660",
     "chmod +x ~/scripts/hello.sh; chmod 660 ~/secret.txt",
     {1: P, 2: OTHERS, 3: P}),
    ("07-chmod", "secret_made_read_only_400_owner_cannot_write",
     "chmod +x ~/scripts/hello.sh; chmod 400 ~/secret.txt",
     {1: P, 2: P, 3: "locked yourself out"}),
    ("07-chmod", "secret_made_000_locks_the_owner_out",
     "chmod +x ~/scripts/hello.sh; chmod 000 ~/secret.txt",
     {1: P, 2: P, 3: "locked yourself out"}),
    ("07-chmod", "execute_given_to_group_not_owner",
     "chmod g+x ~/scripts/hello.sh; chmod 600 ~/secret.txt",
     {1: NO_X, 2: P, 3: P}),
    ("07-chmod", "execute_given_to_others_not_owner",
     "chmod o+x ~/scripts/hello.sh; chmod 600 ~/secret.txt",
     {1: NO_X, 2: P, 3: P}),
    ("07-chmod", "script_made_unrunnable_by_removing_all_permissions",
     "chmod 000 ~/scripts/hello.sh; chmod 600 ~/secret.txt",
     {1: NO_X, 2: P, 3: P}),
    ("07-chmod", "execute_added_to_the_wrong_file",
     "chmod +x ~/secret.txt; chmod 600 ~/scripts/hello.sh",
     {1: NO_X, 2: OTHERS, 3: P}),
    ("07-chmod", "secret_given_x_everywhere_755",
     "chmod +x ~/scripts/hello.sh; chmod 755 ~/secret.txt",
     {1: P, 2: OTHERS, 3: P}),
    # Expected to FAIL against the current check.sh: mode 100 leaves the owner with x but no r,
    # and a shell script cannot be run without r, so "the owner can run hello.sh" is false.
    ("07-chmod", "execute_only_100_cannot_actually_run_a_script",
     "chmod 100 ~/scripts/hello.sh; chmod 600 ~/secret.txt",
     {1: NO_X, 2: P, 3: P}),

    # ---------------------------------------------------------------- 09-chgrp
    ("09-chgrp", "nothing_changed_group_still_student",
     "true",
     {1: P, 2: NOT_TEAM, 3: P}),
    ("09-chgrp", "file_deleted_instead_of_changed",
     "rm ~/team-notes.txt",
     {1: "team-notes.txt is missing", 2: NOT_TEAM, 3: "owner of team-notes.txt is not student"}),
    ("09-chgrp", "file_recreated_with_the_default_group",
     "rm ~/team-notes.txt; echo 'meeting at ten' > ~/team-notes.txt",
     {1: P, 2: NOT_TEAM, 3: P}),
    ("09-chgrp", "group_changed_on_a_copy_not_on_the_file",
     "cp ~/team-notes.txt ~/team-notes-2.txt; chgrp team ~/team-notes-2.txt",
     {1: P, 2: NOT_TEAM, 3: P}),
    ("09-chgrp", "group_changed_on_the_home_folder_not_on_the_file",
     "chgrp team ~",
     {1: P, 2: NOT_TEAM, 3: P}),
    ("09-chgrp", "chgrp_to_a_group_the_student_is_not_in_is_refused",
     "chgrp root ~/team-notes.txt 2>/dev/null; true",
     {1: P, 2: NOT_TEAM, 3: P}),
    ("09-chgrp", "chgrp_to_the_primary_group_student_is_not_team",
     "chgrp student ~/team-notes.txt",
     {1: P, 2: NOT_TEAM, 3: P}),
    ("09-chgrp", "team_misspelled_is_refused",
     "chgrp teem ~/team-notes.txt 2>/dev/null; true",
     {1: P, 2: NOT_TEAM, 3: P}),
    ("09-chgrp", "right_answer_plus_chmod_600_still_passes",
     "chgrp team ~/team-notes.txt; chmod 600 ~/team-notes.txt",
     {1: P, 2: P, 3: P}),
]


def _case_id(case):
    return f"{case[0]}-{case[1]}"


def _as_student_login(machine: str, commands: str):
    """Run commands as `student` in a login shell (like the terminal the student types in)."""
    return _docker(
        "exec", "-i", machine, "su", "-", "student", "-c", "sh -s",
        check=False, timeout=60, stdin=commands + "\n",
    )


def test_every_hands_on_task_has_wrong_answer_cases():
    covered = {c[0] for c in CASES}
    assert covered == set(HANDS_ON_IDS)
    for task_id in HANDS_ON_IDS:
        mine = [c for c in CASES if c[0] == task_id]
        assert len(mine) >= 3, f"{task_id} needs several wrong-answer cases, has {len(mine)}"


def test_the_issue_named_cases_are_present():
    names = {(c[0], c[1]) for c in CASES}
    for task_id in ("03-create-file", "06-grep-log"):
        assert (task_id, "right_answer_but_chmod_600_still_passes") in names
        assert (task_id, "right_answer_but_chmod_000_is_unreadable") in names
    assert ("03-create-file", "word_on_a_later_line_after_a_blank_first_line") in names
    assert any(c[0] == "05-find-file" and c[1].startswith("decoy_") for c in CASES)


@pytest.mark.docker
@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_near_miss_gets_the_right_verdict_item_by_item_with_hints(machine, case):
    task_id, _name, commands, expected = case
    _setup(machine, task_id)
    done = _as_student_login(machine, commands)
    assert done.returncode == 0, f"the student commands failed: {done.stdout}\n{done.stderr}"

    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert set(expected) == set(range(1, n + 1)), "the case must say what every item shows"
    assert set(parsed.items) == set(range(1, n + 1)), (
        f"expected one PASS/FAIL line per item 1..{n}:\n{res.stdout}"
    )
    wrong = [i for i, want in expected.items() if want != P]
    if wrong:
        assert res.returncode != 0 and not parsed.passed, (
            f"{task_id}: check.sh accepted a wrong answer:\n{res.stdout}"
        )
    else:  # a right answer that only looks odd (for example chmod 600) must keep passing
        assert res.returncode == 0 and parsed.passed, f"{task_id}: wrongly failed:\n{res.stdout}"
    for i, want in sorted(expected.items()):
        item = parsed.items[i]
        if want == P:
            assert item.passed, f"item {i} should PASS for this near-miss:\n{res.stdout}"
        else:
            assert not item.passed, f"item {i} should FAIL for this near-miss:\n{res.stdout}"
            assert item.hint, f"FAIL {i} has no hint:\n{res.stdout}"
            assert want in item.hint, (
                f"FAIL {i} hint should mention {want!r}, got {item.hint!r}:\n{res.stdout}"
            )
