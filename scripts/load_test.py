#!/usr/bin/env python
"""Prove over real HTTP that the service does not oversell.

Starts the app on a free port, fires N requests that are released together, and
reports what the server actually did. Exits non-zero if a single unit was
oversold, so this is usable as a gate in CI.

    uv run python scripts/load_test.py
    uv run python scripts/load_test.py --url http://localhost:8000   # existing server

What this proves: the deployed stack -- uvicorn, FastAPI's worker threadpool,
the service, the locks -- sells exactly the stock that exists and no more, and
returns the right status code to every caller.

What this does NOT prove: that the locking is correct. Measured directly by
disabling `lock_product` and re-running, this script still reported PASS. An
in-memory critical section takes microseconds while an HTTP round trip takes
milliseconds, so requests almost never overlap *inside* `reserve`. Widening
FastAPI's threadpool from 40 to 500 did not change that.

The test that actually detects a missing lock is `tests/test_concurrency.py`,
which calls the service in-process behind a barrier with a shortened GIL switch
interval. Against unlocked code it fails every run; this script passes. Both are
needed, and it is worth knowing which one is load-bearing.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx

CREATED = 201
CONFLICT = 409
STARTUP_TIMEOUT = 20.0


@dataclass(frozen=True)
class Scenario:
    name: str
    stock: int
    requests: int


SCENARIOS = (
    Scenario(name="last item", stock=1, requests=500),
    Scenario(name="limited batch", stock=50, requests=500),
)

CONFIRM_REQUESTS = 500


@dataclass(frozen=True)
class Outcome:
    created: int
    conflict: int
    unexpected: int
    reserved: int
    confirmed: int
    available: int
    elapsed_seconds: float

    @property
    def oversold(self) -> int:
        return max(0, -self.available)

    @property
    def passed(self) -> bool:
        return self.oversold == 0 and self.unexpected == 0


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_until_healthy(url: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Server at {url} never became healthy")


async def run_confirm_storm(url: str) -> tuple[int, int]:
    """500 callers try to pay for the same hold. Exactly one may succeed.

    Two confirmations of one reservation means charging a customer twice.
    """
    product_id = "confirm-storm"
    limits = httpx.Limits(
        max_connections=CONFIRM_REQUESTS, max_keepalive_connections=CONFIRM_REQUESTS
    )

    async with httpx.AsyncClient(base_url=url, limits=limits, timeout=60.0) as client:
        await client.post(
            "/products",
            json={"id": product_id, "name": "Flash Sale Item", "total_stock": 1},
        )
        reservation = (
            await client.post(
                "/reservations", json={"product_id": product_id, "quantity": 1}
            )
        ).json()

        gate = asyncio.Barrier(CONFIRM_REQUESTS)

        async def attempt() -> int:
            await gate.wait()
            response = await client.post(f"/reservations/{reservation['id']}/confirm")
            return response.status_code

        statuses = await asyncio.gather(*(attempt() for _ in range(CONFIRM_REQUESTS)))
        product = (await client.get(f"/products/{product_id}")).json()

    return statuses.count(200), product["confirmed"]


async def run_scenario(url: str, scenario: Scenario, index: int) -> Outcome:
    product_id = f"flash-sale-{index}"
    limits = httpx.Limits(
        max_connections=scenario.requests,
        max_keepalive_connections=scenario.requests,
    )

    async with httpx.AsyncClient(base_url=url, limits=limits, timeout=60.0) as client:
        await client.post(
            "/products",
            json={"id": product_id, "name": "Flash Sale Item", "total_stock": scenario.stock},
        )

        # Every request parks here until all of them have arrived, so they hit
        # the server together instead of trickling through one at a time.
        gate = asyncio.Barrier(scenario.requests)

        async def attempt() -> int:
            await gate.wait()
            response = await client.post(
                "/reservations", json={"product_id": product_id, "quantity": 1}
            )
            return response.status_code

        started = time.perf_counter()
        statuses = await asyncio.gather(*(attempt() for _ in range(scenario.requests)))
        elapsed = time.perf_counter() - started

        product = (await client.get(f"/products/{product_id}")).json()

    return Outcome(
        created=statuses.count(CREATED),
        conflict=statuses.count(CONFLICT),
        unexpected=len([s for s in statuses if s not in (CREATED, CONFLICT)]),
        reserved=product["reserved"],
        confirmed=product["confirmed"],
        available=product["available"],
        elapsed_seconds=elapsed,
    )


def report(scenario: Scenario, outcome: Outcome) -> None:
    print(f"\n  {scenario.name}: stock {scenario.stock}, {scenario.requests} concurrent requests")
    print(f"    201 Created         {outcome.created:>6}   (expected {scenario.stock})")
    print(f"    409 Conflict        {outcome.conflict:>6}   "
          f"(expected {scenario.requests - scenario.stock})")
    print(f"    unexpected status   {outcome.unexpected:>6}   (expected 0)")
    print(f"    reserved            {outcome.reserved:>6}")
    print(f"    available           {outcome.available:>6}")
    print(f"    OVERSOLD            {outcome.oversold:>6}   <-- must be 0")
    print(f"    elapsed             {outcome.elapsed_seconds:>6.2f}s")
    print(f"    {'PASS' if outcome.passed else 'FAIL'}")


async def main_async(url: str) -> int:
    print(f"Inventory Reservation - no-overselling proof over HTTP\n  target: {url}")

    outcomes = []
    for index, scenario in enumerate(SCENARIOS):
        outcome = await run_scenario(url, scenario, index)
        report(scenario, outcome)
        outcomes.append((scenario, outcome))

    accepted, confirmed = await run_confirm_storm(url)
    print(f"\n  double charge: 1 hold, {CONFIRM_REQUESTS} concurrent confirmations")
    print(f"    200 OK              {accepted:>6}   (expected 1)")
    print(f"    units confirmed     {confirmed:>6}   (expected 1)")
    confirm_ok = accepted == 1 and confirmed == 1
    print(f"    {'PASS' if confirm_ok else 'FAIL'}")

    failures = [s.name for s, o in outcomes if not o.passed]
    sold = [(s, o) for s, o in outcomes if o.created != s.stock]

    print()
    if failures:
        print(f"RESULT: FAIL - oversold in: {', '.join(failures)}")
        return 1
    if sold:
        names = ", ".join(f"{s.name} sold {o.created} of {s.stock}" for s, o in sold)
        print(f"RESULT: FAIL - wrong number of reservations ({names})")
        return 1
    if not confirm_ok:
        print(f"RESULT: FAIL - one hold was confirmed {accepted} times")
        return 1
    print("RESULT: PASS - every scenario sold exactly its stock, and no hold was paid twice")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Target an already-running server instead of starting one")
    arguments = parser.parse_args()

    if arguments.url:
        wait_until_healthy(arguments.url)
        return asyncio.run(main_async(arguments.url))

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "inventory.api.main:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--workers", "1", "--log-level", "warning",
        ]
    )
    try:
        wait_until_healthy(url)
        return asyncio.run(main_async(url))
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
