from app import janitor, runtime, sessions


def test_minimum_is_the_real_worst_case_of_a_starting_row():
    assert (
        janitor.start_deadline_minimum()
        == sessions.READY_TIMEOUT_MAX_S + runtime.KILL_BOUND_S + sessions.CLEANUP_BOUND_S
    )
    assert janitor.start_deadline_minimum() == 52.0
