.PHONY: install run test test-cov lint format check demo load-test

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
