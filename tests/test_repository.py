"""The storage port's own contract.

Most of the repository is exercised through the service. These are the cases the
service cannot reach, because it always reads a record before writing it -- but
the port promises the behaviour, so the port is tested for it.
"""

from __future__ import annotations

import pytest

from inventory.domain.errors import ReservationNotFound
from inventory.domain.models import Reservation
from tests.test_domain import BASE, HOLD


class TestReplaceReservation:
    def test_replacing_an_unknown_reservation_is_refused(self, repository):
        stranger = Reservation.create(
            id="never-stored", product_id="p1", quantity=1, now=BASE, hold=HOLD
        )

        with pytest.raises(ReservationNotFound):
            repository.replace_reservation(stranger)


class TestReservationsForProduct:
    def test_a_product_with_no_holds_returns_an_empty_list(self, repository, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=1)

        assert repository.reservations_for_product("p1") == []

    def test_an_unknown_product_returns_an_empty_list_rather_than_raising(self, repository):
        """A read of nothing is not an error -- `get_product` is what validates ids."""
        assert repository.reservations_for_product("nope") == []
