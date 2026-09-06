"""Domain errors.

Every error carries a stable `code`. The API layer turns that code into an HTTP
status and puts it in the response body, so clients can branch on a string that
will not change when a message is reworded.
"""

from __future__ import annotations


class InventoryError(Exception):
    """Base class for anything this domain rejects."""

    code = "inventory_error"


class InvalidQuantity(InventoryError):
    code = "invalid_quantity"


class InvalidStock(InventoryError):
    code = "invalid_stock"


class InvalidStateTransition(InventoryError):
    code = "invalid_state_transition"

    def __init__(self, reservation_id: str, current: str, attempted: str) -> None:
        super().__init__(
            f"Reservation {reservation_id} is already {current} and cannot become {attempted}"
        )
        self.reservation_id = reservation_id
        self.current = current
        self.attempted = attempted


class ProductNotFound(InventoryError):
    code = "product_not_found"


class DuplicateProduct(InventoryError):
    code = "duplicate_product"


class ReservationNotFound(InventoryError):
    code = "reservation_not_found"


class InsufficientStock(InventoryError):
    code = "insufficient_stock"

    def __init__(self, product_id: str, requested: int, available: int) -> None:
        super().__init__(f"Product {product_id} has {available} available, {requested} requested")
        self.product_id = product_id
        self.requested = requested
        self.available = available
