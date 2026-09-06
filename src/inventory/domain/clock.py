"""Time as an injected dependency.

Reservations expire after a wall-clock window, so the logic that decides expiry
has to read the current time from somewhere. Reading it from a `Clock` rather
than calling `datetime.now()` inline is what makes expiry testable without
waiting two real minutes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Anything that can report the current time."""

    def now(self) -> datetime: ...


class SystemClock:
    """The real clock, used in production. Always timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)
