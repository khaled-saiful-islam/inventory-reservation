"""Core domain objects.

This module depends on nothing but the standard library and its sibling
`errors`. No FastAPI, no storage, no clock instance -- times are passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from inventory.domain.errors import (
    InvalidQuantity,
    InvalidStateTransition,
    InvalidStock,
)


class ReservationState(StrEnum):
    """The four states a reservation can be in.

    ACTIVE is the only one that can still change. The other three are final:
    once a reservation is confirmed, cancelled, or expired, it stays that way.
    """

    ACTIVE = "active"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self is not ReservationState.ACTIVE


@dataclass(frozen=True, slots=True)
class Reservation:
    """A temporary hold on stock.

    Frozen on purpose. A state change returns a new `Reservation` instead of
    mutating this one, so a caller can never end up holding an object whose
    state changed underneath it.
    """

    id: str
    product_id: str
    quantity: int
    state: ReservationState
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise InvalidQuantity(f"Quantity must be at least 1, got {self.quantity}")

    @classmethod
    def create(
        cls,
        *,
        id: str,
        product_id: str,
        quantity: int,
        now: datetime,
        hold: timedelta,
    ) -> Reservation:
        """Open a reservation that holds stock until `now + hold`."""
        return cls(
            id=id,
            product_id=product_id,
            quantity=quantity,
            state=ReservationState.ACTIVE,
            created_at=now,
            expires_at=now + hold,
        )

    def confirm(self) -> Reservation:
        return self._transition_to(ReservationState.CONFIRMED)

    def cancel(self) -> Reservation:
        return self._transition_to(ReservationState.CANCELLED)

    def expire(self) -> Reservation:
        return self._transition_to(ReservationState.EXPIRED)

    def is_expired_at(self, now: datetime) -> bool:
        """True when this hold is still active but its window has already elapsed.

        Nothing expires a reservation on a timer. It becomes expired the moment
        anyone looks at it after the deadline -- see the service layer, which
        sweeps these before it reads available stock.
        """
        return self.state is ReservationState.ACTIVE and now >= self.expires_at

    def _transition_to(self, target: ReservationState) -> Reservation:
        if self.state.is_terminal:
            raise InvalidStateTransition(self.id, self.state, target)
        return replace(self, state=target)


@dataclass(frozen=True, slots=True)
class Product:
    """A catalogue entry. Holds the total stock, not the current availability."""

    id: str
    name: str
    total_stock: int

    def __post_init__(self) -> None:
        if self.total_stock < 0:
            raise InvalidStock(f"Total stock cannot be negative, got {self.total_stock}")


@dataclass(frozen=True, slots=True)
class StockLevel:
    """A snapshot of one product's stock at a point in time.

    `available` is the single definition of the invariant this whole system
    exists to protect. It is not recomputed anywhere else.
    """

    total: int
    confirmed: int
    reserved: int

    @property
    def available(self) -> int:
        return self.total - self.confirmed - self.reserved
