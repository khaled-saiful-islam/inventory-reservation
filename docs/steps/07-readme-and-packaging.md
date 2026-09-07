# Step 7 — README, Disclosure, Packaging

**Goal:** make the repository readable and runnable by someone who has never seen
it, and state plainly what it does not do.

## What was created

| File | Purpose |
|---|---|
| `README.md` | Quick start, the invariant, the locking strategy, pasted proof output, limitations |
| `AI_DISCLOSURE.md` | The four questions the brief asks, plus what was rejected |
| `Dockerfile` | Single stage, pinned uv, healthcheck, one worker |
| `.python-version` | Pins 3.12 |

## README ordering

Deliberate, on the assumption a reviewer reads top to bottom and stops when they
have what they need:

1. **The headline** -- `stock = 1 · 500 requests · 1 created · 499 refused · 0 oversold`
2. **Quick start** -- two commands, then two that prove it
3. **The problem** -- with the measured overselling from before the fix
4. **The invariant**
5. **Locking strategy** -- what was chosen and the three alternatives that were not
6. **Proof** -- pasted output, and which test is actually load-bearing
7. **API**, **lifecycle**, **structure**, **tests**
8. **Limitations** and **with more time**

The locking strategy sits above the API reference because the brief lists "clear
locking strategy explanation" as an evaluation criterion. The limitations are in
the README rather than only in an ADR, because a limitation a reader has to go
looking for is not really disclosed.

## Python pinned to 3.12

Development had been on 3.14 while `pyproject.toml` declared `>=3.12`. Anyone
cloning the repo would get whatever their machine had, which is not what was
tested.

`.python-version` now pins 3.12 -- the declared floor, so `uv sync` reproduces
the tested environment exactly. The environment was rebuilt and everything
re-verified on it:

```
$ uv run python --version
Python 3.12.12

$ uv run pytest
109 passed in 1.80s

$ uv run python scripts/load_test.py
RESULT: PASS

$ ./scripts/demo.sh
passed: 14   failed: 0

$ for i in $(seq 1 8); do pytest tests/test_concurrency.py; done
clean: 8 / 8
```

## Dockerfile

Single stage -- four runtime dependencies and no build step, so a multi-stage
build would add a layer of indirection for nothing.

Three details that are not decoration:

- **uv is pinned** to a digest-addressed tag. A floating installer makes the
  image non-reproducible, which defeats the point of committing a lockfile.
- **Dependencies are copied before source**, so editing a `.py` file does not
  invalidate the dependency layer.
- **`--workers 1`, with the reason in a comment.** This is the one setting that
  would silently break the guarantee if someone raised it.

Verified by building and running it, not by reading it:

```
$ docker build -t inventory-reservation .
$ docker run -d -p 8100:8000 inventory-reservation

$ curl localhost:8100/health
{"status":"ok"}

$ curl -X POST .../products -d '{"id":"s","name":"Sneaker","total_stock":1}'
{"id":"s","total":1,"confirmed":0,"reserved":0,"available":1}

$ curl -X POST .../reservations -d '{"product_id":"s","quantity":1}'
{"id":"55bd9b7c...","state":"active",...}

$ curl -X POST .../reservations -d '{"product_id":"s","quantity":1}'
{"error":{"code":"insufficient_stock",...}}   [409]

$ docker inspect --format='{{.State.Health.Status}}' inv-test
healthy
```

## No CI pipeline

A workflow was written and then removed. Both proof scripts already exit
non-zero on failure, so `make check` plus `./scripts/demo.sh` and
`scripts/load_test.py` give the same gate locally. Committing a pipeline that
cannot run is worse than not having one.

## AI disclosure

The brief's four questions, answered directly. The section worth reading is the
last one: **what was rejected**.

- `time.sleep()` in the expiry tests, replaced with an injected clock
- a single global lock, replaced with one lock per product
- a load test that could not fail, kept but honestly labelled
- widening FastAPI's threadpool, reverted as tuning without evidence
- injecting a delay into the critical section to force the HTTP race
- a sync-handler test that inspected the wrong object and failed

Anyone can write "I used Claude." Naming six things that were produced and turned
down, with the reason for each, is the part that shows the code was reviewed
rather than accepted.

## Final state

```
109 tests · 100% coverage · 0.9s
821 lines of source · 1069 lines of tests
8 commits, one per step
```

Everything in the brief is implemented: Level 1 basic reservation, Level 2
lifecycle and expiry, Level 3 concurrency, unit tests, concurrency tests with
parallel requests, and a written locking strategy. The API was added beyond the
brief because the reservation flow is easier to believe when you can curl it.
