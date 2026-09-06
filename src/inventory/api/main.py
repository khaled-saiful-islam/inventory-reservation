"""Application factory.

`create_app` takes an optional service so a test can supply one built with a
`FakeClock`. Nothing else in the codebase constructs the object graph, so there
is exactly one place to look to see how the layers are wired together.
"""

from __future__ import annotations

from fastapi import FastAPI

from inventory.api.errors import register_error_handlers
from inventory.api.routes import router
from inventory.repository.memory import InMemoryInventoryRepository
from inventory.service.reservations import ReservationService

DESCRIPTION = """
Holds limited stock for two minutes so a flash sale cannot oversell.

`available = total - confirmed - reserved`

Run with a single worker. Inventory and its locks live in process memory, so a
second worker would hold a second copy of both and the guarantee would break.
"""


def create_app(service: ReservationService | None = None) -> FastAPI:
    app = FastAPI(
        title="Inventory Reservation System",
        version="1.0.0",
        description=DESCRIPTION,
    )
    app.state.service = service or ReservationService(repository=InMemoryInventoryRepository())
    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
