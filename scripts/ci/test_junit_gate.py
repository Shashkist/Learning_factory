import subprocess
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



def test_no_allow_empty_flag(tmp_path):
    # #243: the gate has no way to accept an empty report any more
    gate = Path(__file__).with_name("junit_gate.py")
    r = subprocess.run([sys.executable, str(gate), str(_report(tmp_path, 0, 0)), "--allow-empty"])
    assert r.returncode == 1
