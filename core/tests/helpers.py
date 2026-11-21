"""Shared test data / builders (import these; fixtures live in conftest.py)."""
from decimal import Decimal

from autopilot.domain.schemas import RouteOptionDTO

# Belawan -> Bab-el-Mandeb -> Suez -> Mersin, and the Cape alternative from the Indian Ocean
RED_SEA_PATH = [[3.78, 98.69], [5.9, 95.3], [12.0, 51.0], [12.6, 43.4], [27.9, 33.6], [31.3, 32.3], [36.78, 34.63]]
CAPE_PATH = [[5.9, 80.0], [-34.8, 20.0], [14.9, -17.5], [36.0, -6.0], [37.3, 11.0], [36.78, 34.63]]


def route_dto(label, path, codes, *, days="23", breakdown=None, kind="ROUTE", recommended=False, key=None, **extra):
    """A B-format RouteOptionDTO; `path` is [[lat, lon], ...] (None for a WAIT option without legs)."""
    legs = [{"from_node": "A", "to_node": "B", "mode": "SEA", "km": 9000, "days": float(days), "corridors": codes,
             "geometry": [[p[1], p[0]] for p in path]}] if path else []
    return RouteOptionDTO.model_validate(dict(
        key=key or label[:12], kind=kind, label=label, legs=legs, transit_days=float(days),
        days_p10=float(days) - 1, days_p90=float(days) + 2, landed_cost=Decimal("0"), co2_kg=0.0, risk_score=0.0,
        cost_breakdown=breakdown or {}, recommended=recommended, **extra,
    ))
