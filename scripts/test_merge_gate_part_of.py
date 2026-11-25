"""Unit tests for #590: merge_gate reads a line-anchored `Part of #n`."""

import merge_gate


def test_part_of_matches_own_line_only():
    assert merge_gate.PART_OF.findall("a\n  PART OF #5  \nb") == ["5"]
    assert merge_gate.PART_OF.findall("this is part of #5 work") == []


def test_part_of_and_closes_are_distinct_forms():
    body = "Closes #1\nPart of #2\n"
    assert merge_gate.CLOSES.findall(body) == ["1"]
    assert merge_gate.PART_OF.findall(body) == ["2"]
