# Step 1 — Domain Model

**Goal:** encode the rules of a reservation in plain Python, with no web server,
no storage, and no real clock involved. If these rules are wrong, everything
built on top of them is wrong.

## What was created

| File | Contents |
|---|---|
| `domain/errors.py` | `InventoryError` base + `InvalidQuantity`, `InvalidStock`, `InvalidStateTransition` |
| `domain/clock.py` | `Clock` protocol, `SystemClock` |
| `domain/models.py` | `ReservationState`, `Reservation`, `Product`, `StockLevel` |
| `tests/fakes.py` | `FakeClock` |
| `tests/test_domain.py` | 33 tests |

## The state machine

```
                 confirm()  -> CONFIRMED  (final)
   ACTIVE  ----   cancel()  -> CANCELLED  (final)
                  expire()  -> EXPIRED    (final)
```

`ACTIVE` is the only state that can change. All three others are final, which is
what makes "confirmed purchases cannot be reversed" true by construction rather
than by a scattered `if` statement. Any transition attempted from a terminal
state raises `InvalidStateTransition`.

That single rule covers nine cases, and the test suite asserts all nine:

```python
@pytest.mark.parametrize("state", TERMINAL_STATES)
@pytest.mark.parametrize("action", ["confirm", "cancel", "expire"])
def test_a_terminal_reservation_rejects_every_transition(self, state, action):
    with pytest.raises(InvalidStateTransition):
        getattr(make_reservation(state), action)()
```

## Four decisions worth explaining

### 1. Reservations are immutable

`Reservation` is a frozen dataclass. `confirm()` does not flip a field, it returns
a **new** `Reservation`:

```python
def _transition_to(self, target: ReservationState) -> Reservation:
    if self.state.is_terminal:
        raise InvalidStateTransition(self.id, self.state, target)
    return replace(self, state=target)
```

Why: under concurrency, mutable shared objects are the thing that bites. If two
threads hold the same reservation and one mutates it, the other is silently
looking at changed data. With frozen objects that cannot happen -- a thread's
reference always shows the state it read. It also makes the tests trivial: assert
the new object changed *and* the old one did not.

### 2. Time is injected, never read inline

`Clock` is a `Protocol` with one method. Production passes `SystemClock`; tests
pass `FakeClock` and move time by hand.

This is why the whole suite runs in 0.06 seconds while covering a two-minute
expiry window. The alternative -- `time.sleep(121)` -- would make the suite
slower than the feature it tests, and flaky on a loaded CI box.

### 3. Nothing expires on a timer

There is no background thread. `is_expired_at(now)` is a question, not an event:

```python
def is_expired_at(self, now: datetime) -> bool:
    return self.state is ReservationState.ACTIVE and now >= self.expires_at
```

A reservation becomes expired the moment someone looks at it after its deadline.
The service layer (Step 3) sweeps expired holds before it reads available stock.

Why not a timer thread: a timer that fires while another thread is mid-reservation
is a second concurrency problem to solve, on top of the one the challenge asks
for. Deciding expiry at read time means it is already inside the lock that
protects everything else, so it cannot race. The trade-off is that stock is
released lazily rather than at the exact second -- which no caller can observe,
because every path that reads stock sweeps first.

Note `now >= self.expires_at`: the deadline itself counts as expired. The tests
pin the boundary explicitly at 119s, 120s, and 121s so a future refactor cannot
quietly turn it into `>`.

### 4. Availability is derived, not counted

`Product` holds `total_stock` only. It does **not** carry `confirmed` and
`reserved` counters. `StockLevel` computes:

```python
available = total - confirmed - reserved
```

Why: two running counters can drift out of step with the reservations that are
supposed to back them, and a drifted counter is exactly how a system oversells.
Deriving the number from the reservations themselves means there is one source of
truth. At the scale this challenge describes the cost is irrelevant -- and in a
real deployment this arithmetic moves into a SQL statement anyway.

## Validation at the boundary

`__post_init__` rejects a quantity below 1 and negative stock. Because
`dataclasses.replace` re-runs `__post_init__`, the invariant also holds across
every state transition, not just at construction.

The API layer will validate the same things again through Pydantic. That
duplication is deliberate: the domain must be safe to use from anywhere, not
only from behind an HTTP handler.

## Verification

Tests were written first and failed for the right reason:

```
$ uv run pytest
ImportError while importing test module 'tests/test_domain.py'
E   ModuleNotFoundError: No module named 'inventory.domain.errors'
```

After implementing the three modules:

```
$ uv run pytest
33 passed in 0.06s

$ uv run pytest --cov
src/inventory/domain/clock.py     7      0   100%
src/inventory/domain/errors.py   14      0   100%
src/inventory/domain/models.py   55      0   100%
TOTAL                            76      0   100%

$ uv run ruff check src tests
All checks passed!
```

## Not built yet

No storage, no service, no HTTP, and no locking. `Reservation` does not know how
to find its `Product`, and nothing yet stops two callers reserving the same last
item -- that is Step 2 and Step 4.
