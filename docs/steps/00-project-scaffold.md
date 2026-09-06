# Step 0 — Project Scaffold

**Goal:** a repository that a reviewer can clone and run in two commands, with
linting and testing wired up before any business logic exists.

## What was created

| Path | Purpose |
|---|---|
| `pyproject.toml` | Dependencies, pytest config, ruff config, coverage config |
| `Makefile` | Every command a reviewer needs, self-documenting |
| `.gitignore` | Standard Python ignores |
| `src/inventory/` | The package, split into four layers (see below) |
| `tests/` | Test suite |
| `scripts/` | Manual demo and load-test scripts (added in a later step) |
| `docs/steps/` | This log — one file per build step |
| `docs/adr/` | Architecture decision records for the non-obvious choices |

## The four layers

```
api/          FastAPI routes and schemas    — knows HTTP, knows the service
service/      Business rules                 — knows the domain, not HTTP
repository/   Storage and locking            — knows the domain, not the service
domain/       Models, states, errors, clock  — knows nothing else
```

Dependencies only ever point downward. The domain layer has no imports from
FastAPI, the repository, or the service. This is what makes the core logic
testable without a web server and swappable to a real database later.

## Dependency choices

Deliberately small — four runtime packages, four dev packages.

- **FastAPI + uvicorn** — the API layer.
- **pytest + pytest-cov** — tests and coverage.
- **httpx** — used by FastAPI's `TestClient` and by the load-test script.
- **ruff** — formatter and linter in one tool, so there is no black/flake8/isort split.

No database driver, no Redis client, no ORM. The challenge specifies in-memory
inventory, and adding infrastructure the problem does not need would be scope creep.

## Commands

```bash
make install    # uv sync
make run        # start the API
make test       # run the suite
make check      # lint + test, i.e. everything CI would run
```

## Verification

```
$ uv run ruff check src tests
All checks passed!

$ uv run ruff format --check src tests
6 files already formatted

$ uv run pytest
collected 0 items
```

Zero tests is correct at this point — Step 1 writes the first one.

## Note on `--workers 1`

`make run` pins uvicorn to a single worker. This is intentional and load-bearing,
not an oversight. Inventory lives in process memory guarded by in-process locks,
so multiple workers would each hold a separate copy of the inventory and the
no-overselling guarantee would break. This is documented again in the README and
revisited in Step 4.
