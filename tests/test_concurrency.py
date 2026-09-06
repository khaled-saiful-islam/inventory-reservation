"""Level 3: correctness when everybody arrives at once.

The tests in the other files call the service one operation at a time, and they
all pass. That proves the business rules are right; it proves nothing about what
happens under a flash sale.

Every test here uses `race()`, which holds all its threads at a barrier and
releases them together. Without the barrier the threads would trickle through the
pool one at a time and the test would pass against code that is plainly broken.
"""

from __future__ import annotations

import sys
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest

from inventory.domain.errors import InsufficientStock, InvalidStateTransition

CONTENDERS = 500

# Generous. A breached barrier fails the test instead of hanging the suite.
BARRIER_TIMEOUT = 30.0

OK = "ok"
REFUSED = "refused"


@pytest.fixture(autouse=True)
def preempt_aggressively():
    """Make CPython switch threads far more often than it normally would.

    At the default 5ms switch interval a read-modify-write containing no
    blocking call usually finishes inside one time slice, so a genuine race
    reads as passing code. Measured on this repo before locking existed:

        switchinterval=5e-3   confirm successes per run: [1, 1, 1, 1, 1]
        switchinterval=1e-6   confirm successes per run: [1, 2, 2, 3, 3]

    Shrinking the interval is what turns "this test happened to pass" into
    "this test fails when the locking is wrong". See docs/adr/001.
    """
    original = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(original)


def race(action: Callable[[int], str], contenders: int = CONTENDERS) -> Counter[str]:
    """Run `action` on `contenders` threads that all start at the same instant.

    The barrier is the whole point. Threads that merely run "concurrently" tend
    to serialise by accident; threads released from a barrier collide.
    """
    barrier = threading.Barrier(contenders)

    def run(index: int) -> str:
        barrier.wait(timeout=BARRIER_TIMEOUT)
        return action(index)

    with ThreadPoolExecutor(max_workers=contenders) as pool:
        futures = [pool.submit(run, index) for index in range(contenders)]
        return Counter(future.result() for future in futures)


class TestReserveUnderContention:
    def test_only_one_caller_wins_when_everyone_wants_the_last_item(self, service):
        """Stock = 1, 500 simultaneous requests: 1 success, 499 failures."""
        service.create_product(product_id="p1", name="Sneaker", total_stock=1)

        def attempt(_: int) -> str:
            try:
                service.reserve(product_id="p1", quantity=1)
                return OK
            except InsufficientStock:
                return REFUSED

        outcomes = race(attempt)

        assert outcomes[OK] == 1
        assert outcomes[REFUSED] == CONTENDERS - 1
        assert service.get_stock_level("p1").available == 0

    def test_stock_is_never_oversold_when_demand_far_exceeds_supply(self, service):
        service.create_product(product_id="p1", name="Sneaker", total_stock=50)

        def attempt(_: int) -> str:
            try:
                service.reserve(product_id="p1", quantity=1)
                return OK
            except InsufficientStock:
                return REFUSED

        outcomes = race(attempt)

        assert outcomes[OK] == 50
        level = service.get_stock_level("p1")
        assert level.reserved == 50
        assert level.available == 0

    def test_two_products_are_reserved_correctly_at_the_same_time(self, service):
        """Contention on one product must not corrupt another."""
        service.create_product(product_id="p1", name="Sneaker", total_stock=1)
        service.create_product(product_id="p2", name="Jacket", total_stock=1)

        def attempt(index: int) -> str:
            product_id = "p1" if index % 2 == 0 else "p2"
            try:
                service.reserve(product_id=product_id, quantity=1)
                return f"{OK}:{product_id}"
            except InsufficientStock:
                return REFUSED

        outcomes = race(attempt)

        assert outcomes[f"{OK}:p1"] == 1
        assert outcomes[f"{OK}:p2"] == 1
        assert service.get_stock_level("p1").available == 0
        assert service.get_stock_level("p2").available == 0


class TestConfirmUnderContention:
    def test_a_hold_can_only_be_confirmed_once_however_many_callers_try(self, service):
        """A double-confirm would mean charging the same customer twice."""
        service.create_product(product_id="p1", name="Sneaker", total_stock=1)
        reservation = service.reserve(product_id="p1", quantity=1)

        def attempt(_: int) -> str:
            try:
                service.confirm(reservation.id)
                return OK
            except InvalidStateTransition:
                return REFUSED

        outcomes = race(attempt)

        assert outcomes[OK] == 1
        assert outcomes[REFUSED] == CONTENDERS - 1
        assert service.get_stock_level("p1").confirmed == 1


class TestStockIsConserved:
    def test_reserving_and_releasing_in_a_storm_leaves_the_stock_intact(self, service):
        """Whatever order these interleave in, the pool must end up whole."""
        service.create_product(product_id="p1", name="Sneaker", total_stock=10)

        def attempt(_: int) -> str:
            try:
                reservation = service.reserve(product_id="p1", quantity=1)
            except InsufficientStock:
                return REFUSED
            service.cancel(reservation.id)
            return OK

        race(attempt)

        level = service.get_stock_level("p1")
        assert level.reserved == 0
        assert level.confirmed == 0
        assert level.available == 10
