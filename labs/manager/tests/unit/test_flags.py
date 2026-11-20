from app.flags import covered_seconds, is_fast_lab, is_fast_task


def test_task_limit_is_strict_and_quiz_never_fast() -> None:
    assert is_fast_task("hands-on", 19.9) and not is_fast_task("hands-on", 20)
    assert not is_fast_task("quiz", 0)
    assert not is_fast_task("hands-on", 0, repeat=True)


def test_lab_rules_and_median() -> None:
    assert is_fast_lab(599, 10) and not is_fast_lab(600, 10)
    assert not is_fast_lab(0, None, [1000] * 4)
    assert is_fast_lab(299, None, [100, 100, 1000, 1000, 1000])
    assert not is_fast_lab(0, 10, repeat=True)


def test_covered_seconds_drops_long_gaps() -> None:
    assert covered_seconds([0, 10, 400, 410]) == 20
    assert covered_seconds([5]) == 0


def test_active_time_rounds_up_once() -> None:
    from app.flags import fast_lab_detail, fast_lab_reason

    # 599.2 s is 600 s once rounded up: not under a 10 minute minimum, as it is stored
    assert not is_fast_lab(599.2, 10)
    assert fast_lab_detail(599.2, 10) is None
    assert is_fast_lab(598.5, 10)
    detail = fast_lab_detail(598.5, 10)
    assert detail is not None and detail["seconds"] == 599
    assert "in 599 s" in (fast_lab_reason(598.5, 10) or "")
    # class median: 300.4 s is 301 s, not under 30 % of 1000 s
    assert not is_fast_lab(300.4, None, [1000] * 5)
    assert is_fast_lab(299.0, None, [1000] * 5)
