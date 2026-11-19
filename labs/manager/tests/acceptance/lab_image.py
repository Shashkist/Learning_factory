"""Shared, process-wide build of the lab image for every docker exam (issue #228).

Before: ten exam modules each ran `docker build -t lf-lab-term:exam` in their own
fixture, so a mirror outage cost ten failed builds of up to 1500 s each.

Contract:
- `ensure_lab_image()` returns the image tag. The first call builds the image
  (or returns $LF_LAB_TERM_IMAGE when set); every later call, from any module,
  reuses the result.
- A failed build is remembered: every later call raises the same
  `ImageBuildFailed` at once, carrying the last lines of the build output,
  without a second build attempt.
- `ImageBuilder(build)` is the cache; tests give it a fake `build` callable.
- `build_image(run=...)` is the real build; tests give it a fake `run`.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
IMAGE_DIR = REPO_ROOT / "labs" / "images" / "term"
TAG = "lf-lab-term:exam"
BUILD_TIMEOUT = 1500
TAIL_LINES = 20


class ImageBuildFailed(AssertionError):
    """The lab image did not build; the message ends with the build's last lines."""


def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def build_image(run: Callable[..., object] = subprocess.run) -> str:
    """Run `docker build` once. Raise ImageBuildFailed with the output tail on failure."""
    if not (IMAGE_DIR / "Dockerfile").is_file():
        raise ImageBuildFailed("labs/images/term/Dockerfile is missing")
    cmd = ["docker", "build", "-t", TAG, str(IMAGE_DIR)]
    try:
        res = run(cmd, capture_output=True, text=True, timeout=BUILD_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b""
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        raise ImageBuildFailed(
            f"docker build of {TAG} timed out after {BUILD_TIMEOUT} s; last output:\n{tail(out)}"
        ) from None
    if res.returncode != 0:  # type: ignore[attr-defined]
        out = f"{res.stdout or ''}\n{res.stderr or ''}"  # type: ignore[attr-defined]
        raise ImageBuildFailed(
            f"docker build of {TAG} failed (exit {res.returncode}); "  # type: ignore[attr-defined]
            f"last output:\n{tail(out)}"
        )
    return TAG


class ImageBuilder:
    """Calls `build` at most once; remembers its result or its failure."""

    def __init__(self, build: Callable[[], str]) -> None:
        self._build = build
        self._done = False
        self._tag: str | None = None
        self._error: BaseException | None = None

    def get(self) -> str:
        if not self._done:
            try:
                self._tag = self._build()
            except Exception as exc:
                self._error = exc
            self._done = True
        if self._error is not None:
            raise self._error
        assert self._tag is not None
        return self._tag


_BUILDER = ImageBuilder(build_image)


def ensure_lab_image() -> str:
    """The tag of the lab image, built at most once per test session."""
    env = os.environ.get("LF_LAB_TERM_IMAGE")
    if env:
        return env
    return _BUILDER.get()
