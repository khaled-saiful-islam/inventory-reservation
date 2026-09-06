# Step 4 — Level 3: Concurrency

**Goal:** stock = 1, 500 simultaneous requests, exactly 1 success and 499
failures. And to arrive there by watching the bug happen first.

The locking rationale, the alternatives, and the measurements live in
[ADR-001](../adr/001-per-product-locking.md). This file is the narrative of the
step.

## What was created

| File | Change |
|---|---|
| `tests/test_concurrency.py` | 5 tests + the `preempt_aggressively` fixture |
| `repository/base.py` | `lock_product` added to the port |
| `repository/memory.py` | one `threading.Lock` per product, plus a registry guard |
| `service/reservations.py` | every stock-touching operation wrapped in the product lock |
| `tests/test_repository.py` | 4 more port-contract tests |
| `docs/adr/001-per-product-locking.md` | the decision record |

## The bug, before the fix

The concurrency tests were written and run against Step 3's code, which had no
locking at all. Ten consecutive runs of the headline test, stock of 1:

```
$ uv run pytest tests/test_concurrency.py::TestReserveUnderContention -k last_item
E  assert 6 == 1
E  assert 2 == 1
E  assert 2 == 1
E  assert 2 == 1
E  assert 4 == 1
E  assert 5 == 1
E  assert 9 == 1
E  assert 2 == 1
E  assert 2 == 1
```

One item in stock, sold between two and nine times. With stock of 50, one run
sold 60. This is the failure the whole step exists to remove, and it is in the
git history rather than described from memory.

## Why the barrier matters

```python
def race(action, contenders=CONTENDERS):
    barrier = threading.Barrier(contenders)

    def run(index):
        barrier.wait(timeout=BARRIER_TIMEOUT)
        return action(index)

    with ThreadPoolExecutor(max_workers=contenders) as pool:
        futures = [pool.submit(run, index) for index in range(contenders)]
        return Counter(future.result() for future in futures)
```

A thread pool given 500 jobs does not run 500 things at the same instant -- the
first thread often finishes before the last one starts. A test written that way
passes against broken code and proves nothing. Every thread here parks at the
barrier until all 500 have arrived, then they are released together.

`BARRIER_TIMEOUT` means a barrier that never fills fails the test instead of
hanging the suite.

## The finding worth reporting

`test_a_hold_can_only_be_confirmed_once` **passed** against the unlocked code --
15 runs out of 15. `confirm` has the same read-then-write shape as `reserve`, so
that was suspicious rather than reassuring.

The difference is `uuid4()`. It calls `os.urandom`, which releases the GIL inside
`reserve`'s critical section, so another thread reliably interleaves. `confirm`
contains no blocking call, so the read-transition-write usually completes inside
one 5ms time slice.

Shrinking CPython's switch interval settles it:

```
switchinterval=5e-3   confirm successes per run: [1, 1, 1, 1, 1]
switchinterval=1e-6   confirm successes per run: [1, 2, 2, 3, 3]
```

Two or three confirmations of the same hold means charging one customer two or
three times.

So the suite pins the interval for the duration of these tests:

```python
@pytest.fixture(autouse=True)
def preempt_aggressively():
    original = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(original)
```

This is not there to make tests pass. It is there to make them **fail** when the
locking is wrong. Adding it took the RED from 3 failures to 4.

## The fix

One mutex per product, held across the whole decide-and-write sequence:

```python
with self._repository.lock_product(product_id):
    available = self._current_level(product_id).available
    if quantity > available:
        raise InsufficientStock(product_id, requested=quantity, available=available)
    reservation = Reservation.create(...)
    self._repository.add_reservation(reservation)
    return reservation
```

Not one global lock -- reserving a sneaker must not block someone reserving a
jacket. Not optimistic retry -- under a flash sale, 499 of 500 callers would
collide and retry, which is more work than waiting. ADR-001 has the full
reasoning for both.

`confirm`, `cancel`, and `get_reservation` route through `_apply`, which reads
the reservation once outside the lock only to learn which product to lock, then
re-reads and acts inside it. A reservation never changes product, so that first
read cannot go stale in a way that matters.

`get_reservation` passes `_no_change` as its transition. A read still needs to
settle a lapsed hold under the lock, and routing it through the same helper is
what stops there being a second, nearly-identical code path.

## The five tests

| Test | Asserts |
|---|---|
| 500 race for 1 item | 1 success, 499 refusals, available 0 |
| 500 race for 50 items | exactly 50 successes, available 0 |
| 500 race across 2 products | exactly 1 success each -- contention on one does not corrupt the other |
| 500 confirm the same hold | exactly 1 success -- nobody is charged twice |
| 500 reserve-then-cancel | stock ends whole at 10, whatever order they interleave in |

## Verification

Before the fix:

```
$ uv run pytest tests/test_concurrency.py
4 failed, 1 passed in 0.51s
E  assert 5 == 1      # 500 racing for one item
E  assert 53 == 50    # 500 racing for fifty
E  assert 2 == 1      # two products
E  assert 3 == 1      # double confirm
```

After:

```
$ uv run pytest
80 passed in 0.44s

$ uv run pytest --cov
src/inventory/repository/memory.py       44   0   100%
src/inventory/service/reservations.py    67   0   100%
TOTAL                                   212   0   100%
```

A concurrency test that passes once has proved very little, so the suite was run
twelve times end to end:

```
clean runs: 12 / 12   failures: 0
```

## Known limitation, stated not hidden

The guarantee is per process. Two uvicorn workers would each hold their own
repository and their own mutexes, and overselling comes straight back. `make run`
pins `--workers 1`. ADR-001 describes what moving the invariant into a shared
store would involve, and why the port is typed the way it is so that the service
would not have to change.
