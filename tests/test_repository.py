"""The storage port's own contract.

Most of the repository is exercised through the service. These are the cases the
service cannot reach, because it always reads a record before writing it -- but
the port promises the behaviour, so the port is tested for it.
"""

from __future__ import annotations

import pytest

from inventory.domain.errors import ProductNotFound, ReservationNotFound
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


class TestUnknownProduct:
    def test_fetching_an_unknown_product_is_refused(self, repository):
        """Unreachable from the service now -- `lock_product` rejects first -- but
        the port still promises it, so the port is still tested for it."""
        with pytest.raises(ProductNotFound):
            repository.get_product("nope")

    def test_locking_an_unknown_product_is_refused(self, repository):
        with pytest.raises(ProductNotFound):
            repository.lock_product("nope")


class TestPerProductLocks:
    def test_each_product_gets_its_own_mutex(self, repository, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=1)
        service.create_product(product_id="p2", name="Jacket", total_stock=1)

        assert repository.lock_product("p1") is not repository.lock_product("p2")

    def test_the_same_product_always_yields_the_same_mutex(self, repository, service):
        """Two callers must contend on one lock, not on two copies of one."""
        service.create_product(product_id="p1", name="Sneaker", total_stock=1)

        assert repository.lock_product("p1") is repository.lock_product("p1")
