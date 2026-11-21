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


def path_length_km(path: list[list[float]]) -> float:
    return sum(haversine_km(path[i], path[i + 1]) for i in range(len(path) - 1))


def remaining_path(path: list[list[float]], anchor: list[float]) -> list[list[float]]:
    """[anchor] + the part of `path` still ahead of it.

    Uses the nearest vertex; if the anchor is already past that vertex (closer
    to the next one than the vertex is), the vertex is skipped.
    """
    if not path:
        return [list(anchor)]
    i = min(range(len(path)), key=lambda k: haversine_km(anchor, path[k]))
    if i + 1 < len(path) and haversine_km(anchor, path[i + 1]) < haversine_km(path[i], path[i + 1]):
        i += 1  # anchor is between vertex i and i+1: vertex i is behind us
    return [list(anchor)] + [list(p) for p in path[i:]]


def walk(path: list[list[float]], km: float) -> tuple[list[float], float, bool]:
    """Move `km` along `path` from its first point.

    Returns (point, heading_deg, reached_end). Never overshoots the last point.
    """
    if len(path) < 2:
        return list(path[0]), 0.0, True
    heading = bearing_deg(path[0], path[1])
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        leg = haversine_km(a, b)
        if leg > 0:
            heading = bearing_deg(a, b)
        if km <= leg:
            t = 0.0 if leg == 0 else km / leg
            return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], heading, False
        km -= leg
    return list(path[-1]), heading, True
