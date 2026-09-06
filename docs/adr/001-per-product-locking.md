# ADR-001: One lock per product

**Status:** accepted
**Date:** 2026-09-06

## Context

`reserve` reads available stock, decides, and then writes:

```python
available = self._current_level(product_id).available   # read
if quantity > available:
    raise InsufficientStock(...)
self._repository.add_reservation(reservation)            # write
```

Nothing stops two threads both passing the check before either writes. Measured
on this code before any locking existed -- stock of 1, 500 threads released from
a barrier together, ten consecutive runs:

```
successful reservations: 6, 2, 2, 2, 4, 5, 9, 2, 2, 2
```

Every run oversold. With stock of 50, one run sold 60.

The same read-then-write shape exists in `confirm` and `cancel`. That race is
harder to observe -- see "The race the GIL hides" below.

## Decision

The repository hands out **one mutex per product**, and the service holds it
across the whole decide-and-write sequence of every operation that touches stock.

```python
def lock_product(self, product_id: str) -> threading.Lock:
    with self._registry:
        try:
            return self._locks[product_id]
        except KeyError:
            raise ProductNotFound(...) from None
```

```python
with self._repository.lock_product(product_id):
    available = self._current_level(product_id).available
    if quantity > available:
        raise InsufficientStock(...)
    self._repository.add_reservation(reservation)
```

Four properties make this safe:

1. **The lock spans the decision and the write.** No window between them.
2. **The expiry sweep is inside it.** `_current_level` sweeps lapsed holds before
   counting, so reclaiming stock cannot race with consuming it.
3. **The service never holds two locks.** There is no lock ordering to get wrong,
   so there is no deadlock to reason about.
4. **The registry lock is held for a dictionary lookup only**, never across
   business logic, so it is not a hidden global bottleneck.

`confirm`, `cancel`, and `get_reservation` all route through one helper that
reads the reservation once outside the lock -- purely to learn which product to
lock, and a reservation never changes product -- then re-reads and acts inside it.

## The race the GIL hides

The `confirm` race did not reproduce at CPython's default 5ms switch interval.
`reserve` contains `uuid4()`, which calls `os.urandom` and releases the GIL
mid-critical-section, so its race is reliable. `confirm` contains no blocking
call, so the read-transition-write usually completes inside a single time slice.

Measured directly:

```
switchinterval=5e-3   confirm successes per run: [1, 1, 1, 1, 1]
switchinterval=1e-6   confirm successes per run: [1, 2, 2, 3, 3]
```

A double confirm means charging one customer twice. A bug that depends on the
interpreter's scheduling luck is still a bug: it becomes reachable the moment a
database round-trip lands between the read and the write, or on a free-threaded
build with no GIL at all.

So the concurrency tests set `sys.setswitchinterval(1e-6)` for their duration.
This is not a trick to make tests pass -- it is what makes them **fail** when the
locking is wrong. Without it, `test_a_hold_can_only_be_confirmed_once` passed 15
times out of 15 against code that was provably broken.

## Alternatives considered

### One global lock

Simplest to write and equally correct. Rejected because it serialises the entire
catalogue: reserving a sneaker would block someone reserving a jacket. A flash
sale is exactly the situation where that throughput matters, and the cost of
avoiding it is one dictionary.

### Optimistic concurrency with a version number and retry

Read a version, write conditionally, retry on conflict. This is the right answer
when writes are distributed or contention is low, because it needs no shared
mutex.

Rejected here for two reasons. Under a flash sale, contention is not low -- 499
of 500 callers would collide and retry, which is more work than waiting on a
mutex, with worse tail latency. And because the store is a local dictionary,
there is no cross-process boundary that makes a mutex impossible in the first
place.

### A background thread that expires holds on a timer

Rejected in Step 3, before locking existed, and this ADR is why the decision
held. A timer firing at the 120-second mark would mutate stock from a thread that
holds no product lock, or would have to take one -- introducing a second lock
holder with its own ordering rules. Sweeping on the calling thread inside the
existing lock means expiry cannot race with a reservation, because it is part of
the reservation.

## Consequences

**What this buys.** Overselling is impossible within the process. `available`
cannot go negative. A hold can be confirmed exactly once. Products do not contend
with each other.

**What it does not buy.** The guarantee is *per process*. Two uvicorn workers
would each hold a separate `InMemoryInventoryRepository` and a separate set of
mutexes, and overselling returns immediately. `make run` therefore pins
`--workers 1`, and this is stated in the README rather than left as a footgun.

Scaling horizontally means moving the invariant to a store that all processes
share. The service would not change -- `lock_product` is typed as a context
manager precisely so a SQL implementation can return a transaction holding
`SELECT ... FOR UPDATE`, or a Redis implementation can return a Lua-script guard.
That substitution is the reason the port exists.

**A smaller cost.** Every product carries a `threading.Lock` for the process
lifetime. Nothing evicts them. For a bounded catalogue this is noise; a real
system with millions of SKUs would need a striped fixed-size lock array instead
of one lock per key.
