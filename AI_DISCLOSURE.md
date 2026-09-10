# AI Tool Disclosure

I am responsible for everything in this repository, including what a tool drafted.

## Which tool

Claude (Opus 5) via Claude Code, in my terminal with access to the working
directory, so it could run the tests and scripts rather than only suggest code.

## How I used it

As a pair programmer inside a test-first loop I directed. Seven steps, each one:
decide the goal, write the tests and **run them to see them fail**, implement,
then lint, full suite, document, commit. The commits are the step boundaries, so
`git log` reflects the real order of work.

Two things mattered more than code generation:

- **I watched the bug before fixing it.** The concurrency tests were run against
  the previous step's unlocked code and caught it selling one item up to nine
  times. Only then was the lock added.
- **I checked the verification, not just the code.** Before shipping
  `scripts/load_test.py` I removed every lock and re-ran it — it still reported
  PASS. That is in the README and in the script's own docstring.

## What was AI-assisted

| | |
|---|---|
| **Mine** | Choice of challenge and scope · layering · locking strategy · lazy expiry over a timer · deriving availability instead of counters · sync `def` handlers so FastAPI's threadpool creates real contention |
| **Claude drafted, I reviewed** | Domain models, service, repository · all six test files · demo and load scripts · README and doc prose |
| **Mostly Claude** | FastAPI routes, schemas, error mapping |

## What I rejected

The part where the review actually happened.

- **`time.sleep()` in the expiry tests** → injected `Clock` + `FakeClock`. No test
  in this repo sleeps.
- **A single global lock** → one lock per product, so a sneaker does not block a
  jacket. Both are written up in ADR-001, not just the winner.
- **A load test that could not fail** → kept, but honestly labelled rather than
  presented as a thread-safety proof.
- **Widening FastAPI's threadpool 40 → 500** to force the race over HTTP. No
  effect, no measurement justifying it, and it added a deprecated hook. Reverted.
- **Injecting a delay into the critical section** to make the HTTP race
  reproducible. That is test scaffolding inside production code.
- **A sync-handler test walking `app.routes`** — it found only FastAPI's own
  `/docs` routes and failed. The test was wrong, not the code.

Plus a deprecated Starlette constant, and three defensive branches no test
reached until I read the coverage report line by line.
