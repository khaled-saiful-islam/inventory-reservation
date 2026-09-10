"""Domain errors to HTTP responses, in one place.

No route contains a `try/except`. A route calls the service and returns; if the
service raises, these handlers decide the status code and the body. Adding an
error means adding one line to the table below.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from inventory.domain.errors import (
    DuplicateProduct,
    InsufficientStock,
    InvalidQuantity,
    InvalidStateTransition,
    InvalidStock,
    InventoryError,
    ProductNotFound,
    ReservationNotFound,
)

STATUS_BY_ERROR: dict[type[InventoryError], int] = {
    InvalidQuantity: status.HTTP_400_BAD_REQUEST,
    InvalidStock: status.HTTP_400_BAD_REQUEST,
    ProductNotFound: status.HTTP_404_NOT_FOUND,
    ReservationNotFound: status.HTTP_404_NOT_FOUND,
    DuplicateProduct: status.HTTP_409_CONFLICT,
    InsufficientStock: status.HTTP_409_CONFLICT,
    InvalidStateTransition: status.HTTP_409_CONFLICT,
}

VALIDATION_ERROR_CODE = "validation_error"

# Starlette renamed this constant; read it by value so the module works on
# either version without tripping a deprecation warning.
UNPROCESSABLE = 422


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(InventoryError)
    def handle_domain_error(_: Request, exc: InventoryError) -> JSONResponse:
        """Starlette walks the exception's MRO, so registering the base class here
        covers every subclass. An error missing from the table is a bug, not a
        crash: it becomes a 500 with its own code rather than leaking a traceback.
        """
        status_code = STATUS_BY_ERROR.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
        return error_response(status_code, exc.code, str(exc))

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's default validation body is `{"detail": [...]}`, a second error
        shape for a client to handle. This folds it into the one envelope.
        """
        return error_response(
            UNPROCESSABLE,
            VALIDATION_ERROR_CODE,
            _first_problem(exc),
        )


def _first_problem(exc: RequestValidationError) -> str:
    """The first validation failure as one readable line, e.g. `quantity: ...`.

    Only the first: a caller fixing one field at a time does not need the list,
    and the full detail is still in the OpenAPI schema.
    """
    errors = exc.errors()
    if not errors:
        return "Request body is invalid"
    first = errors[0]
    field = ".".join(str(part) for part in first["loc"] if part != "body")
    return f"{field}: {first['msg']}" if field else str(first["msg"])
