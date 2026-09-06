# Step 3 — Level 2: Reservation Lifecycle and Expiry

**Goal:** `confirm`, `cancel`, and the two-minute hold. Stock must come back on
its own when a hold lapses, and nobody may pay for a hold that already ran out.

## What was created

| File | Change |
|---|---|
| `repository/base.py` | `replace_reservation` added to the port |
| `repository/memory.py` | implemented, with a guard for an unknown id |
| `service/reservations.py` | `confirm`, `cancel`, `_apply`, `_current_level`, `_sweep_lapsed`, `_settle` |
| `tests/test_lifecycle.py` | 18 tests |
| `tests/test_repository.py` | 3 tests for the port's own contract |

## Expiry is lazy, and that is the design

There is no timer, no scheduler, and no background thread. A hold becomes expired
the first time anybody looks at it after its deadline:

```python
def _sweep_lapsed(self, product_id: str) -> None:
    now = self._clock.now()
    for reservation in self._repository.reservations_for_product(product_id):
        if reservation.is_expired_at(now):
            self._repository.replace_reservation(reservation.expire())
```

Two entry points sweep:

- `_current_level(product_id)` sweeps that product before counting stock, so
  `reserve` and `get_stock_level` always see reclaimed inventory.
- `_settle(reservation)` sweeps a single hold before `get_reservation`,
  `confirm`, or `cancel` touches it.

### Why not a background timer

A timer that fires at the 120-second mark would have to mutate stock while
another thread might be halfway through a reservation. That is a *second*
concurrency problem, stacked on top of the one the brief actually asks about, and
it would need its own lock ordering to stay correct.

Deciding expiry at read time means the sweep happens on the calling thread,
inside whatever critical section already protects that product (Step 4). It
cannot race with a reservation, because it *is* part of the reservation.

The trade-off: stock is reclaimed when someone asks, not on the exact second. No
caller can observe the difference, because every path that reads stock sweeps
first. What a caller sees is always correct; only the internal bookkeeping is
deferred.

### Why the sweep is the only place expiry is decided

`_current_level` sweeps and then counts. The counting step deliberately does
**not** also filter on deadlines:

```python
confirmed=self._quantity_in(reservations, ReservationState.CONFIRMED),
reserved=self._quantity_in(reservations, ReservationState.ACTIVE),
```

Filtering in both places would mean two rules that have to agree forever. One
mechanism, one place.

## "You cannot pay for a lapsed hold" costs zero lines

Both state changes go through one helper:

```python
def _apply(self, reservation_id, transition):
    reservation = self._settle(self._repository.get_reservation(reservation_id))
    updated = transition(reservation)
    self._repository.replace_reservation(updated)
    return updated

def confirm(self, reservation_id): return self._apply(reservation_id, Reservation.confirm)
def cancel(self, reservation_id):  return self._apply(reservation_id, Reservation.cancel)
```

Settle first, then transition. By the time `confirm()` is called on a lapsed
hold, its state is already `EXPIRED`, and the domain's terminal-state rule from
Step 1 refuses it. There is no `if expired:` branch anywhere in the service --
the guard written in Step 1 does the work.

Passing `Reservation.confirm` as an unbound method keeps `confirm` and `cancel`
to one line each and guarantees they cannot drift apart in how they settle,
store, or report.

## The boundary is pinned by tests, not by comments

The deadline itself counts as expired (`now >= expires_at`). Three tests hold
that still:

| Elapsed | Expected |
|---|---|
| 119s | hold still blocks stock |
| 120s | stock is back |
| 121s | stock is back, someone else can reserve |

And one test proves the sweep only touches what is actually due -- two holds
opened a minute apart expire a minute apart:

```python
early = service.reserve(product_id="p2", quantity=1)
clock.advance(60)
late = service.reserve(product_id="p2", quantity=1)

clock.advance(61)

assert service.get_reservation(early.id).state is ReservationState.EXPIRED
assert service.get_reservation(late.id).state is ReservationState.ACTIVE
```

Another checks the sweep is idempotent: reading an expired hold twice returns the
same object and does not release stock twice.

## Still no `sleep()`

Every test above covers a two-minute window. The file runs in 0.07 seconds
because `FakeClock` is advanced by hand. A suite that slept through its own
expiry window would take over four minutes and would be flaky on a loaded CI box.

## Verification

Tests first:

```
$ uv run pytest tests/test_lifecycle.py
17 failed, 1 passed in 0.06s
```

(One passed before any code existed: a hold blocking stock at 119 seconds is
also what a system with no expiry at all does. It becomes meaningful only next
to the 120-second case beside it.)

After implementing:

```
$ uv run pytest
71 passed in 0.07s

$ uv run pytest --cov
src/inventory/repository/memory.py       33   0   100%
src/inventory/service/reservations.py    59   0   100%
TOTAL                                   191   0   100%

$ uv run ruff check src tests
All checks passed!
```

## Not built yet

The read-then-write gap in `reserve` is still there. All three levels of business
logic now work correctly when called one at a time. Step 4 calls them 500 times
at once and shows that this is not the same thing.
