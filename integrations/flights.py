"""Real flights for AIR shipments: find them (OpenSky), follow one, and make the
shipment's route match it (start -> stopovers -> end airports).

Used by `manage.py track_flight` and by the UI's "Track a real flight" panel.

Route suggestions come from adsbdb.com (free, no key). Cargo callsigns are often
reused on different routes, so a suggestion is checked against where the plane
actually is, and the user confirms or edits the airports before the route changes.
"""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.geo import distance_to_path_km, great_circle, joined_path, locate_on_path, path_length_km
from core.models import AgentRun, Shipment
from core.services import approvals, audit, shipments as shipment_svc
from integrations import airports
from integrations.opensky import OpenSkyProvider

DEFAULT_BBOX = (10.0, 25.0, 50.0, 65.0)  # Middle East / Gulf / East Med
ICAO24 = re.compile(r"^[0-9a-fA-F]{6}$")
PAD_DEG = 8.0
ADSBDB_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"
ON_ROUTE_KM = 400  # the plane must be this close to a suggested route for it to count as a match
CRUISE_KMH = 850
STOP_HOURS = 3  # ground time per stopover


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


# --------------------------------------------------------------------------- find / follow
def find_flights(callsign_prefix: str = "", bbox: tuple[float, float, float, float] | None = DEFAULT_BBOX) -> list[dict]:
    try:
        flights = _provider().find_flights(callsign_prefix=callsign_prefix, bbox=bbox)
    except Exception as exc:  # 429 rate limit, network, ...
        raise FlightError(f"OpenSky is not answering right now ({type(exc).__name__}). Try again in a minute.")
    return sorted((f for f in flights if not f["on_ground"]), key=lambda f: f["callsign"])


def route_path(airport_list: list[dict]) -> list[list[float]]:
    return joined_path([great_circle([a["lat"], a["lon"]], [b["lat"], b["lon"]])
                        for a, b in zip(airport_list, airport_list[1:])])


def lookup_route(callsign: str) -> list[str]:
    """Suggested airports for a callsign from adsbdb ([] if unknown / unreachable)."""
    if not callsign:
        return []
    try:
        resp = requests.get(ADSBDB_URL.format(callsign=callsign.strip().upper()), timeout=10,
                            headers={"User-Agent": "monora-demo"})
        if resp.status_code != 200:
            return []
        fr = (resp.json().get("response") or {}).get("flightroute") or {}
    except (requests.RequestException, ValueError):
        return []
    codes = []
    for key in ("origin", "midpoint", "destination"):
        a = fr.get(key) or {}
        code = a.get("iata_code") or a.get("icao_code")
        if code and airports.get(code) and (not codes or codes[-1] != code.upper()):
            codes.append(code.upper())
    return codes if len(codes) >= 2 and codes[0] != codes[-1] else []


def check_route(codes: list[str], lat: float, lon: float) -> dict:
    """Does the plane's position fit this airport chain?"""
    try:
        path = route_path(airports.resolve(codes))
    except ValueError:
        return {"codes": codes, "on_route": False, "distance_km": None}
    km = distance_to_path_km(path, [lat, lon])
    return {"codes": codes, "on_route": km <= ON_ROUTE_KM, "distance_km": round(km)}


def bind(shipment: Shipment, flight: str, *, actor_name: str, actor_user=None) -> dict:
    """Follow a flight given by callsign (UAE9845) or ICAO24 (8960d2): store its position
    and a route suggestion (checked against the position). Returns the flight dict + "suggestion"."""
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
    suggested = lookup_route(f["callsign"])
    suggestion = check_route(suggested, f["lat"], f["lon"]) if suggested else {"codes": [], "on_route": False,
                                                                              "distance_km": None}
    info = {"callsign": f["callsign"], "icao24": f["icao24"], "suggested_route": suggestion["codes"],
            "suggestion_on_route": suggestion["on_route"], "suggestion_distance_km": suggestion["distance_km"],
            "suggestion_source": "adsbdb.com", "bound_at": timezone.now()}
    shipment_svc.set_tracker(shipment, f["icao24"], actor_name=actor_name, actor_user=actor_user,
                             note=f"bound to {f['callsign'] or f['icao24']} (OpenSky)", info=info)
    shipment_svc.record_position(shipment, f["lat"], f["lon"], f["ts"], "opensky",
                                 speed=f["speed_kn"], heading=f["track"])
    return {**f, "suggestion": suggestion}


# --------------------------------------------------------------------------- route = the flight's airports
def align_route(shipment: Shipment, codes: list[str], *, user, note: str = "") -> Shipment:
    """Make the shipment's route the flight's airports: start, stopovers, end.

    Saved like any route (AgentRun + RouteOption) and approved through
    approvals.approve_route, so it needs core.approve_route and shows in the trail.
    """
    if shipment.mode != "AIR":
        raise FlightError(f"{shipment.ref} is not an air shipment.")
    if len(codes) < 2:
        raise FlightError("Give at least a start and an end airport, e.g. DWC LGG ORD.")
    try:
        chain = airports.resolve(codes)
    except ValueError as exc:
        raise FlightError(str(exc))
    legs, paths = [], []
    for a, b in zip(chain, chain[1:]):
        p = great_circle([a["lat"], a["lon"]], [b["lat"], b["lon"]])
        km = path_length_km(p)
        legs.append({"from_node": airports.label(a), "to_node": airports.label(b), "mode": "AIR", "km": round(km),
                     "days": round(km / CRUISE_KMH / 24, 2), "corridors": [],
                     "geometry": [[pt[1], pt[0]] for pt in p]})  # B's Leg: GeoJSON [lon, lat]
        paths.append(p)
    whole = joined_path(paths)

    # time left: from where the plane is now, plus ground time at stopovers still ahead
    pos = shipment_svc.current_position(shipment)
    km_done = 0.0
    if pos is not None:
        km_done, _, _ = locate_on_path(whole, [pos.lat, pos.lon])
    remaining_km = max(path_length_km(whole) - km_done, 0.0)
    stops_ahead, acc = 0, 0.0
    for leg in legs[:-1]:
        acc += leg["km"]
        stops_ahead += acc > km_done
    hours = remaining_km / CRUISE_KMH + STOP_HOURS * stops_ahead
    label = " → ".join(airports.code_of(a) for a in chain)

    from autopilot.domain.schemas import RouteOptionDTO

    with transaction.atomic():
        run = audit.start_run(
            AgentRun.Pipeline.REPLAN if shipment.status != Shipment.Status.PLANNING else AgentRun.Pipeline.PLAN_ROUTES,
            trigger=AgentRun.Trigger.USER, triggered_by=user, shipment=shipment,
            input={"airports": [airports.code_of(a) for a in chain], "tracker": shipment.tracker_id},
            model="airport codes (no LLM)", prompt_version="flight-route",
        )
        days = max(round(hours / 24, 1), 0.1)
        [option] = shipment_svc.save_route_options(shipment, [RouteOptionDTO.model_validate(dict(
            key="F1", label=f"Flight route {label}", legs=legs, transit_days=days, days_p10=days, days_p90=days,
            landed_cost=shipment.cargo_value, co2_kg=0.0, risk_score=0.0,
            rationale=f"Airports of the real flight {shipment.tracker_id} ({note or 'entered by the user'}).",
            recommended=True,
        ))], run=run, actor_name="web.flights")
        audit.finish_run(run, AgentRun.Status.OK, summary=label)
        first, last = chain[0], chain[-1]
        shipment_svc.update_endpoints(
            shipment, origin=f"{airports.label(first)}, {first['country']}",
            origin_latlon=[first["lat"], first["lon"]],
            destination=f"{airports.label(last)}, {last['country']}",
            dest_latlon=[last["lat"], last["lon"]], actor_name=user.get_username(), actor_user=user,
            note=f"aligned to flight route {label}",
        )
        approvals.approve_route(option.pk, user, note=f"flight route {label}")
        s = Shipment.objects.get(pk=shipment.pk)
        if pos is not None:
            shipment_svc.update_progress(s, pos.lat, pos.lon)
        shipment_svc.update_eta(s, timezone.now() + timedelta(hours=hours), source="flight route",
                                actor_name=user.get_username())
    s.refresh_from_db()
    return s
