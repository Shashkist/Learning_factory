import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from junit_gate import check


def _report(tmp_path, tests, skipped, failures=0, errors=0):
    p = tmp_path / "r.xml"
    p.write_text(
        f'<testsuites><testsuite tests="{tests}" skipped="{skipped}" '
        f'failures="{failures}" errors="{errors}"/></testsuites>'
    )
    return str(p)


def test_all_ran_passes(tmp_path):
    assert check(_report(tmp_path, 3, 0)) == []


def test_skipped_fails(tmp_path):
    assert len(check(_report(tmp_path, 3, 1))) == 1


def test_empty_fails(tmp_path):
    assert len(check(_report(tmp_path, 0, 0))) == 1


def test_no_allow_empty_flag(tmp_path):
    # #243: the gate has no way to accept an empty report any more
    gate = Path(__file__).with_name("junit_gate.py")
    report = _report(tmp_path, 0, 0)
    r = subprocess.run([sys.executable, str(gate), report, "--allow-empty"], check=False)
    assert r.returncode == 1


def test_failures_fail_naming_count(tmp_path):
    assert check(_report(tmp_path, 5, 0, failures=2)) == ["2 acceptance tests failed"]


def test_errors_fail_naming_count(tmp_path):
    assert check(_report(tmp_path, 5, 0, errors=3)) == ["3 acceptance tests errored"]


def test_failures_and_errors_both_reported(tmp_path):
    assert len(check(_report(tmp_path, 5, 0, failures=1, errors=1))) == 2


def test_cli_failed_report_exits_1_with_fail_line(tmp_path):
    gate = Path(__file__).with_name("junit_gate.py")
    r = subprocess.run(
        [sys.executable, str(gate), _report(tmp_path, 5, 0, failures=2)],
        check=False, capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "FAIL: 2 acceptance tests failed" in r.stdout


def test_multiple_testsuites_are_summed(tmp_path):
    p = tmp_path / "m.xml"
    p.write_text(
        '<testsuites><testsuite tests="2" failures="0"/>'
        '<testsuite tests="2" failures="1"/></testsuites>'
    )
    assert check(str(p)) == ["1 acceptance tests failed"]


def _xml(tmp_path, body):
    p = tmp_path / "x.xml"
    p.write_text(body)
    return str(p)


def test_failures_only_on_testsuites_root(tmp_path):
    r = _xml(tmp_path, '<testsuites failures="2"><testsuite tests="3"/></testsuites>')
    assert check(r) == ["2 acceptance tests failed"]


def test_failure_and_error_elements_without_counts(tmp_path):
    r = _xml(
        tmp_path,
        '<testsuites><testsuite tests="3"><testcase name="a"><failure/></testcase>'
        '<testcase name="b"><error/></testcase><testcase name="c"/></testsuite></testsuites>',
    )
    assert check(r) == ["1 acceptance tests failed", "1 acceptance tests errored"]


def test_root_testsuite_with_failing_nested_suite(tmp_path):
    r = _xml(
        tmp_path,
        '<testsuite tests="2"><testsuite tests="2">'
        '<testcase name="a"><failure/></testcase><testcase name="b"/></testsuite></testsuite>',
    )
    assert check(r) == ["1 acceptance tests failed"]


def test_nested_parent_sums_children_not_double_counted(tmp_path):
    r = _xml(
        tmp_path,
        '<testsuites failures="1"><testsuite tests="2" failures="1"><testsuite tests="2" failures="1">'
        '<testcase name="a"><failure/></testcase><testcase name="b"/></testsuite></testsuite></testsuites>',
    )
    assert check(r) == ["1 acceptance tests failed"]
