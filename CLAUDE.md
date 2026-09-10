# Inventory Reservation System — repository map

Holds limited stock for two minutes so a flash sale cannot oversell, even when
hundreds of requests arrive at the same instant.

Read this first. It is the layout and the rules that must hold. Reasoning lives
in `docs/adr/`, build history in `docs/steps/`.

## The one invariant

```
available = total_stock - confirmed - active_reservations
```

Every operation preserves it, and `available` must never go negative. A change
that cannot be shown to preserve it is wrong.

## Layout

| Path | Layer | Depends on |
|---|---|---|
| `src/inventory/domain/` | Models, states, errors, clock | nothing |
| `src/inventory/repository/` | Storage + locking | domain |
| `src/inventory/service/` | Business rules | domain, repository |
| `src/inventory/api/` | FastAPI routes and schemas | domain, service |

Dependencies point downward only. The domain layer never imports FastAPI.

## Hard rules

1. **No `sleep()` in tests.** Time is injected through the `Clock` protocol; tests
   use `FakeClock` and advance it explicitly.
2. **Availability is computed in one place** (`StockLevel`). Never recalculate it
   inline.
3. **Domain objects are immutable.** A state change returns a new object.
4. **Locks span decide-and-write.** Never read availability outside the product
   lock and write inside it.
5. **Routes contain no `try/except`.** Domain errors map to status codes in
   `api/errors.py` — add a row to `STATUS_BY_ERROR`, not a handler in a route.
6. **API handlers stay `def`, never `async def`.** Sync handlers run in FastAPI's
   threadpool, which is what creates real contention. An `async def` with no
   `await` would serialise requests and make the load test meaningless.
7. **Single process only.** Locks are in-process; never more than one uvicorn
   worker. See ADR-001 for what horizontal scaling would require.
8. **Don't relax the concurrency tests' switch interval.** `preempt_aggressively`
   in `tests/test_concurrency.py` is what makes them fail when locking is wrong.

## Commands

```bash
make install       # install dependencies
make test          # run the suite
make check         # lint + tests
make run           # API on :8000, docs at /docs
make demo          # 14-step API walkthrough, statuses asserted
make load-test     # 500 concurrent HTTP requests

make docker-up     # container on :8200, isolated network, no volumes
make docker-verify # demo + load test against the container
make docker-down
```

## Docs

- `docs/adr/` — design decisions, with the rejected alternatives.
- `docs/steps/` — one file per build step, including what was backed out.
- `AI_DISCLOSURE.md` — how AI tooling was used.
