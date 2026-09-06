"""Domain layer: the reservation state machine, immutability, and stock arithmetic.

These tests import nothing from FastAPI and start no server. If the core rules of
this system are wrong, they break here first.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from inventory.domain.clock import SystemClock
from inventory.domain.errors import InvalidQuantity, InvalidStateTransition, InvalidStock
from inventory.domain.models import Product, Reservation, ReservationState, StockLevel
from tests.fakes import FakeClock

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
HOLD = timedelta(minutes=2)

TERMINAL_STATES = [
    ReservationState.CONFIRMED,
    ReservationState.CANCELLED,
    ReservationState.EXPIRED,
]


def make_reservation(state: ReservationState = ReservationState.ACTIVE) -> Reservation:
    """Build a reservation in the requested state by driving the real transitions."""
    reservation = Reservation.create(id="r1", product_id="p1", quantity=1, now=BASE, hold=HOLD)
    match state:
        case ReservationState.ACTIVE:
            return reservation
        case ReservationState.CONFIRMED:
            return reservation.confirm()
        case ReservationState.CANCELLED:
            return reservation.cancel()
        case ReservationState.EXPIRED:
            return reservation.expire()


class TestReservationCreation:
    def test_starts_active_and_holds_stock_for_the_given_window(self):
        reservation = Reservation.create(id="r1", product_id="p1", quantity=3, now=BASE, hold=HOLD)

        assert reservation.state is ReservationState.ACTIVE
        assert reservation.quantity == 3
        assert reservation.created_at == BASE
        assert reservation.expires_at == BASE + HOLD

    @pytest.mark.parametrize("quantity", [0, -1])
    def test_rejects_a_quantity_below_one(self, quantity):
        with pytest.raises(InvalidQuantity):
            Reservation.create(id="r1", product_id="p1", quantity=quantity, now=BASE, hold=HOLD)


class TestReservationTransitions:
    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            ("confirm", ReservationState.CONFIRMED),
            ("cancel", ReservationState.CANCELLED),
            ("expire", ReservationState.EXPIRED),
        ],
    )
    def test_an_active_reservation_can_move_to_any_terminal_state(self, action, expected):
        assert getattr(make_reservation(), action)().state is expected

    @pytest.mark.parametrize("state", TERMINAL_STATES)
    @pytest.mark.parametrize("action", ["confirm", "cancel", "expire"])
    def test_a_terminal_reservation_rejects_every_transition(self, state, action):
        reservation = make_reservation(state)

        with pytest.raises(InvalidStateTransition):
            getattr(reservation, action)()

    def test_a_confirmed_purchase_cannot_be_reversed(self):
        """The business rule stated in the brief, spelled out as its own test."""
        confirmed = make_reservation(ReservationState.CONFIRMED)

        with pytest.raises(InvalidStateTransition):
            confirmed.cancel()


class TestImmutability:
    def test_a_transition_returns_a_new_object_and_leaves_the_original_alone(self):
        active = make_reservation()

        confirmed = active.confirm()

        assert confirmed is not active
        assert active.state is ReservationState.ACTIVE
        assert confirmed.state is ReservationState.CONFIRMED

    def test_fields_cannot_be_reassigned(self):
        with pytest.raises(FrozenInstanceError):
            make_reservation().state = ReservationState.CONFIRMED


class TestExpiryWindow:
    @pytest.mark.parametrize(
        ("elapsed_seconds", "expired"),
        [
            (0, False),
            (119, False),
            (120, True),
            (121, True),
        ],
    )
    def test_expiry_is_decided_by_the_deadline_not_a_timer(self, elapsed_seconds, expired):
        clock = FakeClock(BASE)
        reservation = Reservation.create(
            id="r1", product_id="p1", quantity=1, now=clock.now(), hold=HOLD
        )

        clock.advance(elapsed_seconds)

        assert reservation.is_expired_at(clock.now()) is expired

    @pytest.mark.parametrize("state", TERMINAL_STATES)
    def test_a_reservation_that_already_ended_is_never_reported_as_expiring(self, state):
        reservation = make_reservation(state)

        assert reservation.is_expired_at(BASE + timedelta(days=365)) is False


class TestProduct:
    def test_holds_its_catalogue_details(self):
        product = Product(id="p1", name="Limited Sneaker", total_stock=10)

        assert product.total_stock == 10

    def test_rejects_negative_stock(self):
        with pytest.raises(InvalidStock):
            Product(id="p1", name="Limited Sneaker", total_stock=-1)


class TestStockLevel:
    @pytest.mark.parametrize(
        ("total", "confirmed", "reserved", "available"),
        [
            (10, 0, 0, 10),
            (10, 3, 2, 5),
            (1, 0, 1, 0),
            (5, 5, 0, 0),
        ],
    )
    def test_available_is_total_minus_confirmed_minus_reserved(
        self, total, confirmed, reserved, available
    ):
        level = StockLevel(total=total, confirmed=confirmed, reserved=reserved)

        assert level.available == available


class TestFakeClock:
    def test_time_only_moves_when_advanced(self):
        clock = FakeClock(BASE)

        assert clock.now() == BASE
        clock.advance(90)
        assert clock.now() == BASE + timedelta(seconds=90)


class TestSystemClock:
    def test_reports_timezone_aware_utc(self):
        """A naive datetime here would make every expiry comparison a landmine."""
        now = SystemClock().now()

        assert now.tzinfo is UTC
