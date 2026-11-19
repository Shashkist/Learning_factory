"""Fail when a pytest junit report has no tests or any skipped test.

Usage: python junit_gate.py <report.xml>

There is no way to accept an empty report: an exam run that collected nothing
is a broken gate, not a pass (#243).
"""
import sys
import xml.etree.ElementTree as ET


def check(path: str) -> list[str]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = sum(int(s.get("tests", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    errors = []
    if tests == 0:
        errors.append("no acceptance tests ran")
    if skipped:
        errors.append(f"{skipped} acceptance tests were skipped")
    return errors


if __name__ == "__main__":
    problems = check(sys.argv[1])
    for p in problems:
        print(f"FAIL: {p}")
    sys.exit(1 if problems else 0)
