"""The storage port.

The service depends on this interface, not on the in-memory class that
implements it. Swapping to PostgreSQL means writing one new class here and
changing one line of wiring -- no business logic moves.

A `Protocol` rather than an ABC, for the same reason `Clock` is one: an
implementation does not need to inherit anything, it just needs the methods.
"""

from __future__ import annotations

from typing import Protocol

from inventory.domain.models import Product, Reservation


class InventoryRepository(Protocol):
    def add_product(self, product: Product) -> None:
        """Store a new product. Raises `DuplicateProduct` if the id is taken."""
        ...

    def get_product(self, product_id: str) -> Product:
        """Fetch a product. Raises `ProductNotFound` if there is no such id."""
        ...

    def add_reservation(self, reservation: Reservation) -> None:
        """Store a new reservation."""
        ...

    def get_reservation(self, reservation_id: str) -> Reservation:
        """Fetch a reservation. Raises `ReservationNotFound` if there is no such id."""
        ...

    def replace_reservation(self, reservation: Reservation) -> None:
        """Overwrite the stored reservation that shares this id.

        Reservations are immutable, so a state change produces a new object that
        takes the old one's place.
        """
        ...

    def reservations_for_product(self, product_id: str) -> list[Reservation]:
        """Every reservation ever made against this product, in creation order."""
        ...
