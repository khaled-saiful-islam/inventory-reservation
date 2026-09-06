"""Test doubles.

`FakeClock` is the reason no test in this suite ever calls `sleep()`. Expiry
windows are measured in minutes; the tests that cover them run in microseconds
because they move time forward by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FakeClock:
    """A clock that only moves when a test tells it to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
