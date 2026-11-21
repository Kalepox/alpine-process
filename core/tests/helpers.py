"""Shared test data / builders (import these; fixtures live in conftest.py)."""
from decimal import Decimal

from autopilot.domain.schemas import RouteOptionDTO
from core.geo import feature_collection

# Belawan -> Bab-el-Mandeb -> Suez -> Mersin, and the Cape alternative from the Indian Ocean
RED_SEA_PATH = [[3.78, 98.69], [5.9, 95.3], [12.0, 51.0], [12.6, 43.4], [27.9, 33.6], [31.3, 32.3], [36.78, 34.63]]
CAPE_PATH = [[5.9, 80.0], [-34.8, 20.0], [14.9, -17.5], [36.0, -6.0], [37.3, 11.0], [36.78, 34.63]]


def route_dto(label, path, codes, *, days="23", breakdown=None, kind="ROUTE", recommended=False, **extra):
    leg = {"from": "A", "to": "B", "mode": "SEA", "km": 9000, "days": float(days), "corridors": codes}
    return RouteOptionDTO.model_validate(dict(
        kind=kind, label=label, legs=[leg], geometry=feature_collection([path], [leg]) if path else {},
        transit_days=Decimal(days), cost_breakdown=breakdown or {}, recommended=recommended, **extra,
    ))
