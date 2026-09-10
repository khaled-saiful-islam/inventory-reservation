.PHONY: install run test test-cov lint format check demo load-test \
        docker-up docker-down docker-logs docker-verify

install:          ## Install all dependencies
	uv sync

run:              ## Start the API on http://localhost:8000
	uv run uvicorn inventory.api.main:app --reload --workers 1

test:             ## Run the full test suite
	uv run pytest

test-cov:         ## Run tests with a coverage report
	uv run pytest --cov --cov-report=term-missing

lint:             ## Check formatting and lint rules
	uv run ruff format --check src tests
	uv run ruff check src tests

format:           ## Auto-fix formatting and lint issues
	uv run ruff format src tests
	uv run ruff check --fix src tests

check: lint test  ## Everything CI would run

demo:             ## Walk through the API with curl (needs `make run`)
	./scripts/demo.sh

load-test:        ## Fire 500 concurrent requests at a 1-item product
	uv run python scripts/load_test.py

# --- Docker ---------------------------------------------------------------
# Self-contained: its own compose project, its own network, no volumes, and
# bound to 127.0.0.1:8200 so it cannot collide with anything else running.
# Override the port with INVENTORY_PORT=9500 make docker-up

INVENTORY_PORT ?= 8200

docker-up:        ## Build and start the API in a container on :8200
	INVENTORY_PORT=$(INVENTORY_PORT) docker compose up --build -d
	@echo "waiting for health..."
	@until [ "$$(docker inspect --format='{{.State.Health.Status}}' inventory-reservation-api 2>/dev/null)" = "healthy" ]; do sleep 1; done
	@echo "ready -> http://localhost:$(INVENTORY_PORT)  (docs at /docs)"

docker-down:      ## Stop and remove the container and its network
	INVENTORY_PORT=$(INVENTORY_PORT) docker compose down

docker-logs:      ## Tail the container logs
	INVENTORY_PORT=$(INVENTORY_PORT) docker compose logs -f

docker-verify:    ## Run the demo and load test against the running container
	./scripts/demo.sh http://localhost:$(INVENTORY_PORT)
	uv run python scripts/load_test.py --url http://localhost:$(INVENTORY_PORT)
