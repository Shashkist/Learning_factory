import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from exam_integrity import check

EXAM = "labs/manager/tests/acceptance/test_issue_1.py"
CONFTEST = "labs/manager/tests/conftest.py"
CODE = "labs/manager/app/main.py"
UNIT = "labs/manager/tests/unit/test_main.py"
CI = "scripts/ci/exam_integrity.py"


def test_exam_first_then_code_passes():
    assert check([[EXAM], [CODE], [UNIT, CODE]]) == []


def test_no_exam_changes_passes():
    assert check([[CODE], [".github/workflows/ci.yml"]]) == []


def test_same_commit_exam_and_code_fails():
    errors = check([[EXAM, CODE]])
    assert len(errors) == 1 and "commit 0" in errors[0]


def test_exam_edit_after_code_fails():
    errors = check([[CODE], [EXAM]])
    assert len(errors) == 1 and "commit 1" in errors[0]


def test_conftest_is_exam_surface():
    assert len(check([[CODE], [CONFTEST]])) == 1
    assert len(check([["labs/manager/tests/helpers/x.py", CODE]])) == 1


def test_unit_tests_are_free():
    assert check([[EXAM], [CODE], [UNIT]]) == []
    assert check([[CODE], [UNIT]]) == []


def test_merge_commit_in_range_fails(tmp_path, monkeypatch, capsys):
    import subprocess

    import exam_integrity

    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
                       cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "base")
    git("branch", "side")
    git("commit", "-q", "--allow-empty", "-m", "a")
    git("checkout", "-q", "side")
    git("commit", "-q", "--allow-empty", "-m", "b")
    git("merge", "-q", "--no-ff", "-m", "m", "main")
    monkeypatch.chdir(tmp_path)
    assert exam_integrity.main("main~1", "HEAD", False) == 1
    assert "merge commits are not allowed" in capsys.readouterr().out


def test_exam_with_ci_surface_fails_unless_bootstrap():
    commits = [[EXAM], [CI]]
    assert len(check(commits)) == 1
    assert check(commits, bootstrap=True) == []


def test_pytest_config_outside_tests_is_exam_surface():
    for cfg in ("labs/manager/conftest.py", "labs/manager/pytest.ini", "labs/web/setup.cfg"):
        assert len(check([[CODE], [cfg]])) == 1, cfg
    assert check([[CODE], ["labs/manager/tests/unit/conftest.py"]]) == []
