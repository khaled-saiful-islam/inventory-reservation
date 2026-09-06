# Inventory Reservation System — repository map

A backend service that holds limited stock for a short window so that a flash
sale never oversells, even when hundreds of requests arrive at the same instant.

Read this file first. It describes how the repository is laid out and which rules
are non-negotiable. Detailed reasoning lives in `docs/`.

## The one invariant

```
available = total_stock - confirmed - active_reservations
```

Every operation preserves it. `available` must never go negative. If a change
cannot be shown to preserve this, it is wrong.

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
2. **Single process only.** Locks are in-process. Never run uvicorn with more than
   one worker.
3. **Availability is computed in one place.** Do not recalculate it inline anywhere else.
4. **Domain objects are immutable.** State changes return a new object rather than
   mutating in place.
5. **Routes do not contain `try/except`.** Domain errors are mapped to HTTP status
   codes centrally in `api/errors.py`.

## Docs

- `docs/steps/` — what changed at each step of the build, in order.
- `docs/adr/` — the design decisions, with the alternatives that were rejected.
- `AI_DISCLOSURE.md` — how AI tooling was used on this project.

## Commands

```bash
make install    # install dependencies
make run        # start the API on :8000
make test       # run the suite
make check      # lint + test
make load-test  # 500 concurrent requests against a 1-item product
```
