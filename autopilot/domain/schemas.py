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


class RouteLeg(BaseModel):
    """legs[i] of a RouteOption. `corridors` are Corridor codes (chokepoints) the leg passes."""

    from_: str = Field(alias="from")
    to: str
    mode: Literal["SEA", "AIR", "ROAD", "RAIL"]
    km: float = 0
    days: float = 0
    corridors: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class RouteOptionDTO(BaseModel):
    """What the solver/advisor hands to core.services.shipments.save_route_options (v2 §0.7).

    geometry: GeoJSON, coordinates [lon, lat]: a FeatureCollection with one LineString
              per leg (a single LineString / list of LineStrings is accepted too).
    transit_days: days from now (plan: from departure; replan/wait: from the cargo's
              current position). approve_route sets eta_current = now + transit_days.
    cost_breakdown: FULL landed cost per category for this route (not a delta).
    contingency: index of another option in the same list (e.g. the Cape fallback).
    """

    kind: Literal["ROUTE", "WAIT"] = "ROUTE"
    label: str
    legs: list[RouteLeg] = Field(default_factory=list)
    geometry: dict | list = Field(default_factory=dict)
    transit_days: Decimal
    days_p10: Decimal | None = None
    days_p90: Decimal | None = None
    delay_days: Decimal = Decimal("0")
    cost_breakdown: dict[str, Decimal] = Field(default_factory=dict)
    landed_cost: Decimal = Decimal("0")
    co2_kg: Decimal = Decimal("0")
    risk_score: Decimal = Decimal("0")
    score: dict = Field(default_factory=dict)
    possible_disruptions: list[dict] = Field(default_factory=list)
    contingency: int | None = None
    rationale: str = ""
    recommended: bool = False

    def model_dump(self, **kwargs):  # legs keep their "from" key for the database
        kwargs.setdefault("by_alias", True)
        return super().model_dump(**kwargs)


class CostBreakdown(BaseModel):
    items: dict[str, Decimal] = Field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return sum(self.items.values(), Decimal("0"))
