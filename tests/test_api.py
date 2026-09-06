"""The HTTP contract.

These tests drive the real FastAPI app end to end. The service behind it is built
with a `FakeClock`, so the expiry case here exercises the whole stack -- route,
service, domain -- without waiting two minutes.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from inventory.api.errors import _first_problem, register_error_handlers
from inventory.api.main import create_app
from inventory.domain.errors import InventoryError
from inventory.repository.memory import InMemoryInventoryRepository
from inventory.service.reservations import ReservationService

HOLD_SECONDS = 120


@pytest.fixture
def api(clock) -> TestClient:
    service = ReservationService(repository=InMemoryInventoryRepository(), clock=clock)
    return TestClient(create_app(service=service))


@pytest.fixture
def product(api) -> str:
    api.post("/products", json={"id": "p1", "name": "Sneaker", "total_stock": 1})
    return "p1"


def reserve(api, product_id: str = "p1", quantity: int = 1):
    return api.post("/reservations", json={"product_id": product_id, "quantity": quantity})


class TestHealth:
    def test_the_service_reports_itself_healthy(self, api):
        response = api.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestCreateProduct:
    def test_a_new_product_is_created_with_all_stock_available(self, api):
        response = api.post("/products", json={"id": "p1", "name": "Sneaker", "total_stock": 10})

        assert response.status_code == 201
        assert response.json() == {
            "id": "p1",
            "name": "Sneaker",
            "total": 10,
            "confirmed": 0,
            "reserved": 0,
            "available": 10,
        }

    def test_reusing_an_id_is_a_conflict(self, api, product):
        response = api.post("/products", json={"id": "p1", "name": "Other", "total_stock": 5})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "duplicate_product"

    def test_negative_stock_is_rejected_before_it_reaches_the_service(self, api):
        response = api.post("/products", json={"id": "p1", "name": "Sneaker", "total_stock": -1})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


class TestReadProduct:
    def test_stock_can_be_inspected(self, api, product):
        response = api.get(f"/products/{product}")

        assert response.status_code == 200
        assert response.json()["available"] == 1

    def test_a_hold_shows_up_in_the_stock_split(self, api, product):
        reserve(api)

        body = api.get(f"/products/{product}").json()

        assert body["reserved"] == 1
        assert body["available"] == 0

    def test_an_unknown_product_is_not_found(self, api):
        response = api.get("/products/nope")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "product_not_found"


class TestCreateReservation:
    def test_a_hold_is_created_with_its_deadline(self, api, product, clock):
        response = reserve(api)

        assert response.status_code == 201
        body = response.json()
        assert body["state"] == "active"
        assert body["quantity"] == 1
        assert body["product_id"] == "p1"
        assert body["id"]
        assert body["expires_at"] > body["created_at"]

    def test_the_second_caller_for_the_last_item_is_refused(self, api, product):
        reserve(api)

        response = reserve(api)

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "insufficient_stock"
        assert "0 available" in error["message"]

    @pytest.mark.parametrize("quantity", [0, -1])
    def test_a_quantity_below_one_is_rejected(self, api, product, quantity):
        response = reserve(api, quantity=quantity)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_reserving_an_unknown_product_is_not_found(self, api):
        response = reserve(api, product_id="nope")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "product_not_found"


class TestReadReservation:
    def test_a_hold_can_be_read_back(self, api, product):
        created = reserve(api).json()

        response = api.get(f"/reservations/{created['id']}")

        assert response.status_code == 200
        assert response.json() == created

    def test_an_unknown_hold_is_not_found(self, api):
        response = api.get("/reservations/nope")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "reservation_not_found"


class TestConfirm:
    def test_confirming_completes_the_sale(self, api, product):
        created = reserve(api).json()

        response = api.post(f"/reservations/{created['id']}/confirm")

        assert response.status_code == 200
        assert response.json()["state"] == "confirmed"
        assert api.get(f"/products/{product}").json()["confirmed"] == 1

    def test_confirming_twice_is_a_conflict(self, api, product):
        created = reserve(api).json()
        api.post(f"/reservations/{created['id']}/confirm")

        response = api.post(f"/reservations/{created['id']}/confirm")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_state_transition"

    def test_confirming_an_unknown_hold_is_not_found(self, api):
        response = api.post("/reservations/nope/confirm")

        assert response.status_code == 404


class TestCancel:
    def test_cancelling_returns_the_stock(self, api, product):
        created = reserve(api).json()

        response = api.post(f"/reservations/{created['id']}/cancel")

        assert response.status_code == 200
        assert response.json()["state"] == "cancelled"
        assert api.get(f"/products/{product}").json()["available"] == 1

    def test_a_completed_sale_cannot_be_cancelled(self, api, product):
        created = reserve(api).json()
        api.post(f"/reservations/{created['id']}/confirm")

        response = api.post(f"/reservations/{created['id']}/cancel")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_state_transition"


class TestExpiryOverHttp:
    def test_stock_is_released_once_the_hold_lapses(self, api, product, clock):
        reserve(api)

        clock.advance(HOLD_SECONDS)

        assert api.get(f"/products/{product}").json()["available"] == 1

    def test_a_lapsed_hold_reports_itself_expired(self, api, product, clock):
        created = reserve(api).json()

        clock.advance(HOLD_SECONDS)

        body = api.get(f"/reservations/{created['id']}").json()
        assert body["state"] == "expired"

    def test_a_lapsed_hold_cannot_be_paid_for(self, api, product, clock):
        created = reserve(api).json()

        clock.advance(HOLD_SECONDS)
        response = api.post(f"/reservations/{created['id']}/confirm")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_state_transition"


class TestErrorEnvelope:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("get", "/products/nope", None),
            ("get", "/reservations/nope", None),
            ("post", "/reservations", {"product_id": "nope", "quantity": 1}),
            ("post", "/products", {"id": "p", "name": "n", "total_stock": -1}),
        ],
    )
    def test_every_failure_uses_the_same_shape(self, api, method, path, body):
        """One envelope, so a client never has to parse two error formats."""
        response = getattr(api, method)(path, json=body) if body else getattr(api, method)(path)

        assert response.status_code >= 400
        error = response.json()["error"]
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]


class TestHandlersAreSynchronous:
    def test_every_route_is_a_plain_function_not_a_coroutine(self):
        """Load-bearing, not stylistic.

        FastAPI dispatches a sync handler to a worker thread, so concurrent
        requests genuinely overlap and contend on the per-product lock. An
        `async def` handler with no `await` inside would run start-to-finish on
        the event loop: requests would serialise, and `scripts/load_test.py`
        would report "no overselling" even against code with no locking at all.

        Asserted on the router rather than the assembled app, because FastAPI
        does not flatten an included router into `app.routes`.
        """
        from inventory.api.routes import router

        coroutine_handlers = [
            route.endpoint.__name__
            for route in router.routes
            if inspect.iscoroutinefunction(route.endpoint)
        ]

        assert len(router.routes) == 7
        assert coroutine_handlers == []


class TestErrorMappingEdges:
    """The two defensive branches in `api/errors.py`.

    Neither is reachable through a normal request, which is exactly why they are
    worth pinning: if either broke, no other test would notice.
    """

    def test_an_error_missing_from_the_table_becomes_a_500_not_a_traceback(self):
        class UnmappedError(InventoryError):
            code = "unmapped"

        app = FastAPI()
        register_error_handlers(app)

        @app.get("/boom")
        def boom() -> None:
            raise UnmappedError("an error nobody added to STATUS_BY_ERROR")

        response = TestClient(app).get("/boom")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "unmapped"

    def test_a_validation_error_carrying_no_details_still_yields_a_message(self):
        """Tests a private helper on purpose -- it is the fallback that stops an
        empty error list turning into an IndexError inside an error handler."""
        assert _first_problem(RequestValidationError([])) == "Request body is invalid"
