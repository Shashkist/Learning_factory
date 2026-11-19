import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from incident_gate import decide


def test_scheduled_dashboard_needs_a_failed_previous_run():
    assert decide("Factory dashboard", "schedule", "failure") == "raise"
    assert decide("Factory dashboard", "schedule", "success") == "skip"
    assert decide("Factory dashboard", "schedule") == "skip"


def test_everything_else_raises():
    assert decide("Factory dashboard", "push", "success") == "raise"
    assert decide("CI", "schedule", "none") == "raise"
