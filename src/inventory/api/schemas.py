"""Request and response bodies.

Field constraints here are the outermost of three validation layers: Pydantic
rejects a malformed request before the service sees it, the service states its
own preconditions, and the domain objects enforce their invariants. Each layer
protects a different entry point.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateProductRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64, description="Caller-chosen product id")
    name: str = Field(min_length=1, max_length=200, description="Display name")
    total_stock: int = Field(ge=0, description="Units in stock for the whole sale")


class ProductResponse(BaseModel):
    id: str
    name: str
    total: int = Field(description="Units in stock")
    confirmed: int = Field(description="Units sold and paid for")
    reserved: int = Field(description="Units held by reservations that have not lapsed")
    available: int = Field(description="total - confirmed - reserved")


class CreateReservationRequest(BaseModel):
    product_id: str = Field(min_length=1, description="Product to hold stock from")
    quantity: int = Field(ge=1, description="Units to hold")


class ReservationResponse(BaseModel):
    id: str
    product_id: str
    quantity: int
    state: str = Field(description="active, confirmed, cancelled, or expired")
    created_at: datetime
    expires_at: datetime = Field(description="When an active hold lapses")


class ErrorBody(BaseModel):
    code: str = Field(description="Stable identifier; safe to branch on")
    message: str = Field(description="Human-readable detail; may be reworded")


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str
