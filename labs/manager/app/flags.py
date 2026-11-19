"""Fast-work flags and active time (D37, spec section 8): pure functions, no database."""
from collections.abc import Iterable
from statistics import median
from typing import Any

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


def fast_task_reason(
    task_id: str,
    kind: str,
    seconds: float,
    min_task_seconds: float = DEFAULT_MIN_TASK_SECONDS,
    repeat: bool = False,
) -> str | None:
    """The teacher-facing sentence when `is_fast_task` holds, else None."""
    if not is_fast_task(kind, seconds, min_task_seconds, repeat):
        return None
    return f"task {task_id} passed {int(seconds)} s after it appeared"


def fast_lab_reason(
    active_seconds: float,
    fast_lab_min: float | None = None,
    prior_active_seconds: Iterable[float] = (),
    repeat: bool = False,
) -> str | None:
    """Under `fast_lab_min` minutes, or under 30 % of the median of at least 5 earlier students.

    Returns the teacher-facing sentence, or None when the lab is not fast."""
    if repeat:
        return None
    spent = f"lab finished in {int(active_seconds)} s of active time"
    if fast_lab_min is not None and active_seconds < fast_lab_min * 60:
        return f"{spent}, under the {fast_lab_min} min minimum"
    priors = list(prior_active_seconds)
    if len(priors) >= MEDIAN_MIN_PRIORS:
        typical = median(priors)
        if active_seconds < MEDIAN_FRACTION * typical:
            return (
                f"{spent}, under {round(MEDIAN_FRACTION * 100)} % of the class median "
                f"({typical:.0f} s)"
            )
    return None


def fast_task_detail(
    task_id: str,
    kind: str,
    seconds: float,
    min_task_seconds: float = DEFAULT_MIN_TASK_SECONDS,
    repeat: bool = False,
) -> dict[str, Any] | None:
    """The structured twin of `fast_task_reason`: code, whole seconds and the limit."""
    if fast_task_reason(task_id, kind, seconds, min_task_seconds, repeat) is None:
        return None
    return {"code": "fast_task", "seconds": int(seconds), "threshold": min_task_seconds}


def fast_lab_detail(
    active_seconds: float,
    fast_lab_min: float | None = None,
    prior_active_seconds: Iterable[float] = (),
    repeat: bool = False,
) -> dict[str, Any] | None:
    """The structured twin of `fast_lab_reason`; the minimum wins when both rules hold."""
    priors = list(prior_active_seconds)
    if fast_lab_reason(active_seconds, fast_lab_min, priors, repeat) is None:
        return None
    if fast_lab_min is not None and active_seconds < fast_lab_min * 60:
        code, threshold = "fast_lab_min", fast_lab_min * 60
    else:
        code, threshold = "fast_lab_median", median(priors)
    return {"code": code, "seconds": int(active_seconds), "threshold": threshold}


def is_fast_lab(
    active_seconds: float,
    fast_lab_min: float | None = None,
    prior_active_seconds: Iterable[float] = (),
    repeat: bool = False,
) -> bool:
    return fast_lab_reason(active_seconds, fast_lab_min, prior_active_seconds, repeat) is not None


def covered_seconds(beats: Iterable[float], timeout: float = 300) -> float:
    """The sum of the gaps between consecutive heartbeats that are not longer than `timeout`.

    The reference for the SQL in the heartbeat route (app.sessions), which accumulates the same
    gaps one beat at a time."""
    ordered = sorted(beats)
    gaps = (b - a for a, b in zip(ordered, ordered[1:], strict=False))
    return sum(g for g in gaps if g <= timeout)
