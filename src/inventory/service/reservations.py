"""Business rules for reserving stock.

Everything the brief calls a rule lives here. This layer knows the domain and
the storage port; it knows nothing about HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

from inventory.domain.clock import Clock, SystemClock
from inventory.domain.errors import InsufficientStock, InvalidQuantity
from inventory.domain.models import (
    Product,
    Reservation,
    ReservationState,
    StockLevel,
)
from inventory.repository.base import InventoryRepository

DEFAULT_HOLD = timedelta(minutes=2)


class ReservationService:
    """Reserves stock without ever letting `available` go negative."""

    def __init__(
        self,
        repository: InventoryRepository,
        clock: Clock | None = None,
        hold: timedelta = DEFAULT_HOLD,
    ) -> None:
        self._repository = repository
        self._clock = clock or SystemClock()
        self._hold = hold

    def create_product(self, *, product_id: str, name: str, total_stock: int) -> Product:
        product = Product(id=product_id, name=name, total_stock=total_stock)
        self._repository.add_product(product)
        return product

    def get_stock_level(self, product_id: str) -> StockLevel:
        """The current split of a product's stock. Raises `ProductNotFound`."""
        with self._repository.lock_product(product_id):
            return self._current_level(product_id)

    def get_reservation(self, reservation_id: str) -> Reservation:
        """Raises `ReservationNotFound`."""
        return self._apply(reservation_id, _no_change)

    def confirm(self, reservation_id: str) -> Reservation:
        """Turn an active hold into a completed sale.

        Raises `ReservationNotFound`, or `InvalidStateTransition` if the hold has
        already been confirmed, cancelled, or has run out of time.
        """
        return self._apply(reservation_id, Reservation.confirm)

    def cancel(self, reservation_id: str) -> Reservation:
        """Give up an active hold and return its stock to the pool.

        Raises `ReservationNotFound`, or `InvalidStateTransition` if the hold is
        already in a final state.
        """
        return self._apply(reservation_id, Reservation.cancel)

    def reserve(self, *, product_id: str, quantity: int) -> Reservation:
        """Hold `quantity` units of a product for the configured window.

        Raises `InvalidQuantity`, `ProductNotFound`, or `InsufficientStock`.
        """
        if quantity < 1:
            raise InvalidQuantity(f"Quantity must be at least 1, got {quantity}")

        with self._repository.lock_product(product_id):
            available = self._current_level(product_id).available
            if quantity > available:
                raise InsufficientStock(product_id, requested=quantity, available=available)

            reservation = Reservation.create(
                id=uuid4().hex,
                product_id=product_id,
                quantity=quantity,
                now=self._clock.now(),
                hold=self._hold,
            )
            self._repository.add_reservation(reservation)
            return reservation

    def _apply(
        self, reservation_id: str, transition: Callable[[Reservation], Reservation]
    ) -> Reservation:
        """Settle any lapsed hold, then run a state transition and store the result.

        Settling first is what makes "you cannot pay for something whose hold ran
        out" fall out for free: the reservation is already EXPIRED by the time the
        transition is attempted, and the domain refuses it.

        The read outside the lock only discovers which product to lock -- a
        reservation never moves between products. Everything that decides or
        writes happens again inside the lock.
        """
        product_id = self._repository.get_reservation(reservation_id).product_id
        with self._repository.lock_product(product_id):
            reservation = self._settle(self._repository.get_reservation(reservation_id))
            updated = transition(reservation)
            if updated is reservation:
                return reservation
            self._repository.replace_reservation(updated)
            return updated

    def _current_level(self, product_id: str) -> StockLevel:
        """Reclaim lapsed holds, then derive the stock split.

        Callers must already hold this product's lock.

        Sweeping before counting is the only mechanism that decides expiry. The
        counting step does not filter on deadlines as well -- one rule, one place.
        """
        product = self._repository.get_product(product_id)
        self._sweep_lapsed(product_id)

        reservations = self._repository.reservations_for_product(product_id)
        return StockLevel(
            total=product.total_stock,
            confirmed=self._quantity_in(reservations, ReservationState.CONFIRMED),
            reserved=self._quantity_in(reservations, ReservationState.ACTIVE),
        )

    def _sweep_lapsed(self, product_id: str) -> None:
        """Mark every hold on this product whose window has closed as EXPIRED."""
        now = self._clock.now()
        for reservation in self._repository.reservations_for_product(product_id):
            if reservation.is_expired_at(now):
                self._repository.replace_reservation(reservation.expire())

    def _settle(self, reservation: Reservation) -> Reservation:
        """Record a single lapsed hold as expired. A no-op for anything else."""
        if not reservation.is_expired_at(self._clock.now()):
            return reservation
        expired = reservation.expire()
        self._repository.replace_reservation(expired)
        return expired

    @staticmethod
    def _quantity_in(reservations: list[Reservation], state: ReservationState) -> int:
        return sum(r.quantity for r in reservations if r.state is state)


def _no_change(reservation: Reservation) -> Reservation:
    """A read is a transition that transitions nothing.

    Routing `get_reservation` through `_apply` means a read settles a lapsed
    hold under the same lock as a write, instead of having a second code path
    that does almost the same thing.
    """
    return reservation
