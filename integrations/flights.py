"""Find live flights on OpenSky and bind an AIR shipment to one.
Used by `manage.py track_flight` and by the UI's "Track a real flight" panel."""
from __future__ import annotations

import re

from django.conf import settings

from core.models import Shipment
from core.services import shipments as shipment_svc
from integrations.opensky import OpenSkyProvider

DEFAULT_BBOX = (10.0, 25.0, 50.0, 65.0)  # Middle East / Gulf / East Med
ICAO24 = re.compile(r"^[0-9a-fA-F]{6}$")
PAD_DEG = 8.0


class FlightError(Exception):
    """Shown to the user (not found, not an AIR shipment, OpenSky unavailable...)."""


def _provider() -> OpenSkyProvider:
    return OpenSkyProvider(settings.OPENSKY_CLIENT_ID, settings.OPENSKY_CLIENT_SECRET)


def bbox_for(shipment: Shipment | None) -> tuple[float, float, float, float]:
    """Box around the shipment's origin and destination (+8°), else the Middle East."""
    pts = [p for p in ((shipment.origin_latlon, shipment.dest_latlon) if shipment else ()) if p]
    if not pts:
        return DEFAULT_BBOX
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return (max(min(lats) - PAD_DEG, -90.0), max(min(lons) - PAD_DEG, -180.0),
            min(max(lats) + PAD_DEG, 90.0), min(max(lons) + PAD_DEG, 180.0))


def find_flights(callsign_prefix: str = "", bbox: tuple[float, float, float, float] | None = DEFAULT_BBOX) -> list[dict]:
    try:
        flights = _provider().find_flights(callsign_prefix=callsign_prefix, bbox=bbox)
    except Exception as exc:  # 429 rate limit, network, ...
        raise FlightError(f"OpenSky is not answering right now ({type(exc).__name__}). Try again in a minute.")
    return sorted((f for f in flights if not f["on_ground"]), key=lambda f: f["callsign"])


def bind(shipment: Shipment, flight: str, *, actor_name: str, actor_user=None) -> dict:
    """Bind `shipment` to a flight given by callsign (UAE9845) or ICAO24 (8960d2);
    stores its current position right away. Returns the flight dict."""
    if shipment.mode != "AIR":
        raise FlightError(f"{shipment.ref} is a {shipment.mode} shipment; OpenSky tracks aircraft only.")
    flight = flight.strip()
    try:
        if ICAO24.match(flight):
            matches = _provider().find_flights(icao24=flight)
        else:
            matches = [f for f in _provider().find_flights(callsign_prefix=flight, bbox=None)
                       if f["callsign"].upper() == flight.upper()]
    except Exception as exc:
        raise FlightError(f"OpenSky is not answering right now ({type(exc).__name__}). Try again in a minute.")
    if not matches:
        raise FlightError(f"{flight} is not airborne / visible on OpenSky right now.")
    f = matches[0]
    shipment_svc.set_tracker(shipment, f["icao24"], actor_name=actor_name, actor_user=actor_user,
                             note=f"bound to {f['callsign'] or f['icao24']} (OpenSky)")
    shipment_svc.record_position(shipment, f["lat"], f["lon"], f["ts"], "opensky",
                                 speed=f["speed_kn"], heading=f["track"])
    return f
