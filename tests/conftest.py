"""Shared fixtures.

The service is always built with a `FakeClock`, so no test in this suite depends
on real time passing.
"""

from __future__ import annotations

import pytest

from inventory.repository.memory import InMemoryInventoryRepository
from inventory.service.reservations import ReservationService
from tests.fakes import FakeClock


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def repository() -> InMemoryInventoryRepository:
    return InMemoryInventoryRepository()


@pytest.fixture
def service(repository: InMemoryInventoryRepository, clock: FakeClock) -> ReservationService:
    return ReservationService(repository=repository, clock=clock)
