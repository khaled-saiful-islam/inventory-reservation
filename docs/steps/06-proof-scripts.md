# Step 6 — Proof Scripts

**Goal:** two commands a reviewer can run that show the system working, without
reading a single test file.

## What was created

| File | Purpose |
|---|---|
| `scripts/demo.sh` | 14-step API walkthrough via curl, each step's status asserted |
| `scripts/load_test.py` | 500 concurrent HTTP requests, three scenarios, exits non-zero on failure |

Both start their own server on a free port and shut it down afterwards, or accept
a URL to target one that is already running.

## `make load-test`

```
Inventory Reservation - no-overselling proof over HTTP
  target: http://127.0.0.1:56732

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

## The finding: this script does not prove what it looks like it proves

Before shipping it, the obvious question was whether it would fail against
broken code. So `lock_product` was replaced with `contextlib.nullcontext()` --
every lock in the system removed -- and the script was run again:

```
    201 Created              1   (expected 1)
    OVERSOLD                 0   <-- must be 0
RESULT: PASS - every scenario sold exactly its stock, no more
```

**It passed with no locking at all.**

The reason is timing. An in-memory critical section runs in microseconds; an HTTP
round trip takes milliseconds. Requests arrive staggered by the network and the
event loop, so two of them almost never sit inside `reserve` at the same instant.
The suspicion that FastAPI's 40-thread default pool was serialising things was
tested too -- widening it to 500 changed nothing:

```python
@app.on_event("startup")
async def _widen_threadpool() -> None:
    anyio.to_thread.current_default_thread_limiter().total_tokens = 500
```

Both experiments were reverted. The threadpool change was not kept: there is no
measurement showing 40 is too few, and tuning without evidence is guesswork.

### What this means

| | `tests/test_concurrency.py` | `scripts/load_test.py` |
|---|---|---|
| Calls | the service, in-process | the deployed HTTP stack |
| Threads released together | yes, `threading.Barrier` | yes, `asyncio.Barrier` |
| GIL switch interval | shortened to 1e-6 | default |
| Against unlocked code | **fails every run** | passes |
| Proves | the locking is correct | the whole stack behaves and returns the right statuses |

The in-process test is the race detector. The load test is the end-to-end
demonstration and a regression guard for the API layer. Both are worth having;
only one of them would catch a missing lock, and it is worth knowing which.

This is written into the script's own docstring, not just here, so nobody reading
`load_test.py` in six months mistakes it for a thread-safety proof.

### Why not force the race over HTTP

It could be done -- inject a delay inside the critical section behind an
environment variable, and the race would appear. That was rejected because it
means shipping test scaffolding inside production code to make a test look
stronger than it is. The honest version is a load test that proves end-to-end
behaviour, an in-process test that proves the locking, and a note saying which is
which.

It is also worth noting *why* the window is so small: storage is a dictionary.
Put a real database round trip between the read and the write, and the window
becomes milliseconds wide -- at which point the lock stops being a precaution and
starts being the only thing holding the invariant up.

## `make demo`

Fourteen steps, each asserting its status code, covering the full lifecycle plus
the failure paths:

```
2. First customer reserves it - succeeds, held for two minutes
  -> HTTP 201 (expected 201)
  {"id":"dedaec0b...","state":"active","expires_at":"2026-09-06T14:48:57Z"}

3. Second customer tries the same unit - refused, nothing is oversold
  -> HTTP 409 (expected 409)
  {"error":{"code":"insufficient_stock","message":"Product sneaker has 0 available, 1 requested"}}

6. Paying twice is refused - a confirmed purchase cannot be repeated
  -> HTTP 409 (expected 409)
  {"error":{"code":"invalid_state_transition","message":"... is already confirmed and cannot become confirmed"}}

13. An unknown product is a 404, not a 500
  -> HTTP 404 (expected 404)
  {"error":{"code":"product_not_found","message":"Product does-not-exist does not exist"}}

-----------------------------------------
passed: 14   failed: 0
```

It exits non-zero on any mismatch, so it is a smoke test as well as a
demonstration -- a reviewer can run it, and CI could too.

## Verification

```
$ ./scripts/demo.sh
passed: 14   failed: 0        (exit 0)

$ uv run python scripts/load_test.py
RESULT: PASS                  (exit 0)

$ uv run pytest
109 passed in 0.69s
```
