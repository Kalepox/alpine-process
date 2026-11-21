"""DTOs shared between agents, rules and core.services (DEV_PLAN 0.10 / B.2).

Owned by B. Seeded from the contract so A's service signatures and C's
views can import the names from hour 0. No Django imports here.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class ExtractedExpense(BaseModel):
    vendor_name: str | None = None
    vendor_vat: str | None = None
    doc_type: str | None = None
    number: str | None = None
    date: dt.date | None = None
    currency: str = "EUR"
    net: Decimal | None = None
    vat: Decimal | None = None
    total: Decimal | None = None
    iban: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    shipment_hint: str | None = Field(None, description="vessel name / AWB / B/L number")
    line_items: list[LineItem] = Field(default_factory=list)
    confidence: float = 0.0


class IssueDraft(BaseModel):
    code: str
    severity: Literal["warn", "block"] = "block"
    message: str
    facts: dict = Field(default_factory=dict)
    suggested_action: str = ""


class RouteOptionDTO(BaseModel):
    label: str
    corridor_code: str | None = None
    mode: Literal["SEA", "AIR", "ROAD", "RAIL"]
    waypoints: list[list[float]] = Field(default_factory=list)
    transit_days: int
    delay_days: int
    cost_breakdown: dict[str, Decimal] = Field(default_factory=dict)
    total_extra_cost: Decimal
    rationale: str = ""


class CostBreakdown(BaseModel):
    items: dict[str, Decimal] = Field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return sum(self.items.values(), Decimal("0"))
