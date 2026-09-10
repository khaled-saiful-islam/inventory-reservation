# Inventory Reservation System

A backend service that holds limited stock for a short window so a flash sale
cannot oversell, even when hundreds of requests arrive at the same instant.

> AI tool usage on this project is disclosed in [AI_DISCLOSURE.md](./AI_DISCLOSURE.md).

```
stock = 1  ·  500 simultaneous requests  ·  1 reservation created  ·  499 refused  ·  0 oversold
```

---

## Quick start

```bash
make install     # uv sync
make test        # 109 tests, ~0.9s
```

Then either of these, each of which starts and stops its own server:

```bash
make demo        # 14-step API walkthrough, every status code asserted
make load-test   # 500 concurrent HTTP requests, three scenarios
```

To run the API yourself:

```bash
make run         # http://localhost:8000  ·  interactive docs at /docs
```

No database, no Redis, no external services. Python 3.12 is pinned in
`.python-version`, so `uv sync` gives you the interpreter this was tested on.

`make check` runs lint and tests together. Both proof scripts exit non-zero on
failure, so they work as gates too.

<details>
<summary>Without make, or with Docker</summary>

```bash
uv sync
uv run pytest
uv run uvicorn inventory.api.main:app --workers 1
```

```bash
docker compose up --build       # http://localhost:8200
docker compose down
```

Or with the make targets, which also wait for the healthcheck:

```bash
make docker-up        # build, start, wait until healthy
make docker-verify    # run the demo and load test against the container
make docker-down      # stop and remove the container and its network
```

The compose file is deliberately self-contained, so it cannot disturb anything
else already running on the machine:

- **Its own compose project** (`name: inventory-reservation`) and its own
  bridge network (`inventory-reservation-net`) -- nothing is shared.
- **Port 8200, not 8000.** 8000 is a common default and this stack should never
  fight another project for it. Override with `INVENTORY_PORT=9500 make docker-up`.
- **Bound to `127.0.0.1`**, so it is not reachable from the local network.
- **No volumes and no bind mounts.** Inventory lives in process memory by
  design, so `docker compose down` leaves nothing behind.
- **One worker**, for the reason in [Limitations](#limitations).
</details>

---

## The problem

A flash sale puts hundreds of people on the same product at the same second.
The naive implementation reads the stock, decides, and then writes:

```python
available = total - confirmed - reserved     # read
if quantity > available:
    raise InsufficientStock(...)
store(reservation)                           # write
```

Nothing stops two callers both passing the check before either writes. Measured
on this codebase before locking existed -- one item in stock, 500 threads
released together, ten consecutive runs:

```
successful reservations: 6, 2, 2, 2, 4, 5, 9, 2, 2, 2
```

Every run oversold. With 50 in stock, one run sold 60.

## The invariant

```
available = total_stock - confirmed_sales - active_reservations
```

Every operation preserves it, and `available` is never allowed to go negative.
It is computed in exactly one place ([`domain/models.py`](src/inventory/domain/models.py)),
derived from the reservations themselves rather than tracked as a counter -- a
counter is a second copy of a fact, and a counter that drifts is precisely how a
system oversells.

## Locking strategy

Full reasoning, alternatives and measurements: **[ADR-001](docs/adr/001-per-product-locking.md)**.
The short version:

**One mutex per product, held across the whole decide-and-write sequence.**

```python
with self._repository.lock_product(product_id):
    available = self._current_level(product_id).available    # sweeps lapsed holds first
    if quantity > available:
        raise InsufficientStock(product_id, requested=quantity, available=available)
    reservation = Reservation.create(...)
    self._repository.add_reservation(reservation)
    return reservation
```

Four properties make it safe:

1. **The lock spans the decision and the write.** There is no window between them.
2. **Expiry happens inside it.** Lapsed holds are reclaimed by the same thread
   under the same lock, so releasing stock cannot race with consuming it.
3. **The service never holds two locks at once.** There is no lock ordering to
   get wrong, so there is no deadlock to reason about.
4. **The lock registry is held for a dictionary lookup only**, never across
   business logic, so it is not a hidden global bottleneck.

`confirm`, `cancel`, and `get_reservation` route through one helper that reads a
reservation outside the lock purely to learn which product to lock -- a
reservation never changes product -- then re-reads and acts inside it.

**Rejected: one global lock.** Equally correct, simpler, and it serialises the
whole catalogue. Reserving a sneaker would block someone reserving a jacket,
which is the exact throughput that matters in a flash sale. The cost of avoiding
it is one dictionary.

**Rejected: optimistic concurrency with retry.** The right answer when writes are
distributed or contention is low. Under a flash sale contention is not low --
499 of 500 callers would collide and retry, which is more work than waiting on a
mutex, with worse tail latency.

**Rejected: a background thread that expires holds on a timer.** It would mutate
stock from a thread holding no product lock, introducing a second lock holder
with its own ordering rules -- a new concurrency problem stacked on the one being
solved. Expiry is decided at read time instead, so it is part of the reservation
rather than a race against it.

## Proof

Run these yourself; both exit non-zero on failure.

```
$ make load-test

  last item: stock 1, 500 concurrent requests
    201 Created              1   (expected 1)
    409 Conflict           499   (expected 499)
    unexpected status        0   (expected 0)
    OVERSOLD                 0   <-- must be 0
    elapsed               0.75s
    PASS

  limited batch: stock 50, 500 concurrent requests
    201 Created             50   (expected 50)
    409 Conflict           450   (expected 450)
    OVERSOLD                 0   <-- must be 0
    PASS

  double charge: 1 hold, 500 concurrent confirmations
    200 OK                   1   (expected 1)
    units confirmed          1   (expected 1)
    PASS

RESULT: PASS - every scenario sold exactly its stock, and no hold was paid twice
```

```
$ make test
109 passed in 0.88s

$ make test-cov
TOTAL   324   0   100%
```

### Which test actually proves the locking

Worth being precise about, because the load test looks more convincing than it is.

Before shipping `scripts/load_test.py`, `lock_product` was replaced with
`contextlib.nullcontext()` -- every lock in the system removed -- and the script
was run again. **It still reported PASS.** An in-memory critical section takes
microseconds while an HTTP round trip takes milliseconds, so two requests almost
never sit inside `reserve` at the same instant. Widening FastAPI's threadpool
from 40 to 500 changed nothing.

| | `tests/test_concurrency.py` | `scripts/load_test.py` |
|---|---|---|
| Calls | the service, in-process | the deployed HTTP stack |
| Released together | `threading.Barrier` | `asyncio.Barrier` |
| GIL switch interval | shortened to 1e-6 | default |
| **Against unlocked code** | **fails every run** | passes |
| Proves | the locking is correct | the stack behaves, statuses are right |

The in-process test is the race detector; the load test is the end-to-end
demonstration and an API regression guard. Both are worth having. Forcing the
race over HTTP would have meant injecting a delay into the critical section
behind an environment variable -- shipping test scaffolding inside production
code so a test looks stronger than it is. That was rejected in favour of saying
which test is load-bearing.

## API

Interactive docs at `/docs` once running.

| Method | Path | Success | Failures |
|---|---|---|---|
| `GET` | `/health` | 200 | — |
| `POST` | `/products` | 201 | 409 duplicate · 422 invalid body |
| `GET` | `/products/{id}` | 200 | 404 unknown |
| `POST` | `/reservations` | 201 | 404 unknown product · 409 insufficient stock · 422 invalid body |
| `GET` | `/reservations/{id}` | 200 | 404 unknown |
| `POST` | `/reservations/{id}/confirm` | 200 | 404 unknown · 409 already ended |
| `POST` | `/reservations/{id}/cancel` | 200 | 404 unknown · 409 already ended |

One error envelope everywhere, including FastAPI's own validation failures:

```json
{"error": {"code": "insufficient_stock", "message": "Product sneaker has 0 available, 1 requested"}}
```

`code` is stable and safe to branch on; `message` is for humans and may be
reworded. No route contains a `try/except` -- one table maps domain errors to
status codes in [`api/errors.py`](src/inventory/api/errors.py).

```bash
curl -X POST localhost:8000/products \
  -H 'Content-Type: application/json' \
  -d '{"id":"sneaker","name":"Limited Sneaker","total_stock":1}'

curl -X POST localhost:8000/reservations \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"sneaker","quantity":1}'

curl -X POST localhost:8000/reservations/<id>/confirm
```

## Reservation lifecycle

```
                 confirm()  ->  CONFIRMED   final
   ACTIVE  ----   cancel()  ->  CANCELLED   final
                  expire()  ->  EXPIRED     final
```

`ACTIVE` is the only state that can change; the other three are final. That one
rule makes *"confirmed purchases cannot be reversed"* true by construction rather
than by a scattered `if`, and it is what makes "you cannot pay for a hold that
ran out" cost zero extra lines -- a lapsed hold is already `EXPIRED` by the time
`confirm()` is attempted, so the domain refuses it.

**Hold time is two minutes**, and expiry is lazy. There is no timer and no
background thread: a hold becomes expired the first time anybody looks at it
after its deadline, and every path that reads stock sweeps first. No caller can
observe the difference; only the internal bookkeeping is deferred.

## Structure

```
src/inventory/
├── domain/       models, state machine, errors, Clock     depends on nothing
├── repository/   storage port + in-memory implementation  depends on domain
├── service/      business rules                           depends on domain, repository
└── api/          FastAPI routes, schemas, error mapping   depends on domain, service
```

Dependencies point downward only. `domain/` imports nothing outside the standard
library, so the core rules are testable with no web server and no storage.

The service holds an `InventoryRepository`
([`repository/base.py`](src/inventory/repository/base.py)) and has never seen the
in-memory class. `lock_product` is typed as a context manager rather than a
`threading.Lock` for the same reason: a SQL-backed store would return a
transaction holding `SELECT ... FOR UPDATE`, and neither the service nor any
service-level test would change.

## Tests

```
tests/test_domain.py         33   state machine, immutability, stock arithmetic
tests/test_reservations.py   17   Level 1 -- reserve against available stock
tests/test_lifecycle.py      18   Level 2 -- confirm, cancel, two-minute expiry
tests/test_concurrency.py     5   Level 3 -- 500 threads, barrier-released
tests/test_api.py            29   HTTP contract, error envelope, expiry over HTTP
tests/test_repository.py      7   storage port contract
                            ---
                            109   100% coverage, 0.88s
```

**No test calls `sleep()`.** Time is injected through a `Clock` protocol; tests
use a `FakeClock` and advance it by hand. That is why a suite covering a
two-minute expiry window runs in under a second instead of taking four minutes
and being flaky on a loaded CI box.

The concurrency tests shorten CPython's thread switch interval to 1e-6 for their
duration. This is not there to make them pass -- it is what makes them **fail**
when the locking is wrong. At the default 5ms, `test_a_hold_can_only_be_confirmed_once`
passed 15 runs out of 15 against provably broken code, because `confirm` contains
no blocking call and its read-modify-write finished inside a single time slice.
Measured:

```
switchinterval=5e-3   confirm successes per run: [1, 1, 1, 1, 1]
switchinterval=1e-6   confirm successes per run: [1, 2, 2, 3, 3]
```

Two confirmations of one hold means charging a customer twice.

## Limitations

**Single process only.** This is the important one. Inventory and its mutexes
live in process memory, so two uvicorn workers would each hold a separate copy of
both and overselling returns immediately. `make run` pins `--workers 1`, and the
OpenAPI description says so.

Scaling horizontally means moving the invariant into a store all processes share.
The service would not change -- that substitution is the reason the repository
port exists. Concretely:

```sql
UPDATE products SET reserved = reserved + :qty
 WHERE id = :id AND total_stock - confirmed - reserved >= :qty;
-- 0 rows affected means someone else took it
```

or a Redis Lua script doing the same check-and-decrement atomically.

**Nothing is persisted.** A restart loses all state. Deliberate: the brief
specifies in-memory inventory, and adding a database the problem does not need
would be scope creep.

**No authentication, rate limiting, or idempotency keys.** A production flash
sale wants all three -- particularly an idempotency key on `POST /reservations`,
so a client retrying a timed-out request does not take a second unit.

**Locks are never evicted.** Every product holds a `threading.Lock` for the
process lifetime. Noise for a bounded catalogue; a system with millions of SKUs
would want a fixed-size striped lock array instead of one lock per key.

**Availability is O(n) in a product's reservation count.** Derived rather than
counted, on purpose (see [the invariant](#the-invariant)). The list stays small
because rejected attempts are never stored, and in a real deployment the
arithmetic moves into a SQL `SUM`.

## With more time

- **Idempotency keys** on reservation creation -- the most valuable single
  addition, and the one a real flash sale would notice first.
- **A PostgreSQL repository**, to demonstrate the port substitution rather than
  only describing it, with the conditional `UPDATE` above as the invariant.
- **Structured logging and metrics** -- reservation rate, refusal rate, lock wait
  time. Lock wait time is the number that tells you when to shard the lock.
- **A partial-fill option**: today, asking for 5 units when 3 remain fails
  outright, because the brief says a request exceeding available stock must fail.
  Whether a customer would rather take 3 is a product decision, not a technical one.

## Documentation

- [`docs/adr/001-per-product-locking.md`](docs/adr/001-per-product-locking.md) —
  the locking decision, with the alternatives and the measurements behind it.
- [`docs/steps/`](docs/steps/) — one file per build step, in order, recording
  what changed and why. Includes the things that were tried and backed out.
- [`CLAUDE.md`](./CLAUDE.md) — repository map and the rules that must hold.
- [`AI_DISCLOSURE.md`](./AI_DISCLOSURE.md) — how AI tooling was used.
