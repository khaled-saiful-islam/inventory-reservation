# Step 2 — Level 1: Basic Reservation

**Goal:** the rule from the brief -- `stock = 1`, user A succeeds, user B fails --
with storage behind an interface so the business logic never touches a dictionary
directly.

## What was created

| File | Contents |
|---|---|
| `repository/base.py` | `InventoryRepository` protocol -- the storage port |
| `repository/memory.py` | `InMemoryInventoryRepository` -- the only implementation |
| `service/reservations.py` | `ReservationService` with `create_product`, `get_stock_level`, `get_reservation`, `reserve` |
| `domain/errors.py` | extended with `ProductNotFound`, `DuplicateProduct`, `ReservationNotFound`, `InsufficientStock` |
| `tests/conftest.py` | `clock`, `repository`, `service` fixtures |
| `tests/test_reservations.py` | 17 tests |

## Where each responsibility lives

```
ReservationService     decides whether a reservation is allowed
InventoryRepository    stores things and hands them back
Reservation / Product  enforce their own internal invariants
```

The service holds an `InventoryRepository`, which is a `Protocol`. It has never
seen `InMemoryInventoryRepository`. Moving to PostgreSQL means writing a second
class with the same five methods and changing one line of wiring -- no business
rule moves, and no test in `test_reservations.py` changes.

## The headline test

```python
def test_only_one_caller_can_take_the_last_item(self, service):
    service.create_product(product_id="p1", name="Sneaker", total_stock=1)

    first = service.reserve(product_id="p1", quantity=1)

    assert first.state is ReservationState.ACTIVE
    with pytest.raises(InsufficientStock):
        service.reserve(product_id="p1", quantity=1)
```

This passes now. It will still pass in Step 4 -- and it is **not** enough,
because it only ever calls `reserve` one at a time. Step 4 exists to show why.

## Three decisions worth explaining

### 1. Availability is counted on demand, not tracked

```python
def _stock_level(self, product: Product) -> StockLevel:
    reservations = self._repository.reservations_for_product(product.id)
    return StockLevel(
        total=product.total_stock,
        confirmed=self._quantity_in(reservations, ReservationState.CONFIRMED),
        reserved=self._quantity_in(reservations, ReservationState.ACTIVE),
    )
```

The obvious alternative is to keep `reserved` and `confirmed` as running counters
on the product and adjust them on every operation. That is O(1) instead of O(n),
and it is how this would be written if the numbers were large.

It was rejected because a counter is a second copy of a fact. If any code path
forgets to decrement it -- an expiry that does not fire, an early return, an
exception between two writes -- the counter and the reservations disagree, and
the system oversells while believing it did not. Deriving the number means that
class of bug cannot exist.

The cost is a scan of one product's reservations. `reservations_for_product` is
indexed by product id, so it never touches unrelated products, and the list stays
small because rejected attempts are never stored. If this became a real
bottleneck the arithmetic would move into a SQL `SUM`, not into a Python counter.

### 2. Rejected attempts leave no trace

`reserve` raises before creating anything. 499 failed attempts out of 500 add
nothing to storage. Only successful holds exist as objects, which is why the
derived count above stays cheap even under the load test in Step 6.

### 3. Quantity is checked twice, deliberately

`reserve` guards `quantity < 1` at the top, and `Reservation.__post_init__` guards
it again. The service check is there so `reserve` reads as a complete statement
of its own preconditions; the domain check is there so a `Reservation` built from
anywhere else is still valid. The API layer will add a third via Pydantic.

This is defence in depth at each boundary, not an accident. The alternative --
relying on the domain check alone -- happens to work today only because the
comparison `quantity > available` is harmless for zero and negative inputs. That
is the kind of correctness that survives until someone reorders two lines.

## Errors and what they carry

| Error | Raised when |
|---|---|
| `InvalidQuantity` | quantity below 1 |
| `ProductNotFound` | unknown product id |
| `DuplicateProduct` | product id already registered |
| `ReservationNotFound` | unknown reservation id |
| `InsufficientStock` | request exceeds available; carries `requested` and `available` |

`InsufficientStock` keeps the two numbers as attributes rather than only in the
message, so the API can put them in a structured response and a test can assert
on them without parsing a string:

```python
assert caught.value.requested == 5
assert caught.value.available == 2
```

## Verification

Tests first, failing for the right reason:

```
$ uv run pytest
E   ModuleNotFoundError: No module named 'inventory.repository.memory'
```

After implementing:

```
$ uv run pytest
50 passed in 0.05s

$ uv run pytest --cov
src/inventory/repository/base.py         9   0   100%
src/inventory/repository/memory.py      29   0   100%
src/inventory/service/reservations.py   37   0   100%
TOTAL                                  164   0   100%

$ uv run ruff check src tests
All checks passed!
```

## Not built yet, on purpose

- **No `confirm` or `cancel`.** Step 3.
- **No expiry sweep.** A hold currently blocks stock forever. Step 3.
- **No locking.** `reserve` reads availability and then writes, with a gap in
  between. Two threads can both pass the check and both succeed. This is a real
  bug and it is left in deliberately -- Step 4 opens with a test that catches it
  overselling, so the fix can be shown working rather than asserted.
