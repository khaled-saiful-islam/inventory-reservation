"""HTTP endpoints.

Every handler is a plain `def`, not `async def`. That is load-bearing: FastAPI
dispatches a sync handler to a worker thread, so concurrent requests genuinely
run at the same time and hit the per-product lock. An `async def` handler with no
`await` inside would run start-to-finish on the event loop, serialising requests
and making the load test pass against code with no locking at all.

Handlers contain no `try/except`. Domain errors become HTTP responses in
`api/errors.py`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from inventory.api.schemas import (
    CreateProductRequest,
    CreateReservationRequest,
    ErrorResponse,
    HealthResponse,
    ProductResponse,
    ReservationResponse,
)
from inventory.domain.models import Reservation
from inventory.service.reservations import ReservationService

router = APIRouter()

FAILURE_RESPONSES: dict[int | str, dict[str, type[ErrorResponse]]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def get_service(request: Request) -> ReservationService:
    """The one place the app's service instance is handed to a route."""
    return request.app.state.service


Service = Annotated[ReservationService, Depends(get_service)]


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post(
    "/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    responses=FAILURE_RESPONSES,
    tags=["products"],
)
def create_product(body: CreateProductRequest, service: Service) -> ProductResponse:
    """Register a product and the stock available for the sale."""
    service.create_product(product_id=body.id, name=body.name, total_stock=body.total_stock)
    return _product_response(service, body.id)


@router.get(
    "/products/{product_id}",
    response_model=ProductResponse,
    responses=FAILURE_RESPONSES,
    tags=["products"],
)
def read_product(product_id: str, service: Service) -> ProductResponse:
    """The current stock split, with lapsed holds already reclaimed."""
    return _product_response(service, product_id)


@router.post(
    "/reservations",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=FAILURE_RESPONSES,
    tags=["reservations"],
)
def create_reservation(body: CreateReservationRequest, service: Service) -> ReservationResponse:
    """Hold stock for two minutes. Returns 409 when there is not enough left."""
    reservation = service.reserve(product_id=body.product_id, quantity=body.quantity)
    return _reservation_response(reservation)


@router.get(
    "/reservations/{reservation_id}",
    response_model=ReservationResponse,
    responses=FAILURE_RESPONSES,
    tags=["reservations"],
)
def read_reservation(reservation_id: str, service: Service) -> ReservationResponse:
    """A hold's current state. Reports `expired` once its window has closed."""
    return _reservation_response(service.get_reservation(reservation_id))


@router.post(
    "/reservations/{reservation_id}/confirm",
    response_model=ReservationResponse,
    responses=FAILURE_RESPONSES,
    tags=["reservations"],
)
def confirm_reservation(reservation_id: str, service: Service) -> ReservationResponse:
    """Complete the purchase. Returns 409 if the hold already ended."""
    return _reservation_response(service.confirm(reservation_id))


@router.post(
    "/reservations/{reservation_id}/cancel",
    response_model=ReservationResponse,
    responses=FAILURE_RESPONSES,
    tags=["reservations"],
)
def cancel_reservation(reservation_id: str, service: Service) -> ReservationResponse:
    """Give up the hold and return its stock. Returns 409 if the hold already ended."""
    return _reservation_response(service.cancel(reservation_id))


def _product_response(service: ReservationService, product_id: str) -> ProductResponse:
    product = service.get_product(product_id)
    level = service.get_stock_level(product_id)
    return ProductResponse(
        id=product.id,
        name=product.name,
        total=level.total,
        confirmed=level.confirmed,
        reserved=level.reserved,
        available=level.available,
    )


def _reservation_response(reservation: Reservation) -> ReservationResponse:
    return ReservationResponse(
        id=reservation.id,
        product_id=reservation.product_id,
        quantity=reservation.quantity,
        state=reservation.state.value,
        created_at=reservation.created_at,
        expires_at=reservation.expires_at,
    )
