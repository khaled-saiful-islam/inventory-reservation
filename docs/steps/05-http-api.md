# Step 5 — HTTP API

**Goal:** put the service behind six endpoints, with one error shape and no
`try/except` in any route.

## What was created

| File | Contents |
|---|---|
| `api/schemas.py` | Pydantic request/response models with field constraints |
| `api/errors.py` | one table mapping domain errors to status codes, two exception handlers |
| `api/routes.py` | seven routes (six operations plus `/health`) |
| `api/main.py` | `create_app(service=None)` -- the only place the object graph is wired |
| `service/reservations.py` | `get_product` added, so a response can carry the name |
| `tests/test_api.py` | 29 tests |

## The surface

| Method | Path | Success | Failures |
|---|---|---|---|
| GET | `/health` | 200 | -- |
| POST | `/products` | 201 | 409 duplicate, 422 invalid body |
| GET | `/products/{id}` | 200 | 404 unknown |
| POST | `/reservations` | 201 | 404 unknown product, 409 insufficient stock, 422 invalid body |
| GET | `/reservations/{id}` | 200 | 404 unknown |
| POST | `/reservations/{id}/confirm` | 200 | 404 unknown, 409 already ended |
| POST | `/reservations/{id}/cancel` | 200 | 404 unknown, 409 already ended |

Six operations, not sixteen. There is no list endpoint, no delete, no pagination
and no `/v1` prefix, because nothing in the brief needs them and each one would be
surface area to review without a requirement behind it.

Interactive docs come free at `/docs`.

## Handlers are `def`, not `async def`

This is the single most important line in the whole layer:

```python
@router.post("/reservations", ...)
def create_reservation(body: CreateReservationRequest, service: Service):
```

FastAPI dispatches a **sync** handler to a worker thread, so concurrent requests
genuinely overlap and contend on the per-product lock from Step 4.

An `async def` handler containing no `await` would run start to finish on the
event loop. Requests would serialise, and `scripts/load_test.py` would print
"1 success, 499 failures, no overselling" **even against code with no locking at
all**. The load test would be theatre.

Because that is easy to undo by accident, a test asserts it:

```python
def test_every_route_is_a_plain_function_not_a_coroutine(self):
    coroutine_handlers = [
        route.endpoint.__name__
        for route in router.routes
        if inspect.iscoroutinefunction(route.endpoint)
    ]
    assert len(router.routes) == 7
    assert coroutine_handlers == []
```

The first version of that test walked `app.routes`, found only FastAPI's own
`/docs` routes, and failed. This FastAPI version does not flatten an included
router into `app.routes`, so the assertion moved to the router itself. The test
was wrong, not the code -- worth recording, because a test that inspects
framework internals is the kind that rots.

## One error shape, everywhere

```json
{"error": {"code": "insufficient_stock", "message": "Product sneaker has 0 available, 1 requested"}}
```

`code` is stable and safe to branch on. `message` is for humans and may be
reworded. Routes never build this -- two handlers do:

```python
STATUS_BY_ERROR: dict[type[InventoryError], int] = {
    InvalidQuantity: 400,
    InvalidStock: 400,
    ProductNotFound: 404,
    ReservationNotFound: 404,
    DuplicateProduct: 409,
    InsufficientStock: 409,
    InvalidStateTransition: 409,
}
```

Starlette resolves handlers by walking the exception's MRO, so one handler
registered on `InventoryError` covers every subclass. Adding an error means
adding one row to that table; no route changes.

FastAPI's own validation failures are folded into the same envelope. Its default
is `{"detail": [ ... ]}`, which would be a **second** error format for a client
to handle:

```
$ curl -X POST .../reservations -d '{"product_id":"sneaker","quantity":0}'
{"error":{"code":"validation_error","message":"quantity: Input should be greater than or equal to 1"}}
```

A parametrised test walks four different failures and asserts they all carry a
non-empty `code` and `message`.

### An unmapped error is a 500, not a traceback

```python
status_code = STATUS_BY_ERROR.get(type(exc), 500)
```

If someone adds a domain error and forgets the table, the client gets a clean
500 carrying that error's own code -- not a stack trace, and not a crash. Tested
with a throwaway app and an error deliberately left out of the table.

## The app factory takes its service

```python
def create_app(service: ReservationService | None = None) -> FastAPI:
    app = FastAPI(...)
    app.state.service = service or ReservationService(
        repository=InMemoryInventoryRepository()
    )
```

That optional argument is why `tests/test_api.py` can drive the **whole stack**
-- route, service, domain -- against a `FakeClock`:

```python
def test_a_lapsed_hold_cannot_be_paid_for(self, api, product, clock):
    created = reserve(api).json()

    clock.advance(HOLD_SECONDS)
    response = api.post(f"/reservations/{created['id']}/confirm")

    assert response.status_code == 409
```

A two-minute expiry, verified over HTTP, in under a millisecond.

## Verification

Tests first:

```
$ uv run pytest tests/test_api.py
ERROR tests/test_api.py -- ModuleNotFoundError: No module named 'inventory.api.main'
```

After implementing:

```
$ uv run pytest
109 passed in 0.77s

$ uv run pytest --cov
src/inventory/api/errors.py    25   0   100%
src/inventory/api/main.py      14   0   100%
src/inventory/api/routes.py    40   0   100%
src/inventory/api/schemas.py   31   0   100%
TOTAL                         324   0   100%
```

`TestClient` is not a real server, so the same flow was run against uvicorn:

```
$ curl localhost:8099/health
{"status":"ok"}

$ curl -X POST .../products -d '{"id":"sneaker","name":"Limited Sneaker","total_stock":1}'
{"id":"sneaker","name":"Limited Sneaker","total":1,"confirmed":0,"reserved":0,"available":1}

$ curl -X POST .../reservations -d '{"product_id":"sneaker","quantity":1}'
{"id":"dc659633...","state":"active","created_at":"2026-09-06T14:41:48Z","expires_at":"2026-09-06T14:43:48Z"}

$ curl -X POST .../reservations -d '{"product_id":"sneaker","quantity":1}'      # second caller
{"error":{"code":"insufficient_stock","message":"Product sneaker has 0 available, 1 requested"}}   [409]

$ curl -X POST .../reservations/dc659633.../confirm
{"id":"dc659633...","state":"confirmed",...}

$ curl -X POST .../reservations/dc659633.../confirm                            # again
{"error":{"code":"invalid_state_transition","message":"... is already confirmed and cannot become confirmed"}}  [409]

$ curl .../products/sneaker
{"id":"sneaker","total":1,"confirmed":1,"reserved":0,"available":0}
```

Step 6 turns that sequence into `scripts/demo.sh` so a reviewer does not have to
retype it.

## Housekeeping

The suite reported six deprecation warnings. One was ours -- Starlette renamed
`HTTP_422_UNPROCESSABLE_ENTITY` -- and is fixed by using the numeric value. The
other two are inside Starlette's own `TestClient` and are silenced by name in
`pyproject.toml`, listed individually rather than blanket-ignored so a **new**
warning still appears.

## Not built yet

`scripts/demo.sh` and `scripts/load_test.py`. The concurrency proof so far is at
the service layer; Step 6 proves it over real HTTP with 500 parallel requests.
