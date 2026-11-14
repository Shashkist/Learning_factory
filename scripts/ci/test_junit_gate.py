import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from junit_gate import check


def _report(tmp_path, tests, skipped):
    p = tmp_path / "r.xml"
    p.write_text(
        f'<testsuites><testsuite tests="{tests}" skipped="{skipped}"/></testsuites>'
    )
    return str(p)


def test_all_ran_passes(tmp_path):
    assert check(_report(tmp_path, 3, 0)) == []


def test_skipped_fails(tmp_path):
    assert len(check(_report(tmp_path, 3, 1))) == 1


def test_empty_fails(tmp_path):
    assert len(check(_report(tmp_path, 0, 0))) == 1


def test_allow_empty_accepts_no_tests_but_not_skips(tmp_path):
    assert check(_report(tmp_path, 0, 0), allow_empty=True) == []
    assert len(check(_report(tmp_path, 2, 1), allow_empty=True)) == 1
