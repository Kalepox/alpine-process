"""DTOs shared between agents, solver, monitor and core.services (contract v2).

Owned by B. Pure Pydantic, no Django imports, so the domain layer and its
tests run without a database. LLM agents use some of these as
`output_schema`; anything an LLM fills is clamped here before code uses it.

Conventions:
- Modes: "SEA" | "RAIL" | "ROAD" | "AIR" (same values as core.models.TransportMode).
- Geometry: GeoJSON order, [lon, lat].
- Money: Decimal EUR. Days: float (P10/P50/P90 come from risk delays).
- Before an option is saved it has no DB id, so options are referenced by `key`
  ("R1", "R2", "WAIT"). save_route_options (A) maps key -> RouteOption.id.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

Mode = Literal["SEA", "RAIL", "ROAD", "AIR"]
CorridorStatus = Literal["OPEN", "RESTRICTED", "CLOSED"]
Severity = Literal["info", "warn", "block"]  # same values as core.models.Severity

MAX_DISRUPTION_DAYS = 120


def _clamp(lo: float, hi: float):
    """Clamp instead of reject: an LLM saying p=1.3 means "very likely", not "drop the assessment"."""

    def clamp(v):
        if v is None:
            return v
        return min(max(float(v), lo), hi)

    return BeforeValidator(clamp)


Probability = Annotated[float, _clamp(0.0, 1.0)]


# ---------------------------------------------------------------- request
class CargoClass(StrEnum):
    """Material type. Drives hard constraints, cost modifiers and weight nudges (domain/cargo.py)."""

    DRY_BULK = "DRY_BULK"                    # copper concentrate, ore
    LIQUID_BULK = "LIQUID_BULK"              # palm oil
    CONTAINER_GENERAL = "CONTAINER_GENERAL"  # cathodes, parts
    DANGEROUS_GOODS = "DANGEROUS_GOODS"      # IMDG / ADR classes
    PERISHABLE_REEFER = "PERISHABLE_REEFER"  # food, pharma
    HIGH_VALUE = "HIGH_VALUE"                # gold
    OVERSIZE = "OVERSIZE"                    # project cargo


class Preset(StrEnum):
    BEST = "BEST"  # default: reliability 0.35, time 0.30, cost 0.20, compliance 0.10, co2 0.05
    FASTEST = "FASTEST"
    MOST_RELIABLE = "MOST_RELIABLE"
    CHEAPEST = "CHEAPEST"
    GREENEST = "GREENEST"


class Weights(BaseModel):
    """Explicit criterion weights. Leave RoutingRequest.weights None to use the preset."""

    reliability: float = Field(ge=0)
    time: float = Field(ge=0)
    cost: float = Field(ge=0)
    compliance: float = Field(ge=0)
    co2: float = Field(ge=0)


class CargoSpec(BaseModel):
    description: str = Field(description="what is shipped, e.g. 'copper cathodes'")
    cargo_class: CargoClass = CargoClass.CONTAINER_GENERAL
    weight_t: float = Field(gt=0, description="tonnes")
    value_eur: Decimal = Field(ge=0)
    dg_class: str | None = Field(None, description="IMDG/ADR class for dangerous goods, e.g. '3'")
    temp_min_c: float | None = Field(None, description="reefer range, perishables only")
    temp_max_c: float | None = None
    max_transit_days: float | None = Field(None, description="hard limit, e.g. shelf life")


class Constraints(BaseModel):
    banned_modes: list[Mode] = Field(default_factory=list)
    max_transfers: int | None = Field(None, ge=0)
    avoid_corridors: list[str] = Field(default_factory=list, description="Corridor codes")


class RoutingRequest(BaseModel):
    """What intent_agent produces from free text, or the /plan form posts directly."""

    origin: str = Field(description="place name or graph node code, e.g. 'Antofagasta'")
    destination: str
    cargo: CargoSpec
    deadline: dt.date | None = None
    urgency: Literal["low", "normal", "high"] = "normal"
    preset: Preset = Preset.BEST
    weights: Weights | None = None
    constraints: Constraints = Field(default_factory=Constraints)


# ---------------------------------------------------------------- risk
class SourceRef(BaseModel):
    title: str = ""
    url: str


class RiskSignal(BaseModel):
    """What risk_scout's structurer outputs per corridor: a classification, not a probability.

    Code maps the level to probabilities (risk.py LEVELS). Numbers only when the news states them.
    """

    corridor: str = Field(description="Corridor code exactly as listed, e.g. 'BAB_EL_MANDEB'")
    status: CorridorStatus = Field(description="CLOSED only if traffic is actually stopped now")
    level: Literal["LOW", "ELEVATED", "HIGH", "SEVERE"] = Field(
        description="LOW: normal; ELEVATED: tensions/incidents, traffic flows; HIGH: attacks/strikes/partial "
                    "stoppages, closure plausible within 30 days; SEVERE: closure imminent or under way")
    reopening_estimate_days: int | None = Field(None, description="only if a source states an estimate")
    war_risk_pct: float | None = Field(None, description="war-risk insurance premium in % of cargo value, only if quoted")
    reason: str = Field("", description="one sentence, with the date of the latest event")


class RiskAssessment(BaseModel):
    """risk_scout's structured output, one per corridor. Validated, then applied by code."""

    corridor: str = Field(description="Corridor code, e.g. 'BAB_EL_MANDEB'")
    status: CorridorStatus
    risk_score: Probability
    p_close_30d: Probability
    expected_duration_days: Annotated[int | None, _clamp(0, MAX_DISRUPTION_DAYS)] = None
    war_risk_pct: Annotated[float, _clamp(0.0, 5.0)] = 0.0  # % of cargo value
    reason: str = ""
    sources: list[SourceRef] = Field(default_factory=list)

    @property
    def verified(self) -> bool:
        """No source means "unverified": it is shown, but it never closes a corridor on its own."""
        return bool(self.sources)


# ---------------------------------------------------------------- routes
class Leg(BaseModel):
    from_node: str
    to_node: str
    mode: Mode
    km: float
    days: float
    corridors: list[str] = Field(default_factory=list)
    geometry: list[list[float]] = Field(default_factory=list, description="[[lon, lat], ...]")


class PossibleDisruption(BaseModel):
    corridor: str
    p_close: Probability
    delay_days: float
    cost_impact: Decimal
    contingency_label: str = ""


class RouteOptionDTO(BaseModel):
    """One option produced by the solver. All numbers come from code."""

    key: str = Field(description="'R1'..'R5' or 'WAIT'; stable within one run")
    kind: Literal["ROUTE", "WAIT"] = "ROUTE"
    label: str
    legs: list[Leg] = Field(default_factory=list)  # empty for WAIT
    transit_days: float
    days_p10: float
    days_p90: float
    delay_days: float = 0.0  # vs the current plan (replan only)
    cost_breakdown: dict[str, Decimal] = Field(default_factory=dict)  # CostCategory keys + "CARRYING" (inventory cost, not invoiced)
    landed_cost: Decimal
    co2_kg: float
    risk_score: Probability
    score: dict[str, float] = Field(default_factory=dict)  # {criterion: 0-1, "total": ...}
    possible_disruptions: list[PossibleDisruption] = Field(default_factory=list)
    contingency_key: str | None = None
    break_even_days: float | None = None  # WAIT: wins if the corridor reopens before this
    infeasible_reason: str = ""  # e.g. WAIT breaks a perishable's max_transit_days
    rationale: str = ""  # filled from AdvisorVerdict
    recommended: bool = False


class Tradeoff(BaseModel):
    option_key: str
    text: str


class AdvisorVerdict(BaseModel):
    """route_advisor's output. Qualitative only: numbers are rendered from RouteOption data."""

    recommended_key: str
    rationale: str
    tradeoffs: list[Tradeoff] = Field(default_factory=list)
    plan_b_required: bool = False
    reroute_or_wait: Literal["REROUTE", "WAIT", "NA"] = "NA"

    def unknown_keys(self, valid: set[str]) -> set[str]:
        """Option keys the advisor cited that the solver never produced (=> reject the verdict)."""
        cited = {self.recommended_key, *(t.option_key for t in self.tradeoffs)}
        return cited - valid


# ---------------------------------------------------------------- monitor
class Disruption(BaseModel):
    """One detector hit on an in-transit shipment's remaining route."""

    kind: Literal["CORRIDOR", "TRACKING", "WEATHER", "ROAD_NEWS"]
    alert_code: str  # core.models.Alert.Code value
    severity: Severity
    shipment_id: int
    leg_index: int
    corridor: str | None = None
    delay_days_est: float = 0.0
    evidence: dict = Field(default_factory=dict)  # raw values, e.g. {"wave_height_m": 7.2, "at": [lon, lat]}
    sources: list[SourceRef] = Field(default_factory=list)

    @property
    def needs_replan(self) -> bool:
        return self.severity == "block" or self.delay_days_est >= 1


# ---------------------------------------------------------------- expenses (secondary track)
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


class CostBreakdown(BaseModel):
    items: dict[str, Decimal] = Field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return sum(self.items.values(), Decimal("0"))
