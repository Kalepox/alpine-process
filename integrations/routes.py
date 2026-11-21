"""RoutesProvider: road legs. Owned by A.

Trucks don't broadcast positions we can read, so a road shipment's position is
simulated along its road route at real time, and the ETA comes from the route:

  * with GOOGLE_MAPS_API_KEY: Google Routes API computeRoutes (DRIVE), route
    polyline + duration, cached per shipment
  * without a key / on error: straight line x ROAD_FACTOR at FALLBACK_KMH

Speeds are km/h (Position.speed for ROAD).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta

import requests
from django.utils import timezone

from core.geo import haversine_km, path_length_km, remaining_path, walk
from core.models import Shipment
from integrations.base import PositionDTO, TrackingProvider

API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"
ROAD_FACTOR = 1.3  # straight line -> road distance
FALLBACK_KMH = 70.0
CACHE_SECONDS = 1800
MAX_STEP_SECONDS = 600


def decode_polyline(encoded: str) -> list[list[float]]:
    """Google encoded polyline -> [[lat, lon], ...]."""
    points, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        for is_lat in (True, False):
            shift, result = 0, 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += delta
            else:
                lon += delta
        points.append([lat / 1e5, lon / 1e5])
    return points


@dataclass
class RoadRoute:
    path: list[list[float]]
    distance_km: float
    duration_s: float
    source: str  # "google" | "fallback"

    @property
    def kmh(self) -> float:
        """Average road speed (what we report as Position.speed)."""
        return self.distance_km / (self.duration_s / 3600) if self.duration_s else FALLBACK_KMH

    @property
    def path_kmh(self) -> float:
        """Speed along `path` itself. Differs from kmh when the path is a
        straight-line fallback shorter than the road distance."""
        length = path_length_km(self.path)
        return length / (self.duration_s / 3600) if self.duration_s and length else self.kmh


class RoutesProvider(TrackingProvider):
    name = "routes"
    mode = "ROAD"

    def __init__(self, api_key: str = "", session: requests.Session | None = None) -> None:
        super().__init__()
        self.api_key = api_key
        self.session = session or requests.Session()
        self._routes: dict[int, tuple[float, RoadRoute]] = {}
        self._etas: dict = {}

    def healthy(self) -> bool:
        return True  # the straight-line fallback always works

    def etas(self) -> dict:
        return dict(self._etas)

    # ---------------------------------------------------------------- routing
    def route(self, origin: list[float], dest: list[float]) -> RoadRoute:
        if self.api_key:
            try:
                return self._google_route(origin, dest)
            except Exception as exc:  # fall back, but let the collector see the failure count
                self.record_failure(exc)
        km = haversine_km(origin, dest) * ROAD_FACTOR
        return RoadRoute(path=[list(origin), list(dest)], distance_km=km, duration_s=km / FALLBACK_KMH * 3600,
                         source="fallback")

    def _google_route(self, origin, dest) -> RoadRoute:
        def waypoint(p):
            return {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}

        resp = self.session.post(
            API_URL,
            json={"origin": waypoint(origin), "destination": waypoint(dest), "travelMode": "DRIVE",
                  "routingPreference": "TRAFFIC_AWARE"},
            headers={"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": FIELD_MASK},
            timeout=20,
        )
        resp.raise_for_status()
        routes = resp.json().get("routes") or []
        if not routes:
            raise ValueError("Routes API returned no route")
        r = routes[0]
        path = decode_polyline(r["polyline"]["encodedPolyline"])
        self.record_success()
        return RoadRoute(path=path, distance_km=r.get("distanceMeters", 0) / 1000,
                         duration_s=float(str(r.get("duration", "0s")).rstrip("s")), source="google")

    def _route_for(self, shipment_pk: int, origin: list[float], dest: list[float]) -> RoadRoute:
        cached = self._routes.get(shipment_pk)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        route = self.route(origin, dest)
        self._routes[shipment_pk] = (time.monotonic(), route)
        return route

    # ------------------------------------------------------------------ fetch
    def fetch(self, tracker_ids: list[str]) -> list[PositionDTO]:
        now = timezone.now()
        out = []
        shipments = Shipment.objects.filter(mode="ROAD", tracker_id__in=[t for t in tracker_ids if t])
        for shipment in shipments:
            origin, dest = shipment.origin_latlon, shipment.dest_latlon
            if not (origin and dest):
                continue
            route = self._route_for(shipment.pk, origin, dest)
            latest = shipment.positions.order_by("-ts").first()
            if latest is None:
                anchor, step_s = route.path[0], 0.0
            else:
                anchor = [latest.lat, latest.lon]
                step_s = min(max((now - latest.ts).total_seconds(), 0.0), MAX_STEP_SECONDS)
            ahead = remaining_path(route.path, anchor)
            (lat, lon), heading, arrived = walk(ahead, route.path_kmh * step_s / 3600)
            remaining_km = path_length_km(remaining_path(route.path, [lat, lon]))
            self._etas[shipment.tracker_id] = now + timedelta(hours=remaining_km / route.path_kmh)
            out.append(PositionDTO(
                tracker_id=shipment.tracker_id, lat=lat, lon=lon, ts=now,
                speed=0.0 if arrived else round(route.kmh, 1), heading=round(heading, 1), source="routes",
            ))
        return out
