"""Level 1: basic reservation against available stock.

The rule under test is the one from the brief: a reservation that would exceed
available stock must fail, and only one caller can take the last item.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from inventory.domain.errors import (
    DuplicateProduct,
    InsufficientStock,
    InvalidQuantity,
    ProductNotFound,
    ReservationNotFound,
)
from inventory.domain.models import ReservationState


class TestCreateProduct:
    def test_a_new_product_has_all_of_its_stock_available(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=10)

        level = service.get_stock_level("p1")

        assert level.total == 10
        assert level.confirmed == 0
        assert level.reserved == 0
        assert level.available == 10

    def test_the_same_id_cannot_be_registered_twice(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=10)

        with pytest.raises(DuplicateProduct):
            service.create_product(product_id="p1", name="Other", total_stock=5)

    def test_an_unknown_product_cannot_be_inspected(self, service):
        with pytest.raises(ProductNotFound):
            service.get_stock_level("nope")


class TestReserve:
    def test_a_successful_reservation_is_active_and_holds_stock(self, service, clock):
        service.create_product(product_id="p1", name="Sneaker", total_stock=10)

        reservation = service.reserve(product_id="p1", quantity=3)

        assert reservation.state is ReservationState.ACTIVE
        assert reservation.quantity == 3
        assert reservation.created_at == clock.now()
        assert reservation.expires_at == clock.now() + timedelta(minutes=2)

    def test_a_hold_reduces_what_is_available_to_everyone_else(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=10)

        service.reserve(product_id="p1", quantity=4)

        level = service.get_stock_level("p1")
        assert level.reserved == 4
        assert level.available == 6

    def test_only_one_caller_can_take_the_last_item(self, service):
        """The headline rule: stock = 1, first caller wins, second is refused."""
        service.create_product(product_id="p1", name="Sneaker", total_stock=1)

        first = service.reserve(product_id="p1", quantity=1)

        assert first.state is ReservationState.ACTIVE
        with pytest.raises(InsufficientStock):
            service.reserve(product_id="p1", quantity=1)

    def test_a_request_larger_than_the_stock_is_refused(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=5)

        with pytest.raises(InsufficientStock):
            service.reserve(product_id="p1", quantity=6)

    def test_the_refusal_says_how_much_was_asked_for_and_what_was_left(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=2)

        with pytest.raises(InsufficientStock) as caught:
            service.reserve(product_id="p1", quantity=5)

        assert caught.value.requested == 5
        assert caught.value.available == 2

    def test_taking_exactly_what_is_left_is_allowed(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=5)

        service.reserve(product_id="p1", quantity=5)

        assert service.get_stock_level("p1").available == 0

    def test_several_holds_accumulate_until_the_stock_runs_out(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=3)

        service.reserve(product_id="p1", quantity=1)
        service.reserve(product_id="p1", quantity=2)

        assert service.get_stock_level("p1").available == 0
        with pytest.raises(InsufficientStock):
            service.reserve(product_id="p1", quantity=1)

    @pytest.mark.parametrize("quantity", [0, -1])
    def test_a_quantity_below_one_is_rejected(self, service, quantity):
        service.create_product(product_id="p1", name="Sneaker", total_stock=10)

        with pytest.raises(InvalidQuantity):
            service.reserve(product_id="p1", quantity=quantity)

    def test_an_unknown_product_cannot_be_reserved(self, service):
        with pytest.raises(ProductNotFound):
            service.reserve(product_id="nope", quantity=1)

    def test_products_do_not_share_stock(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=1)
        service.create_product(product_id="p2", name="Jacket", total_stock=1)

        service.reserve(product_id="p1", quantity=1)

        assert service.get_stock_level("p1").available == 0
        assert service.get_stock_level("p2").available == 1

    def test_every_reservation_gets_its_own_id(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=10)

        ids = {service.reserve(product_id="p1", quantity=1).id for _ in range(5)}

        assert len(ids) == 5


class TestGetReservation:
    def test_a_reservation_can_be_read_back_by_id(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=10)
        created = service.reserve(product_id="p1", quantity=2)

        assert service.get_reservation(created.id) == created

    def test_an_unknown_reservation_id_is_an_error(self, service):
        with pytest.raises(ReservationNotFound):
            service.get_reservation("nope")
