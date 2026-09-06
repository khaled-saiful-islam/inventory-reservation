"""In-memory implementation of the storage port.

The challenge specifies in-memory inventory, so this is the whole persistence
layer. It holds two dictionaries and an index; it contains no business rules.
"""

from __future__ import annotations

from inventory.domain.errors import (
    DuplicateProduct,
    ProductNotFound,
    ReservationNotFound,
)
from inventory.domain.models import Product, Reservation


class InMemoryInventoryRepository:
    """Process-local storage for products and reservations.

    Note what is *not* here: no lock, and no stock arithmetic. Locking arrives in
    Step 4 once there is a failing concurrency test to justify its shape, and
    availability is the service's business rule, not storage's.
    """

    def __init__(self) -> None:
        self._products: dict[str, Product] = {}
        self._reservations: dict[str, Reservation] = {}
        # product_id -> reservation ids, so reading one product's holds never
        # scans every reservation in the system.
        self._reservation_ids_by_product: dict[str, list[str]] = {}

    def add_product(self, product: Product) -> None:
        if product.id in self._products:
            raise DuplicateProduct(f"Product {product.id} already exists")
        self._products[product.id] = product
        self._reservation_ids_by_product[product.id] = []

    def get_product(self, product_id: str) -> Product:
        try:
            return self._products[product_id]
        except KeyError:
            raise ProductNotFound(f"Product {product_id} does not exist") from None

    def add_reservation(self, reservation: Reservation) -> None:
        self._reservations[reservation.id] = reservation
        self._reservation_ids_by_product[reservation.product_id].append(reservation.id)

    def get_reservation(self, reservation_id: str) -> Reservation:
        try:
            return self._reservations[reservation_id]
        except KeyError:
            raise ReservationNotFound(f"Reservation {reservation_id} does not exist") from None

    def replace_reservation(self, reservation: Reservation) -> None:
        if reservation.id not in self._reservations:
            raise ReservationNotFound(f"Reservation {reservation.id} does not exist")
        self._reservations[reservation.id] = reservation

    def reservations_for_product(self, product_id: str) -> list[Reservation]:
        ids = self._reservation_ids_by_product.get(product_id, [])
        return [self._reservations[reservation_id] for reservation_id in ids]
