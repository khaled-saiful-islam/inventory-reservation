"""Business rules for reserving stock.

Everything the brief calls a rule lives here. This layer knows the domain and
the storage port; it knows nothing about HTTP.
"""

from __future__ import annotations

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
        return self._stock_level(self._repository.get_product(product_id))

    def get_reservation(self, reservation_id: str) -> Reservation:
        """Raises `ReservationNotFound`."""
        return self._repository.get_reservation(reservation_id)

    def reserve(self, *, product_id: str, quantity: int) -> Reservation:
        """Hold `quantity` units of a product for the configured window.

        Raises `InvalidQuantity`, `ProductNotFound`, or `InsufficientStock`.
        """
        if quantity < 1:
            raise InvalidQuantity(f"Quantity must be at least 1, got {quantity}")

        product = self._repository.get_product(product_id)
        available = self._stock_level(product).available
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

    def _stock_level(self, product: Product) -> StockLevel:
        """Derive the stock split from the reservations themselves.

        Counting on demand rather than maintaining running totals means there is
        no second number that can drift out of step with reality -- and a drifted
        counter is precisely how a system oversells.
        """
        reservations = self._repository.reservations_for_product(product.id)
        return StockLevel(
            total=product.total_stock,
            confirmed=self._quantity_in(reservations, ReservationState.CONFIRMED),
            reserved=self._quantity_in(reservations, ReservationState.ACTIVE),
        )

    @staticmethod
    def _quantity_in(reservations: list[Reservation], state: ReservationState) -> int:
        return sum(r.quantity for r in reservations if r.state is state)
