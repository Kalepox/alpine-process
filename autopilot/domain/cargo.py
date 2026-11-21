"""Material type -> constraints, cost modifiers, weight nudges (PLAN_B §B2.3). Owned by B.

Pure data + small pure functions. No networkx here: network.py applies the
constraints, scoring.py applies the modifiers and nudges.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import CargoClass, CargoSpec, RoutingRequest
from .tables import AIR, BASE_INSURANCE_PCT, RAIL

CRITERIA = ("reliability", "time", "cost", "compliance", "co2")

# IMDG/ADR classes that may not fly as cargo in our model (simplified IATA DGR).
AIR_BANNED_DG_CLASSES = {"1", "2.1", "2.3", "4.2", "4.3", "6.2", "7"}


@dataclass(frozen=True)
class CargoProfile:
    banned_modes: frozenset[str] = frozenset()
    max_transfers: int | None = None
    adr_tunnels_banned: bool = False
    handling_factor: float = 1.0            # x terminal handling cost
    surcharge_eur_per_t: float = 0.0        # once per shipment (DG paperwork, permits)
    per_transfer_eur_per_t: float = 0.0     # e.g. tank cleaning
    per_day_eur_per_t: float = 0.0          # e.g. reefer power/monitoring
    insurance_pct: float = BASE_INSURANCE_PCT
    escort_eur_per_day: float = 0.0         # security, per shipment
    co2_factor: float = 1.0                 # e.g. reefer uplift
    road_km_soft_limit: float | None = None  # longer road legs count as a cargo-fit mismatch
    weight_nudges: dict[str, float] = field(default_factory=dict)


CARGO_PROFILES: dict[CargoClass, CargoProfile] = {
    CargoClass.DRY_BULK: CargoProfile(
        banned_modes=frozenset({AIR}), road_km_soft_limit=500, weight_nudges={"cost": 0.05},
    ),
    CargoClass.LIQUID_BULK: CargoProfile(
        banned_modes=frozenset({AIR}), per_transfer_eur_per_t=6.0, road_km_soft_limit=800,
    ),
    CargoClass.CONTAINER_GENERAL: CargoProfile(),
    CargoClass.DANGEROUS_GOODS: CargoProfile(
        max_transfers=2, adr_tunnels_banned=True, handling_factor=1.5, surcharge_eur_per_t=25.0,
        weight_nudges={"reliability": 0.05},
    ),
    CargoClass.PERISHABLE_REEFER: CargoProfile(
        per_day_eur_per_t=4.0, co2_factor=1.25, weight_nudges={"time": 0.10},
    ),
    CargoClass.HIGH_VALUE: CargoProfile(
        max_transfers=1, insurance_pct=0.50, escort_eur_per_day=600.0, road_km_soft_limit=300,
        weight_nudges={"reliability": 0.10},
    ),
    CargoClass.OVERSIZE: CargoProfile(
        banned_modes=frozenset({AIR, RAIL}), surcharge_eur_per_t=40.0,
    ),
}


@dataclass(frozen=True)
class EffectiveConstraints:
    """Request constraints merged with the cargo profile: what the solver must obey."""

    banned_modes: frozenset[str]
    avoid_corridors: frozenset[str]
    max_transfers: int | None
    adr_tunnels_banned: bool
    max_transit_days: float | None


def profile_for(cargo: CargoSpec) -> CargoProfile:
    return CARGO_PROFILES[cargo.cargo_class]


def effective_constraints(request: RoutingRequest) -> EffectiveConstraints:
    cargo, c = request.cargo, request.constraints
    profile = profile_for(cargo)
    banned = set(profile.banned_modes) | set(c.banned_modes)
    if cargo.cargo_class is CargoClass.DANGEROUS_GOODS and (cargo.dg_class is None or cargo.dg_class in AIR_BANNED_DG_CLASSES):
        banned.add(AIR)  # unknown DG class: assume it cannot fly
    transfer_limits = [x for x in (profile.max_transfers, c.max_transfers) if x is not None]
    return EffectiveConstraints(
        banned_modes=frozenset(banned),
        avoid_corridors=frozenset(c.avoid_corridors),
        max_transfers=min(transfer_limits) if transfer_limits else None,
        adr_tunnels_banned=profile.adr_tunnels_banned,
        max_transit_days=cargo.max_transit_days,
    )


def nudge_weights(weights: dict[str, float], cargo_class: CargoClass) -> dict[str, float]:
    """Add the cargo's nudges to the preset weights, then renormalize to sum 1."""
    nudged = {k: weights.get(k, 0.0) + CARGO_PROFILES[cargo_class].weight_nudges.get(k, 0.0) for k in CRITERIA}
    total = sum(nudged.values())
    return {k: v / total for k, v in nudged.items()}
