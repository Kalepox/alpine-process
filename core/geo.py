"""Tiny geometry helpers (no GIS dependency). Owned by A."""
from __future__ import annotations

import math

EARTH_KM = 6371.0


def haversine_km(a: list[float], b: list[float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def bearing_deg(a: list[float], b: list[float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def shipment_path(shipment) -> list[list[float]]:
    """origin -> corridor waypoints -> destination, as [[lat, lon], ...]."""
    path = []
    if shipment.origin_latlon:
        path.append(list(shipment.origin_latlon))
    if shipment.corridor_id:
        path.extend(list(p) for p in shipment.corridor.waypoints or [])
    if shipment.dest_latlon:
        path.append(list(shipment.dest_latlon))
    return path


def point_along(path: list[list[float]], fraction: float) -> tuple[list[float], float]:
    """Point at `fraction` (0..1) of the path length, plus the heading there."""
    if not path:
        raise ValueError("empty path")
    if len(path) == 1:
        return list(path[0]), 0.0
    fraction = min(max(fraction, 0.0), 1.0)
    legs = [haversine_km(path[i], path[i + 1]) for i in range(len(path) - 1)]
    target = sum(legs) * fraction
    for i, leg in enumerate(legs):
        if target <= leg or i == len(legs) - 1:
            t = 0.0 if leg == 0 else min(target / leg, 1.0)
            a, b = path[i], path[i + 1]
            return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], bearing_deg(a, b)
        target -= leg
    return list(path[-1]), 0.0
