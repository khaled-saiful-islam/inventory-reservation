# AI Tool Disclosure

Requested as part of the submission. I am responsible for everything in this
repository, including the parts a tool drafted.

## 1. Which AI tool(s) did you use?

Claude (Opus 5) via Claude Code, run in my terminal with access to the working
directory, so it could run the test suite and the scripts rather than only
suggest code.

## 2. How did you use them?

As a pair-programming assistant inside a test-first loop that I directed. The
work was broken into seven steps, and each one ran the same way:

1. I decided what the step should do and what invariant it had to protect.
2. The tests were written first and **run** to confirm they failed for the
   expected reason.
3. The implementation was written to pass them.
4. Lint, full suite, and coverage; then the step was documented in
   `docs/steps/` and committed.

The step boundaries are the commits, so `git log` is an accurate record of the
order the work happened in.

Two things mattered more than the code generation.

**Watching the bug before fixing it.** The concurrency tests were written and run
against the previous step's code, which had no locking. They caught it selling
one item up to nine times across ten runs. Only then was the lock added. An
AI-written concurrency fix is worth very little if you have not first watched the
failure it claims to fix.

**Checking the verification, not just the code.** Before shipping
`scripts/load_test.py` I disabled every lock in the system and re-ran it. It
still reported PASS -- an in-memory critical section is too short to collide over
HTTP. That finding is in the README and in the script's own docstring, because a
load test that cannot fail is worse than no load test: it produces confidence
that is not backed by anything.

## 3. What portions of the solution were AI-assisted?

| Area | Who decided / who wrote |
|---|---|
| Choice of challenge, scope, stack | Me |
| Layering and dependency direction | Me |
| Locking strategy: per-product vs global vs optimistic | Me; Claude argued the trade-offs and I wrote them into ADR-001 |
| Lazy expiry instead of a background timer | Me |
| Deriving availability instead of keeping counters | Me |
| Sync `def` handlers so FastAPI's threadpool creates real contention | Me |
| Domain models, state machine, service methods | Claude drafted, I reviewed and edited |
| Repository port and in-memory store | Claude drafted, I reviewed |
| Test cases across all six test files | Claude drafted from my scenarios; I checked each one could actually fail |
| The barrier-released load test and demo script | Claude drafted, I verified the barrier forces a genuine collision |
| FastAPI routes, schemas, error mapping | Mostly Claude |
| README, ADR, step docs, this file | My structure and arguments, Claude drafted prose |

I have read every line submitted and can explain why each is there.

## 4. Prompts and workflow

The loop was: state the step, require a failing test first, review the diff,
require the suite and lint to be green, document the step, commit. I reviewed
after every step rather than at the end, which is why the history is seven small
commits and not one large one.

### Things Claude produced that I rejected

This is the part worth reading, because it is where the review actually happened.

- **`time.sleep()` in the expiry tests.** Replaced with an injected `Clock`
  protocol and a `FakeClock`. A suite that sleeps through a two-minute window
  takes longer than the feature it tests and goes flaky under CI load. No test in
  this repository sleeps.

- **A single global lock.** Correct, and it serialises the entire catalogue --
  reserving a sneaker would block someone reserving a jacket, which is the exact
  throughput a flash sale needs. Replaced with one lock per product. Both options
  are written up in ADR-001 rather than only the one that won.

- **A load test that could not fail.** Described above. Kept, with an honest
  statement of what it does and does not prove, instead of being presented as a
  thread-safety proof.

- **Widening FastAPI's threadpool from 40 to 500** to try to force the race over
  HTTP. It did not change the result, and there was no measurement showing 40 was
  too few, so it was reverted. Tuning without evidence is guesswork, and it had
  also introduced a deprecated `@app.on_event` hook.

- **Injecting a delay into the critical section** behind an environment variable
  to make the HTTP race reproducible. Rejected: that is test scaffolding inside
  production code, added so a test would look stronger than it is.

- **A test asserting handlers are synchronous by walking `app.routes`.** It found
  only FastAPI's own `/docs` routes and failed. The test was wrong, not the code
  -- this FastAPI version does not flatten an included router into `app.routes`.
  Rewritten against the router itself, and noted in `docs/steps/05-http-api.md`
  because a test that inspects framework internals is the kind that rots.

Two smaller ones: a deprecated Starlette status constant that was emitting a
warning, and three defensive branches that no test reached until I checked the
coverage report line by line.
