"""Fail when a pytest junit report has no tests or any skipped test.

Usage: python junit_gate.py <report.xml> [--allow-empty]

--allow-empty accepts a report with no tests (docker-marked exams may not
exist yet) but still fails on any skipped test.
"""
import sys
import xml.etree.ElementTree as ET


def check(path: str, allow_empty: bool = False) -> list[str]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = sum(int(s.get("tests", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    errors = []
    if tests == 0 and not allow_empty:
        errors.append("no acceptance tests ran")
    if skipped:
        errors.append(f"{skipped} acceptance tests were skipped")
    return errors


if __name__ == "__main__":
    problems = check(sys.argv[1], allow_empty="--allow-empty" in sys.argv[2:])
    for p in problems:
        print(f"FAIL: {p}")
    sys.exit(1 if problems else 0)
