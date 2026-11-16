"""Fast-work flags and active time (D37, spec section 8): pure functions, no database."""
from collections.abc import Iterable
from statistics import median

DEFAULT_MIN_TASK_SECONDS = 20
MEDIAN_MIN_PRIORS = 5
MEDIAN_FRACTION = 0.3


def is_fast_task(
    kind: str,
    seconds: float,
    min_task_seconds: float = DEFAULT_MIN_TASK_SECONDS,
    repeat: bool = False,
) -> bool:
    """A hands-on task passed in less than `min_task_seconds` after it appeared."""
    return kind == "hands-on" and not repeat and seconds < min_task_seconds


def is_fast_lab(
    active_seconds: float,
    fast_lab_min: float | None = None,
    prior_active_seconds: Iterable[float] = (),
    repeat: bool = False,
) -> bool:
    """Under `fast_lab_min` minutes, or under 30 % of the median of at least 5 earlier students."""
    if repeat:
        return False
    if fast_lab_min is not None and active_seconds < fast_lab_min * 60:
        return True
    priors = list(prior_active_seconds)
    return len(priors) >= MEDIAN_MIN_PRIORS and active_seconds < MEDIAN_FRACTION * median(priors)


def covered_seconds(beats: Iterable[float], timeout: float = 300) -> float:
    """The sum of the gaps between consecutive heartbeats that are not longer than `timeout`."""
    ordered = sorted(beats)
    gaps = (b - a for a, b in zip(ordered, ordered[1:], strict=False))
    return sum(g for g in gaps if g <= timeout)
