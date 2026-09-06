"""In-memory implementation of the storage port.

The challenge specifies in-memory inventory, so this is the whole persistence
layer. It holds two dictionaries and an index; it contains no business rules.
"""

from __future__ import annotations

import threading

from inventory.domain.errors import (
    DuplicateProduct,
    ProductNotFound,
    ReservationNotFound,
)
from inventory.domain.models import Product, Reservation


class InMemoryInventoryRepository:
    """Process-local storage for products and reservations.

    It hands out one mutex per product but contains no stock arithmetic --
    availability is the service's business rule, not storage's.
    """

    def __init__(self) -> None:
        self._products: dict[str, Product] = {}
        self._reservations: dict[str, Reservation] = {}
        # product_id -> reservation ids, so reading one product's holds never
        # scans every reservation in the system.
        self._reservation_ids_by_product: dict[str, list[str]] = {}
        # One mutex per product. Reserving a sneaker must not block a jacket.
        self._locks: dict[str, threading.Lock] = {}
        # Guards the registry above while a product is being added or looked up.
        # Held for a dictionary access only, never across business logic.
        self._registry = threading.Lock()

    def lock_product(self, product_id: str) -> threading.Lock:
        """The mutex protecting one product. Raises `ProductNotFound`."""
        with self._registry:
            try:
                return self._locks[product_id]
            except KeyError:
                raise ProductNotFound(f"Product {product_id} does not exist") from None

    def add_product(self, product: Product) -> None:
        with self._registry:
            if product.id in self._products:
                raise DuplicateProduct(f"Product {product.id} already exists")
            self._products[product.id] = product
            self._reservation_ids_by_product[product.id] = []
            self._locks[product.id] = threading.Lock()

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
