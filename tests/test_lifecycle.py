"""Level 2: the reservation lifecycle and the two-minute hold.

Every test here moves time with `FakeClock`. Nothing sleeps, so the whole file
covering a two-minute window runs in milliseconds.
"""

from __future__ import annotations

import pytest

from inventory.domain.errors import (
    InsufficientStock,
    InvalidStateTransition,
    ReservationNotFound,
)
from inventory.domain.models import ReservationState

HOLD_SECONDS = 120


@pytest.fixture
def product(service):
    service.create_product(product_id="p1", name="Sneaker", total_stock=1)
    return "p1"


class TestConfirm:
    def test_confirming_turns_a_hold_into_a_completed_sale(self, service, product):
        reservation = service.reserve(product_id=product, quantity=1)

        confirmed = service.confirm(reservation.id)

        assert confirmed.state is ReservationState.CONFIRMED
        level = service.get_stock_level(product)
        assert level.confirmed == 1
        assert level.reserved == 0
        assert level.available == 0

    def test_a_confirmed_sale_cannot_be_confirmed_again(self, service, product):
        reservation = service.reserve(product_id=product, quantity=1)
        service.confirm(reservation.id)

        with pytest.raises(InvalidStateTransition):
            service.confirm(reservation.id)

    def test_a_confirmed_sale_cannot_be_cancelled(self, service, product):
        """ "Confirmed purchases cannot be reversed" -- checked through the service."""
        reservation = service.reserve(product_id=product, quantity=1)
        service.confirm(reservation.id)

        with pytest.raises(InvalidStateTransition):
            service.cancel(reservation.id)

    def test_confirming_does_not_free_stock_for_anyone_else(self, service, product):
        reservation = service.reserve(product_id=product, quantity=1)
        service.confirm(reservation.id)

        with pytest.raises(InsufficientStock):
            service.reserve(product_id=product, quantity=1)

    def test_an_unknown_reservation_cannot_be_confirmed(self, service):
        with pytest.raises(ReservationNotFound):
            service.confirm("nope")


class TestCancel:
    def test_cancelling_releases_the_hold(self, service, product):
        reservation = service.reserve(product_id=product, quantity=1)

        cancelled = service.cancel(reservation.id)

        assert cancelled.state is ReservationState.CANCELLED
        assert service.get_stock_level(product).available == 1

    def test_released_stock_can_be_taken_by_someone_else(self, service, product):
        first = service.reserve(product_id=product, quantity=1)
        service.cancel(first.id)

        second = service.reserve(product_id=product, quantity=1)

        assert second.state is ReservationState.ACTIVE

    def test_a_cancelled_hold_cannot_be_confirmed(self, service, product):
        reservation = service.reserve(product_id=product, quantity=1)
        service.cancel(reservation.id)

        with pytest.raises(InvalidStateTransition):
            service.confirm(reservation.id)

    def test_cancelling_twice_is_an_error(self, service, product):
        reservation = service.reserve(product_id=product, quantity=1)
        service.cancel(reservation.id)

        with pytest.raises(InvalidStateTransition):
            service.cancel(reservation.id)


class TestExpiry:
    def test_a_hold_still_blocks_stock_one_second_before_its_deadline(
        self, service, clock, product
    ):
        service.reserve(product_id=product, quantity=1)

        clock.advance(HOLD_SECONDS - 1)

        assert service.get_stock_level(product).available == 0
        with pytest.raises(InsufficientStock):
            service.reserve(product_id=product, quantity=1)

    def test_stock_comes_back_once_the_deadline_passes(self, service, clock, product):
        service.reserve(product_id=product, quantity=1)

        clock.advance(HOLD_SECONDS)

        level = service.get_stock_level(product)
        assert level.reserved == 0
        assert level.available == 1

    def test_someone_else_can_reserve_after_a_hold_expires(self, service, clock, product):
        first = service.reserve(product_id=product, quantity=1)

        clock.advance(HOLD_SECONDS + 1)
        second = service.reserve(product_id=product, quantity=1)

        assert second.state is ReservationState.ACTIVE
        assert second.id != first.id

    def test_reading_a_reservation_after_its_deadline_reports_it_expired(
        self, service, clock, product
    ):
        reservation = service.reserve(product_id=product, quantity=1)

        clock.advance(HOLD_SECONDS + 1)

        assert service.get_reservation(reservation.id).state is ReservationState.EXPIRED

    def test_an_expired_hold_cannot_be_confirmed(self, service, clock, product):
        """The race the brief implies: pay for something whose hold ran out."""
        reservation = service.reserve(product_id=product, quantity=1)

        clock.advance(HOLD_SECONDS + 1)

        with pytest.raises(InvalidStateTransition):
            service.confirm(reservation.id)

    def test_an_expired_hold_cannot_be_cancelled(self, service, clock, product):
        reservation = service.reserve(product_id=product, quantity=1)

        clock.advance(HOLD_SECONDS + 1)

        with pytest.raises(InvalidStateTransition):
            service.cancel(reservation.id)

    def test_expiry_is_recorded_once_and_stays_recorded(self, service, clock, product):
        """The sweep must be idempotent -- reading twice must not change anything."""
        reservation = service.reserve(product_id=product, quantity=1)
        clock.advance(HOLD_SECONDS + 1)

        first_read = service.get_reservation(reservation.id)
        second_read = service.get_reservation(reservation.id)

        assert first_read == second_read
        assert service.get_stock_level(product).available == 1

    def test_a_confirmed_sale_is_never_expired_by_the_sweep(self, service, clock, product):
        reservation = service.reserve(product_id=product, quantity=1)
        service.confirm(reservation.id)

        clock.advance(HOLD_SECONDS * 10)

        assert service.get_reservation(reservation.id).state is ReservationState.CONFIRMED
        assert service.get_stock_level(product).available == 0

    def test_only_the_holds_past_their_deadline_are_swept(self, service, clock):
        """Two holds opened a minute apart expire a minute apart, not together."""
        service.create_product(product_id="p2", name="Jacket", total_stock=2)
        early = service.reserve(product_id="p2", quantity=1)
        clock.advance(60)
        late = service.reserve(product_id="p2", quantity=1)

        clock.advance(61)

        assert service.get_reservation(early.id).state is ReservationState.EXPIRED
        assert service.get_reservation(late.id).state is ReservationState.ACTIVE
        assert service.get_stock_level("p2").available == 1
