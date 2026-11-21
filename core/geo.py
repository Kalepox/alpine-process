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


# --------------------------------------------------------------------------
# GeoJSON <-> [lat, lon]. GeoJSON (RouteOption.geometry, Corridor.geometry) is
# [lon, lat]; positions, Shipment.origin_latlon and C's map payload are [lat, lon].
# --------------------------------------------------------------------------
def linestring(path_latlon: list[list[float]]) -> dict:
    return {"type": "LineString", "coordinates": [[p[1], p[0]] for p in path_latlon]}


def feature_collection(paths_latlon: list[list[list[float]]], legs: list[dict] | None = None) -> dict:
    """One LineString Feature per leg; properties carry the leg index and mode."""
    legs = legs or []
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"leg": i, "mode": (legs[i] if i < len(legs) else {}).get("mode", "")},
         "geometry": linestring(path)}
        for i, path in enumerate(paths_latlon)
    ]}


def normalize_geometry(geometry) -> dict:
    """Accept a FeatureCollection, Feature, LineString or list of LineStrings; return a FeatureCollection."""
    if not geometry:
        return {"type": "FeatureCollection", "features": []}
    if isinstance(geometry, list):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"leg": i}, "geometry": g} for i, g in enumerate(geometry)]}
    kind = geometry.get("type")
    if kind == "FeatureCollection":
        return geometry
    if kind == "Feature":
        return {"type": "FeatureCollection", "features": [geometry]}
    if kind in ("LineString", "MultiLineString"):
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"leg": 0}, "geometry": geometry}]}
    raise ValueError(f"unsupported geometry type {kind!r}")


def leg_paths(geometry) -> list[list[list[float]]]:
    """Per-leg [[lat, lon], ...] from any supported geometry."""
    out = []
    for feature in normalize_geometry(geometry)["features"]:
        g = feature.get("geometry") or {}
        if g.get("type") == "LineString":
            out.append([[c[1], c[0]] for c in g["coordinates"]])
        elif g.get("type") == "MultiLineString":
            out.append([[c[1], c[0]] for line in g["coordinates"] for c in line])
    return out


def joined_path(paths: list[list[list[float]]]) -> list[list[float]]:
    """Concatenate leg paths, dropping the duplicated junction points."""
    out: list[list[float]] = []
    for path in paths:
        for p in path:
            if not out or haversine_km(out[-1], p) > 0.01:
                out.append(list(p))
    return out


def route_path(route) -> list[list[float]]:
    """Whole route as one [[lat, lon], ...] path."""
    return joined_path(leg_paths(route.geometry))


def legs_latlon(route) -> list[dict]:
    """[{mode, coords: [[lat, lon], ...]}] per leg: the shape C's map.js draws."""
    legs = route.legs or []
    return [{"mode": (legs[i] if i < len(legs) else {}).get("mode", ""), "coords": path}
            for i, path in enumerate(leg_paths(route.geometry))]


def shipment_path(shipment) -> list[list[float]]:
    """The approved route's path, or a straight origin -> destination line before one exists."""
    route = shipment.route if shipment.route_id else None
    if route is not None:
        path = route_path(route)
        if len(path) >= 2:
            return path
    return [list(p) for p in (shipment.origin_latlon, shipment.dest_latlon) if p]


def locate_on_path(path: list[list[float]], point: list[float]) -> tuple[float, float, int]:
    """Project `point` onto `path`. Returns (km from start, total km, segment index).

    Uses a local equirectangular projection per segment: plenty for progress bars.
    """
    if len(path) < 2:
        return 0.0, 0.0, 0
    best = (float("inf"), 0.0, 0)  # (distance to path, km along, segment)
    along = 0.0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        leg = haversine_km(a, b)
        k = math.cos(math.radians((a[0] + b[0]) / 2))
        ax, ay, bx, by, px, py = a[1] * k, a[0], b[1] * k, b[0], point[1] * k, point[0]
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        proj = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
        dist = haversine_km(point, proj)
        if dist < best[0]:
            best = (dist, along + leg * t, i)
        along += leg
    return best[1], along, best[2]


def leg_index_at(route, km_along: float) -> int:
    """Which leg of `route` contains the point `km_along` km from its start."""
    total = 0.0
    paths = leg_paths(route.geometry)
    for i, path in enumerate(paths):
        total += path_length_km(path)
        if km_along <= total + 0.01:
            return i
    return max(len(paths) - 1, 0)


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
