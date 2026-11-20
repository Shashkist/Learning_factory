"""Acceptance tests for issue #283: one rule for a passed check (review of #280).

`app.content.parse_check_output(stdout, exit_code, n_items=None)`:
with `n_items`, `passed` is true exactly when the exit code is 0 and every item 1..n_items has
a PASS line and no FAIL line; item numbers outside 1..n_items are noise. Without `n_items` the
old rule is kept. The check route uses that value as its `passed`. No database, no Docker.
"""
import asyncio
import uuid
from types import SimpleNamespace

from app import tasks
from app.content import parse_check_output

ALL_PASS = "PASS 1\nPASS 2\nPASS 3\n"
THREE = [{"en": "a", "he": "b"}, {"en": "c", "he": "d"}, {"en": "e", "he": "f"}]


def test_out_of_range_fail_with_exit_0_and_all_items_passing_is_passed_with_n_items():
    assert parse_check_output(ALL_PASS + "FAIL 9 debug\n", 0, 3).passed is True


def test_the_same_output_without_n_items_keeps_the_legacy_failure():
    assert parse_check_output(ALL_PASS + "FAIL 9 debug\n", 0).passed is False


def test_a_missing_item_is_not_passed_with_n_items():
    assert parse_check_output("PASS 1\nPASS 2\n", 0, 3).passed is False


def test_a_fail_on_an_in_range_item_is_not_passed():
    assert parse_check_output("PASS 1\nFAIL 2 why\nPASS 3\n", 0, 3).passed is False


def test_a_nonzero_exit_is_not_passed_even_if_every_item_passed():
    assert parse_check_output(ALL_PASS, 1, 3).passed is False


def test_zero_items_with_exit_0_and_junk_is_passed():
    assert parse_check_output("noise\nFAIL 1\nPASS 2\n", 0, 0).passed is True


def test_zero_items_with_nonzero_exit_is_not_passed():
    assert parse_check_output("", 1, 0).passed is False


def test_item_zero_lines_are_ignored_with_n_items():
    assert parse_check_output("FAIL 0\n" + ALL_PASS, 0, 3).passed is True
    # PASS 0 does not stand in for a missing item
    assert parse_check_output("PASS 0\nPASS 1\nPASS 2\n", 0, 3).passed is False


def test_items_still_hold_every_parsed_line_including_out_of_range_ones():
    r = parse_check_output(ALL_PASS + "FAIL 9 debug\n", 0, 3)
    assert set(r.items) == {1, 2, 3, 9}
    assert r.items[9].passed is False


def test_the_check_route_uses_the_parsers_rule_with_the_checklist_size(monkeypatch):
    calls = []

    def spy(stdout, exit_code, n_items=None):
        calls.append((stdout, exit_code, n_items))
        return SimpleNamespace(passed=True, items={})  # the route has no rule of its own

    task = SimpleNamespace(id="t1", checklist=THREE)

    async def gradable(ctx, task_id, kind):
        return task

    async def run_check(ctx, t):
        return None, "FAIL 1\nFAIL 2\nFAIL 3\n", True

    async def count(ctx, t, passed):
        return 1

    monkeypatch.setattr(tasks, "_gradable", gradable)
    monkeypatch.setattr(tasks, "_run_check", run_check)
    monkeypatch.setattr(tasks, "_count_attempt", count)
    monkeypatch.setattr(tasks, "parse_check_output", spy)
    body = asyncio.run(tasks.check_task("t1", SimpleNamespace(sid=uuid.uuid4())))
    assert calls and calls[0][2] == len(THREE)
    assert calls[0][1] == 0  # _run_check said exit_ok=True, so the parser gets exit code 0
    assert body["passed"] is True  # mirrors the parser's value

    calls.clear()
    spy_false = lambda *a, **k: SimpleNamespace(passed=False, items={})  # noqa: E731
    monkeypatch.setattr(tasks, "parse_check_output", spy_false)
    async def run_ok(ctx, t):
        return None, ALL_PASS, True
    monkeypatch.setattr(tasks, "_run_check", run_ok)
    body = asyncio.run(tasks.check_task("t1", SimpleNamespace(sid=uuid.uuid4())))
    assert body["passed"] is False


def test_the_route_hands_a_nonzero_exit_code_to_the_parser(monkeypatch):
    calls = []

    def spy(stdout, exit_code, n_items=None):
        calls.append(exit_code)
        return SimpleNamespace(passed=False, items={})

    async def gradable(ctx, task_id, kind):
        return SimpleNamespace(id="t1", checklist=THREE)

    async def run_bad(ctx, t):
        return None, ALL_PASS, False

    async def count(ctx, t, passed):
        return 1

    monkeypatch.setattr(tasks, "_gradable", gradable)
    monkeypatch.setattr(tasks, "_run_check", run_bad)
    monkeypatch.setattr(tasks, "_count_attempt", count)
    monkeypatch.setattr(tasks, "parse_check_output", spy)
    asyncio.run(tasks.check_task("t1", SimpleNamespace(sid=uuid.uuid4())))
    assert calls and calls[0] != 0


def _route_with_real_parser(monkeypatch, output: str, exit_ok: bool):
    task = SimpleNamespace(id="t1", checklist=THREE)

    async def gradable(ctx, task_id, kind):
        return task

    async def run_check(ctx, t):
        return None, output, exit_ok

    async def count(ctx, t, passed):
        return 1

    monkeypatch.setattr(tasks, "_gradable", gradable)
    monkeypatch.setattr(tasks, "_run_check", run_check)
    monkeypatch.setattr(tasks, "_count_attempt", count)
    return asyncio.run(tasks.check_task("t1", SimpleNamespace(sid=uuid.uuid4())))


def test_the_route_passes_an_out_of_range_fail_with_the_real_parser(monkeypatch):
    # Criterion 3: the same output is passed by the parser and by the route; no rule of the
    # route's own over the parsed items (item 9 is a FAIL) may turn it back into a failure.
    body = _route_with_real_parser(monkeypatch, ALL_PASS + "FAIL 9 debug\n", True)
    assert body["passed"] is True
    assert [i["passed"] for i in body["checklist"]] == [True, True, True]


def test_the_route_fails_a_missing_item_with_the_real_parser(monkeypatch):
    body = _route_with_real_parser(monkeypatch, "PASS 1\nPASS 2\n", True)
    assert body["passed"] is False
    assert [i["passed"] for i in body["checklist"]] == [True, True, False]


def test_the_route_fails_a_nonzero_exit_even_when_every_item_passed(monkeypatch):
    body = _route_with_real_parser(monkeypatch, ALL_PASS, False)
    assert body["passed"] is False
