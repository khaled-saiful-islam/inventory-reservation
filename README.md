# Inventory Reservation System

Holds limited stock for two minutes so a flash sale cannot oversell, even when
hundreds of requests arrive at the same instant.

```
stock = 1  ·  500 simultaneous requests  ·  1 reservation  ·  499 refused  ·  0 oversold
```

Python 3.12 · FastAPI · 109 tests · 100% coverage.
AI tool usage is disclosed in [AI_DISCLOSURE.md](./AI_DISCLOSURE.md).

## Quick start

```bash
make install     # uv sync
make test        # 109 tests, ~1s
make demo        # 14-step API walkthrough, every status asserted
make load-test   # 500 concurrent HTTP requests
make run         # http://localhost:8000  ·  clickable API docs at /docs
```

`make demo` and `make load-test` start and stop their own server, and exit
non-zero on failure. No database, no Redis, nothing to configure.

<details>
<summary>Docker</summary>

```bash
make docker-up       # build, start, wait for healthy -> http://localhost:8200
make docker-verify   # demo + load test against the container
make docker-down
```

Self-contained so it cannot disturb anything else on the machine: its own
compose project and bridge network, port **8200** rather than 8000, bound to
`127.0.0.1`, and no volumes. Override with `INVENTORY_PORT=9500 make docker-up`.
</details>

## The problem

The naive implementation reads stock, decides, then writes — and nothing stops
two callers passing the check before either writes. Measured on this codebase
before locking existed: one item in stock, 500 threads, ten runs sold it
**6, 2, 2, 2, 4, 5, 9, 2, 2, 2** times.

The invariant every operation preserves, defined in one place and never allowed
to go negative:

```
available = total_stock - confirmed_sales - active_reservations
```

It is derived from the reservations themselves rather than tracked as a counter.
A counter is a second copy of a fact, and a counter that drifts is how a system
oversells.

## Locking strategy

Full reasoning and measurements: **[ADR-001](docs/adr/001-per-product-locking.md)**.

**One mutex per product, held across the whole decide-and-write sequence**
([`service/reservations.py:81`](src/inventory/service/reservations.py)):

```python
with self._repository.lock_product(product_id):
    available = self._current_level(product_id).available   # sweeps lapsed holds first
    if quantity > available:
        raise InsufficientStock(...)
    self._repository.add_reservation(reservation)
```

Two callers racing for the last item both reach the lock; one blocks for
microseconds and then reads the state the winner already wrote. There is no
window to lose a unit in.

- The lock spans the decision **and** the write.
- Expiry is swept inside it, so releasing stock cannot race with consuming it.
- The service never holds two locks, so there is no deadlock to reason about.

**Not one global lock** — that serialises the catalogue, so reserving a sneaker
would block someone reserving a jacket. **Not optimistic retry** — under a flash
sale 499 of 500 callers collide and retry, which is more work than waiting.
**Not a background expiry timer** — it would mutate stock outside the lock,
adding a second concurrency problem to the one being solved.

## API

Clickable docs at `/docs` once running.

| Method | Path | Success | Failures |
|---|---|---|---|
| `GET` | `/health` | 200 | — |
| `POST` | `/products` | 201 | 409 duplicate · 422 invalid |
| `GET` | `/products/{id}` | 200 | 404 |
| `POST` | `/reservations` | 201 | 404 · 409 insufficient stock · 422 |
| `GET` | `/reservations/{id}` | 200 | 404 |
| `POST` | `/reservations/{id}/confirm` | 200 | 404 · 409 already ended |
| `POST` | `/reservations/{id}/cancel` | 200 | 404 · 409 already ended |

One error envelope everywhere, including FastAPI's own validation failures. No
route contains a `try/except`; one table maps domain errors to status codes in
[`api/errors.py`](src/inventory/api/errors.py).

```json
{"error": {"code": "insufficient_stock", "message": "Product sneaker has 0 available, 1 requested"}}
```

## Lifecycle

```
              confirm() -> CONFIRMED   final
   ACTIVE --   cancel()  -> CANCELLED   final
               expire()  -> EXPIRED     final
```

`ACTIVE` is the only state that can change, which makes *"confirmed purchases
cannot be reversed"* true by construction. It also makes "you cannot pay for a
lapsed hold" free: expiry is settled before the transition, so the hold is
already `EXPIRED` and the domain refuses it.

The two-minute hold expires **lazily** — no timer, no background thread. A hold
becomes expired the first time anything looks at it after its deadline, and
every path that reads stock sweeps first.

## Structure

```
src/inventory/
├── domain/       models, state machine, errors, Clock     depends on nothing
├── repository/   storage port + in-memory implementation  depends on domain
├── service/      business rules                           depends on domain, repository
└── api/          FastAPI routes, schemas, error mapping   depends on domain, service
```

Dependencies point downward only; `domain/` imports nothing outside the standard
library. The service holds an `InventoryRepository` protocol and has never seen
the in-memory class. `lock_product` is typed as a context manager, not a
`threading.Lock`, so a SQL implementation can return a transaction holding
`SELECT ... FOR UPDATE` without the service changing.

## Tests

```
test_domain.py         33   state machine, immutability, stock arithmetic
test_reservations.py   17   Level 1 -- reserve against available stock
test_lifecycle.py      18   Level 2 -- confirm, cancel, two-minute expiry
test_concurrency.py     5   Level 3 -- 500 threads, barrier-released
test_api.py            29   HTTP contract, error envelope, expiry over HTTP
test_repository.py      7   storage port contract
                      ---
                      109   100% coverage, ~1s
```

**No test calls `sleep()`.** Time is injected through a `Clock` protocol and
advanced by hand, which is why a suite covering a two-minute window runs in a
second.

The concurrency tests shorten CPython's thread switch interval to `1e-6`. That is
not there to make them pass — it is what makes them **fail** when the locking is
wrong. At the 5ms default, the double-confirm test passed 15 runs out of 15
against provably broken code.

Worth knowing which test is load-bearing: `scripts/load_test.py` still reported
PASS with **every lock removed**, because an in-memory critical section is too
short to collide over HTTP. `tests/test_concurrency.py` is the race detector; the
load test is the end-to-end demonstration. Details in
[`docs/steps/06-proof-scripts.md`](docs/steps/06-proof-scripts.md).

## Limitations

- **Single process.** Inventory and its mutexes live in process memory, so two
  workers would each hold a separate copy and overselling returns. `--workers 1`
  is pinned. Scaling out means moving the invariant into a shared store —
  `UPDATE ... WHERE total_stock - confirmed - reserved >= :qty`, where 0 rows
  affected is the rejection. The repository port exists so the service would not
  change.
- **Nothing is persisted.** A restart is a clean slate; the brief specifies
  in-memory inventory.
- **No auth, rate limiting, or idempotency keys.** An idempotency key on
  `POST /reservations` is the first thing a real flash sale would want, so a
  client retrying a timed-out request does not take a second unit.
- **No partial fills.** Asking for 5 when 3 remain fails outright, per the brief.
- **Locks are never evicted.** Fine for a bounded catalogue; millions of SKUs
  would want a fixed-size striped lock array.

## Documentation

- [ADR-001](docs/adr/001-per-product-locking.md) — the locking decision, with
  alternatives and measurements.
- [`docs/steps/`](docs/steps/) — one file per build step, including what was
  tried and backed out.
- [AI_DISCLOSURE.md](./AI_DISCLOSURE.md) — how AI tooling was used.
