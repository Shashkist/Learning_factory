"""Shared fixtures for the acceptance exams."""

import pytest


@pytest.fixture
def no_janitor(monkeypatch):
    """Keep the janitor's pass out of an exam harness (#284, #290).

    A harness that swaps its FakeRuntime in after the app has started leaves the janitor's startup
    pass to fail on the missing Docker socket and retry about 2 s later. FakeRuntime lists no
    containers, so that retry ends every running session as an orphan and any case longer than
    about 2 s fails at random with `session_ended`. Request this fixture before the app starts.
    Exams of the janitor itself must not use it."""
    import app.janitor as janitor

    async def idle(_app):
        return True

    monkeypatch.setattr(janitor, "run_once", idle)
