#!/usr/bin/env bash
# Walk the API through the whole reservation lifecycle and check every response.
#
#   make demo                        # starts its own server on a free port
#   ./scripts/demo.sh http://host    # or point it at a running one
#
# Exits non-zero if any step returns an unexpected status, so it doubles as a
# smoke test.

set -euo pipefail

BASE_URL="${1:-}"
SERVER_PID=""
PASSED=0
FAILED=0

cleanup() {
  [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

start_server() {
  local port
  port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
  BASE_URL="http://127.0.0.1:${port}"
  uv run uvicorn inventory.api.main:app \
    --host 127.0.0.1 --port "$port" --workers 1 --log-level warning &
  SERVER_PID=$!

  for _ in $(seq 1 100); do
    if curl -sf "${BASE_URL}/health" >/dev/null 2>&1; then return; fi
    sleep 0.2
  done
  echo "Server never became healthy at ${BASE_URL}" >&2
  exit 1
}

# step <description> <expected-status> <curl args...>
step() {
  local description="$1" expected="$2"; shift 2
  local response status body

  response=$(curl -s -w $'\n%{http_code}' "$@")
  status="${response##*$'\n'}"
  body="${response%$'\n'*}"

  printf '\n%s\n' "$description"
  printf '  -> HTTP %s (expected %s)\n' "$status" "$expected"
  printf '  %s\n' "$(echo "$body" | python3 -m json.tool --compact 2>/dev/null || echo "$body")"

  if [[ "$status" == "$expected" ]]; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
    printf '  MISMATCH\n'
  fi
  LAST_BODY="$body"
}

json_field() { echo "$1" | python3 -c "import json,sys; print(json.load(sys.stdin)['$2'])"; }

[[ -z "$BASE_URL" ]] && start_server

echo "Inventory Reservation - API walkthrough"
echo "  target: ${BASE_URL}"

step "1. Register a product with exactly one unit in stock" 201 \
  -X POST "${BASE_URL}/products" -H 'Content-Type: application/json' \
  -d '{"id":"sneaker","name":"Limited Sneaker","total_stock":1}'

step "2. First customer reserves it - succeeds, held for two minutes" 201 \
  -X POST "${BASE_URL}/reservations" -H 'Content-Type: application/json' \
  -d '{"product_id":"sneaker","quantity":1}'
RESERVATION_ID=$(json_field "$LAST_BODY" id)

step "3. Second customer tries the same unit - refused, nothing is oversold" 409 \
  -X POST "${BASE_URL}/reservations" -H 'Content-Type: application/json' \
  -d '{"product_id":"sneaker","quantity":1}'

step "4. Stock now shows the unit as held, not available" 200 \
  "${BASE_URL}/products/sneaker"

step "5. First customer pays - the hold becomes a completed sale" 200 \
  -X POST "${BASE_URL}/reservations/${RESERVATION_ID}/confirm"

step "6. Paying twice is refused - a confirmed purchase cannot be repeated" 409 \
  -X POST "${BASE_URL}/reservations/${RESERVATION_ID}/confirm"

step "7. A completed sale cannot be reversed either" 409 \
  -X POST "${BASE_URL}/reservations/${RESERVATION_ID}/cancel"

step "8. Stock is now sold, not merely held" 200 \
  "${BASE_URL}/products/sneaker"

step "9. A second product releases its stock when the hold is cancelled" 201 \
  -X POST "${BASE_URL}/products" -H 'Content-Type: application/json' \
  -d '{"id":"jacket","name":"Limited Jacket","total_stock":1}'

step "10. Reserve it" 201 \
  -X POST "${BASE_URL}/reservations" -H 'Content-Type: application/json' \
  -d '{"product_id":"jacket","quantity":1}'
JACKET_RESERVATION=$(json_field "$LAST_BODY" id)

step "11. Cancel it - the unit goes back into the pool" 200 \
  -X POST "${BASE_URL}/reservations/${JACKET_RESERVATION}/cancel"

step "12. The jacket is available again" 200 \
  "${BASE_URL}/products/jacket"

step "13. An unknown product is a 404, not a 500" 404 \
  "${BASE_URL}/products/does-not-exist"

step "14. A malformed request is rejected in the same error envelope" 422 \
  -X POST "${BASE_URL}/reservations" -H 'Content-Type: application/json' \
  -d '{"product_id":"sneaker","quantity":0}'

printf '\n%s\n' "-----------------------------------------"
printf 'passed: %d   failed: %d\n' "$PASSED" "$FAILED"
[[ "$FAILED" -eq 0 ]] || exit 1
