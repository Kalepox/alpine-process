"""Shipments, positions, alerts, corridors, route options (DEV_PLAN v2 §0.8). Owned by A."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from core.geo import leg_index_at, locate_on_path, normalize_geometry, shipment_path
from core.models import AgentRun, Alert, Corridor, Position, RouteOption, Severity, Shipment
from core.services import audit

if TYPE_CHECKING:
    from autopilot.domain.schemas import RouteOptionDTO

# Alerts a fresh *real* position automatically clears.
CLEARED_BY_POSITION = {Alert.Code.AIS_SIGNAL_LOST}
LIVE_SOURCES = {Position.Source.AISSTREAM, Position.Source.OPENSKY, Position.Source.ROUTES}
CORRIDOR_ALERTS = [Alert.Code.CORRIDOR_CLOSED, Alert.Code.CORRIDOR_AT_RISK]
KNOTS_TO_KMH = 1.852
ETA_AUDIT_THRESHOLD_SECONDS = 1800  # don't flood the trail with minute-level ETA jitter
NOT_IN_TRANSIT = (Shipment.Status.PLANNING, Shipment.Status.ARRIVED)


def _actor_type_for(actor_name: str, default: str = "system") -> str:
    return "agent" if actor_name.startswith("agent") else default


def current_position(shipment: Shipment) -> Position | None:
    """Where the shipment is, for the UI and for estimating forward.

    With a real fix less than an hour old: the newest position *stored since* that fix
    (the fix itself, or an estimate made from it), so an older estimate never hides a
    real fix. Otherwise the latest position."""
    from django.conf import settings

    cutoff = timezone.now() - timedelta(seconds=settings.INTERPOLATE_AFTER_SECONDS)
    live = shipment.positions.filter(source__in=LIVE_SOURCES, ts__gte=cutoff).order_by("-ts").first()
    if live is not None:
        return shipment.positions.filter(pk__gte=live.pk).order_by("-pk").first()
    return shipment.positions.order_by("-ts").first()


def speed_kmh(speed: float | None, mode: str) -> float | None:
    """Position.speed (knots for SEA/AIR, km/h otherwise) -> km/h."""
    if speed is None:
        return None
    return speed * KNOTS_TO_KMH if mode in ("SEA", "AIR") else speed


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------
REQUEST_FIELDS = ["mode", "cargo", "quantity", "unit", "cargo_value", "origin", "destination",
                  "origin_latlon", "dest_latlon", "hs_code", "origin_country", "incoterm"]


def _place_latlon(name: str) -> list[float] | None:
    """Coordinates of a place B's transport network knows ("Antofagasta", "Milan plant")."""
    try:
        from autopilot.domain.network import LOCATIONS, default_graph
    except Exception:
        return None
    try:
        loc = LOCATIONS[default_graph().resolve(name)]
    except Exception:
        return None
    return [loc.lat, loc.lon]


def create_planning_shipment(request: dict, *, user) -> Shipment:
    """perm core.add_shipment. A shipment in PLANNING (no route yet); the request is kept
    in Shipment.request for plan_routes (B).

    Accepts B's RoutingRequest dict ({"origin", "destination", "cargo": {"description",
    "cargo_class", "weight_t", "value_eur"}, "deadline", "preset", ...}, optionally with
    "text") and the flat form ({"cargo": "Copper", "quantity", "cargo_value", "needed_by", ...}).
    Extra keys (ref, mode, origin_latlon, hs_code ...) fill the Shipment and are kept.
    Missing coordinates are looked up in B's network so the map can show the shipment.
    """
    if user is None or not user.is_authenticated or not user.has_perm("core.add_shipment"):
        raise PermissionDenied(f"user {getattr(user, 'username', None)!r} lacks permission core.add_shipment")
    request = dict(request)
    cargo = request.get("cargo")
    if isinstance(cargo, dict):  # B's RoutingRequest
        flat = {"cargo": cargo.get("description", ""), "quantity": cargo.get("weight_t"), "unit": "mt",
                "cargo_value": cargo.get("value_eur"), "needed_by": request.get("deadline")}
    else:  # flat form: also store it as a RoutingRequest so B can plan it
        flat = {"cargo": cargo, "quantity": request.get("quantity"), "unit": request.get("unit", "mt"),
                "cargo_value": request.get("cargo_value"), "needed_by": request.get("needed_by")}
        weight_t = float(request.get("quantity") or 0) / (1000 if request.get("unit") == "kg" else 1)
        request["cargo"] = {"description": cargo or "", "weight_t": weight_t or 1.0,
                            "value_eur": str(request.get("cargo_value") or 0)}
        if request.get("needed_by") and not request.get("deadline"):
            request["deadline"] = request["needed_by"]
    for key in ("cargo", "origin", "destination"):
        if not (flat.get(key) if key == "cargo" else request.get(key)):
            raise ValueError(f"routing request needs {key!r}")

    fields = {k: request[k] for k in REQUEST_FIELDS if k not in ("cargo", "quantity", "unit", "cargo_value")
              and request.get(k) not in (None, "")}
    fields.setdefault("mode", "SEA")
    fields["origin_latlon"] = fields.get("origin_latlon") or _place_latlon(request["origin"])
    fields["dest_latlon"] = fields.get("dest_latlon") or _place_latlon(request["destination"])
    needed_by = flat.get("needed_by")
    ref = request.get("ref") or f"{flat['cargo']} · {request['origin']} → {request['destination']}"

    with transaction.atomic():
        shipment = Shipment.objects.create(
            ref=ref[:80], status=Shipment.Status.PLANNING, request=audit.json_safe(request),
            deadline=datetime.fromisoformat(needed_by).date() if isinstance(needed_by, str) else needed_by,
            cargo=flat["cargo"], quantity=Decimal(str(flat.get("quantity") or 0)), unit=flat.get("unit") or "mt",
            cargo_value=Decimal(str(flat.get("cargo_value") or 0)), **fields,
        )
        audit.log(
            "shipment.planning_created", actor_type="user", actor_name=user.get_username(), actor_user=user,
            shipment=shipment, input=request, after=audit.snapshot(shipment),
        )
    return shipment


def set_tracker(shipment: Shipment, tracker_id: str, *, actor_name: str, actor_user=None, note: str = "",
                info: dict | None = None) -> Shipment:
    """Bind a shipment to a real vessel / aircraft (MMSI, ICAO24 or plate). Audited.
    `info` (callsign, suggested route, ...) is kept in Shipment.request["tracking"]."""
    before = {"tracker_id": shipment.tracker_id, "tracking": (shipment.request or {}).get("tracking")}
    shipment.tracker_id = tracker_id.strip().lower() if shipment.mode == "AIR" else tracker_id.strip()
    shipment.request = {**(shipment.request or {}), "tracking": audit.json_safe(info or {})}
    shipment.save(update_fields=["tracker_id", "request", "updated_at"])
    audit.log(
        "shipment.tracker_changed", actor_type="user" if actor_user else "system", actor_name=actor_name,
        actor_user=actor_user, shipment=shipment, before=before,
        after={"tracker_id": shipment.tracker_id, "tracking": shipment.request["tracking"]}, detail=note,
    )
    return shipment


def update_endpoints(shipment: Shipment, *, origin: str, origin_latlon: list[float], destination: str,
                     dest_latlon: list[float], actor_name: str, actor_user=None, note: str = "") -> Shipment:
    """Correct where a shipment starts and ends (e.g. to the real flight's airports). Audited."""
    fields = ["origin", "origin_latlon", "destination", "dest_latlon"]
    before = {f: getattr(shipment, f) for f in fields}
    shipment.origin, shipment.destination = origin[:120], destination[:120]
    setattr(shipment, "origin_latlon", [float(origin_latlon[0]), float(origin_latlon[1])])  # JSONField
    setattr(shipment, "dest_latlon", [float(dest_latlon[0]), float(dest_latlon[1])])
    shipment.save(update_fields=fields + ["updated_at"])
    audit.log(
        "shipment.endpoints_changed", actor_type="user" if actor_user else "system", actor_name=actor_name,
        actor_user=actor_user, shipment=shipment, before=before, after={f: getattr(shipment, f) for f in fields},
        detail=note,
    )
    return shipment


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
def refresh_status(shipment: Shipment, *, actor_name: str = "system", run: AgentRun | None = None) -> Shipment:
    """Derive Shipment.status from its open alerts (block -> PROBLEM, warn -> ATTENTION).
    PLANNING and ARRIVED are never changed here."""
    if shipment.status in NOT_IN_TRANSIT:
        return shipment
    severities = set(shipment.alerts.filter(resolved=False).values_list("severity", flat=True))
    if Severity.BLOCK in severities:
        new = Shipment.Status.PROBLEM
    elif Severity.WARN in severities:
        new = Shipment.Status.ATTENTION
    else:
        new = Shipment.Status.ON_TRACK
    if new != shipment.status:
        before = audit.snapshot(shipment)
        shipment.status = new
        shipment.save(update_fields=["status", "updated_at"])
        audit.log(
            "shipment.status_changed", actor_type="system", actor_name=actor_name, run=run, shipment=shipment,
            before=before, after=audit.snapshot(shipment),
        )
    return shipment


def update_eta(shipment: Shipment, eta: datetime, *, source: str, actor_name: str) -> Shipment:
    """Set Shipment.eta_current from a provider estimate. Audited when it moves >= 30 min."""
    if shipment.status in NOT_IN_TRANSIT:
        return shipment
    old = shipment.eta_current
    if old == eta:
        return shipment
    shipment.eta_current = eta
    shipment.save(update_fields=["eta_current", "updated_at"])
    if old is None or abs((eta - old).total_seconds()) >= ETA_AUDIT_THRESHOLD_SECONDS:
        audit.log(
            "shipment.eta_updated", actor_type="system", actor_name=actor_name, shipment=shipment,
            before={"eta_current": old}, after={"eta_current": eta}, detail=f"source: {source}",
        )
    return shipment


def update_progress(shipment: Shipment, lat: float, lon: float) -> tuple[float, float]:
    """Project a point onto the shipment's path; store progress_pct + current_leg_index.
    Returns (remaining_km, total_km). Not audited (it changes with every position)."""
    path = shipment_path(shipment)
    km_along, total, _ = locate_on_path(path, [lat, lon])
    pct = Decimal("0") if total <= 0 else Decimal(str(round(min(max(km_along / total, 0.0), 1.0) * 100, 2)))
    leg = leg_index_at(shipment.route, km_along) if shipment.route_id and shipment.route else 0
    if pct != shipment.progress_pct or leg != shipment.current_leg_index:
        shipment.progress_pct, shipment.current_leg_index = pct, leg
        shipment.save(update_fields=["progress_pct", "current_leg_index", "updated_at"])
    return max(total - km_along, 0.0), total


# --------------------------------------------------------------------------
# positions
# --------------------------------------------------------------------------
def record_position(
    shipment: Shipment,
    lat: float,
    lon: float,
    ts: datetime,
    source: str,  # "aisstream" | "opensky" | "routes" | "replay" | "interpolated"
    speed: float | None = None,
    heading: float | None = None,
) -> Position:
    """Store one position (deduplicated on shipment + ts + source), then update
    progress_pct / current_leg_index along the approved route and, when the
    speed is known and the source isn't replay, eta_current.

    Not audited per position (collectors send thousands); a fresh live fix does
    audit when it clears AIS_SIGNAL_LOST, and big ETA moves are audited.
    """
    if source not in Position.Source.values:
        raise ValueError(f"unknown position source {source!r}")
    existing = Position.objects.filter(shipment=shipment, ts=ts, source=source).first()
    if existing:
        return existing
    pos = Position.objects.create(
        shipment=shipment, lat=lat, lon=lon, ts=ts, source=source, speed=speed, heading=heading
    )
    if shipment.status not in NOT_IN_TRANSIT:
        remaining_km, _ = update_progress(shipment, lat, lon)
        kmh = speed_kmh(speed, shipment.mode)
        # replay is a sped-up animation: it moves the map, not the ETA
        if kmh and kmh > 1 and source != Position.Source.REPLAY:
            update_eta(shipment, ts + timedelta(hours=remaining_km / kmh), source=source,
                       actor_name=f"collector.{source}")
    if source in LIVE_SOURCES:
        resolve_alerts(shipment, CLEARED_BY_POSITION, actor_name=f"collector.{source}",
                       reason="fresh position received")
    return pos


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------
def raise_alert(
    shipment: Shipment,
    code: str,
    severity: str,
    message: str,
    *,
    actor_name: str,
    run: AgentRun | None = None,
    actor_type: str | None = None,
) -> Alert:
    """Idempotent per open (shipment, code): returns the open alert if there is one."""
    if code not in Alert.Code.values:
        raise ValueError(f"unknown alert code {code!r}")
    if severity not in Severity.values:
        raise ValueError(f"unknown severity {severity!r}")
    with transaction.atomic():
        existing = shipment.alerts.filter(code=code, resolved=False).first()
        if existing:
            return existing
        alert = Alert.objects.create(shipment=shipment, code=code, severity=severity, message=message)
        audit.log(
            "alert.raised", actor_type=actor_type or _actor_type_for(actor_name), actor_name=actor_name,
            run=run, shipment=shipment, after=audit.snapshot(alert), detail=message,
        )
        refresh_status(shipment, actor_name=actor_name, run=run)
    return alert


def resolve_alerts(
    shipment: Shipment, codes, *, actor_name: str, actor_type: str = "system", actor_user=None,
    run: AgentRun | None = None, reason: str = "",
) -> int:
    """Resolve open alerts of the given codes on a shipment. Returns how many."""
    open_alerts = list(shipment.alerts.filter(code__in=list(codes), resolved=False))
    if not open_alerts:
        return 0
    with transaction.atomic():
        for alert in open_alerts:
            alert.resolved = True
            alert.save(update_fields=["resolved"])
            audit.log(
                "alert.resolved", actor_type=actor_type, actor_name=actor_name, actor_user=actor_user, run=run,
                shipment=shipment, after=audit.snapshot(alert), detail=reason,
            )
        refresh_status(shipment, actor_name=actor_name, run=run)
    return len(open_alerts)


# --------------------------------------------------------------------------
# corridors
# --------------------------------------------------------------------------
def shipments_using(corridor_code: str) -> list[Shipment]:
    """In-transit shipments whose approved route still has to pass the corridor
    (legs from current_leg_index on)."""
    ships = (
        Shipment.objects.exclude(status__in=NOT_IN_TRANSIT)
        .filter(route__isnull=False)
        .select_related("route")
    )
    return [s for s in ships if s.route and corridor_code in s.route.corridor_codes(s.current_leg_index)]


def set_corridor_status(
    code: str,
    status: str,
    reason: str,
    source: str,  # "search" | "gdelt" (agents) | "manual" (simulate button)
    *,
    risk_score=None,
    p_close_30d=None,
    expected_duration_days: int | None = None,
    war_risk_pct=None,
    sources: list | None = None,
    actor_name: str,
    run: AgentRun | None = None,
    actor_user=None,
) -> Corridor:
    """Update a corridor and alert the shipments still due to pass it:
    CLOSED -> CORRIDOR_CLOSED (block), RESTRICTED -> CORRIDOR_AT_RISK (warn),
    OPEN -> both resolved. Audited only when something actually changed."""
    if status not in Corridor.Status.values:
        raise ValueError(f"unknown corridor status {status!r}")
    if source not in Corridor.Source.values:
        raise ValueError(f"unknown status source {source!r}")
    actor_type = "user" if actor_user else ("agent" if source in ("search", "gdelt") else "system")

    with transaction.atomic():
        corridor = Corridor.objects.select_for_update().get(code=code)
        before = audit.snapshot(corridor)
        corridor.status, corridor.status_reason, corridor.status_source = status, reason, source
        for name, value in (("risk_score", risk_score), ("p_close_30d", p_close_30d), ("war_risk_pct", war_risk_pct)):
            if value is not None:
                setattr(corridor, name, Decimal(str(value)))
        if expected_duration_days is not None or status == Corridor.Status.OPEN:
            corridor.expected_duration_days = expected_duration_days
        if sources is not None:
            corridor.sources = audit.json_safe(sources)
        after = audit.snapshot(corridor)
        if after == before and sources is None:
            return corridor  # nothing new: no event, no alerts
        corridor.status_updated_at = timezone.now()
        corridor.save()
        audit.log(
            "corridor.status_changed", actor_type=actor_type, actor_name=actor_name, actor_user=actor_user,
            run=run, before=before, after=after, sources=sources, detail=f"{code}: {reason}",
        )

        for shipment in shipments_using(code):
            if status == Corridor.Status.OPEN:
                resolve_alerts(shipment, CORRIDOR_ALERTS, actor_name=actor_name, run=run, actor_type=actor_type,
                               actor_user=actor_user, reason=f"{corridor.name} reopened")
                continue
            closed = status == Corridor.Status.CLOSED
            code_now = Alert.Code.CORRIDOR_CLOSED if closed else Alert.Code.CORRIDOR_AT_RISK
            other = Alert.Code.CORRIDOR_AT_RISK if closed else Alert.Code.CORRIDOR_CLOSED
            resolve_alerts(shipment, [other], actor_name=actor_name, run=run, actor_type=actor_type,
                           actor_user=actor_user, reason=f"{corridor.name} is now {status.lower()}")
            extra = f" Expected for ~{expected_duration_days} days." if closed and expected_duration_days else ""
            raise_alert(shipment, code_now, Severity.BLOCK if closed else Severity.WARN,
                        f"{corridor.name} is {status.lower()} on this shipment's route: {reason}.{extra}",
                        actor_name=actor_name, run=run, actor_type=actor_type)
    return corridor


# --------------------------------------------------------------------------
# route options
# --------------------------------------------------------------------------
def _dec(value, default="0") -> Decimal:
    return Decimal(str(value if value not in (None, "") else default))


def _legs_geometry(legs: list[dict]) -> dict | None:
    """B's legs carry their own geometry ([[lon, lat], ...]): one LineString Feature per leg."""
    if not legs or not any(leg.get("geometry") for leg in legs):
        return None
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"leg": i, "mode": leg.get("mode", "")},
         "geometry": {"type": "LineString", "coordinates": [list(c) for c in leg.get("geometry") or []]}}
        for i, leg in enumerate(legs)
    ]}


def save_route_options(
    shipment: Shipment, options: list[RouteOptionDTO], *, run: AgentRun | None, actor_name: str
) -> list[RouteOption]:
    """Persist the solver's proposals for one run, in input order. Never chooses, never
    deletes (what a human was shown stays in the trail).

    * legs are stored as given (B's Leg: from_node, to_node, mode, km, days, corridors,
      geometry [[lon, lat]]) so replan can read them back; every corridor code must exist
    * route geometry: the DTO's `geometry` if any, else built from the legs' geometry
    * `contingency_key` ("R2") or `contingency` (list index) points at another option of the same list
    * a WAIT option without legs inherits the current route's legs/geometry
    * "CARRYING" in cost_breakdown is inventory cost: stored on the option, never booked as spend
    """
    known = set(Corridor.objects.values_list("code", flat=True))
    rows = [opt.model_dump(mode="python") if hasattr(opt, "model_dump") else dict(opt) for opt in options]
    for data in rows:
        unknown = {c for leg in data.get("legs") or [] for c in leg.get("corridors", [])} - known
        if unknown:
            raise ValueError(f"route option {data.get('label')!r}: unknown corridor(s) {sorted(unknown)}")
        if data.get("kind", "ROUTE") not in RouteOption.Kind.values:
            raise ValueError(f"unknown route option kind {data.get('kind')!r}")

    with transaction.atomic():
        current = shipment.route if shipment.route_id else None
        saved: list[RouteOption] = []
        for data in rows:
            legs = data.get("legs") or []
            geometry = data.get("geometry") or _legs_geometry(legs)
            if data.get("kind") == RouteOption.Kind.WAIT and not legs and current is not None:
                legs, geometry = current.legs, current.geometry
            saved.append(RouteOption.objects.create(
                shipment=shipment, run=run, kind=data.get("kind", RouteOption.Kind.ROUTE), key=data.get("key") or "",
                label=data["label"], legs=audit.json_safe(legs), geometry=normalize_geometry(geometry),
                transit_days=_dec(data.get("transit_days")),
                days_p10=_dec(data["days_p10"]) if data.get("days_p10") is not None else None,
                days_p90=_dec(data["days_p90"]) if data.get("days_p90") is not None else None,
                delay_days=_dec(data.get("delay_days")),
                cost_breakdown={k: str(_dec(v).quantize(Decimal("0.01"))) for k, v in (data.get("cost_breakdown") or {}).items()},
                landed_cost=_dec(data.get("landed_cost")), co2_kg=_dec(data.get("co2_kg")),
                risk_score=_dec(data.get("risk_score")), score=audit.json_safe(data.get("score") or {}),
                possible_disruptions=audit.json_safe(data.get("possible_disruptions") or []),
                break_even_days=_dec(data["break_even_days"]) if data.get("break_even_days") is not None else None,
                infeasible_reason=data.get("infeasible_reason") or "",
                rationale=data.get("rationale", ""), recommended=bool(data.get("recommended")),
                created_by=actor_name,
            ))
        by_key = {o.key: o for o in saved if o.key}
        for option, data in zip(saved, rows):
            target = None
            if data.get("contingency_key"):
                target = by_key.get(data["contingency_key"])
                if target is None:
                    raise ValueError(f"route option {option.label!r}: unknown contingency_key {data['contingency_key']!r}")
            elif data.get("contingency") is not None:
                idx = int(data["contingency"])
                if not (0 <= idx < len(saved)) or idx == saved.index(option):
                    raise ValueError(f"route option {option.label!r}: bad contingency index {idx}")
                target = saved[idx]
            if target is not None:
                option.contingency = target
                option.save(update_fields=["contingency"])
        audit.log(
            "route_options.saved", actor_type=_actor_type_for(actor_name, "agent"), actor_name=actor_name,
            run=run, shipment=shipment,
            output={"options": [audit.snapshot(o) for o in saved]},
            detail=f"{len(saved)} option(s)" + (f", recommended: {next((o.label for o in saved if o.recommended), '-')}"),
        )
    return saved


def update_shipment_request(shipment: Shipment, request: dict, *, actor_name: str, run: AgentRun | None = None) -> Shipment:
    """Replace Shipment.request (e.g. with the RoutingRequest the intent agent parsed). Audited.
    Keeps A's "tracking" info (followed flight) if the new request doesn't carry it."""
    before = dict(shipment.request or {})
    new = audit.json_safe(dict(request))
    if "tracking" in before and "tracking" not in new:
        new["tracking"] = before["tracking"]
    shipment.request = new
    shipment.save(update_fields=["request", "updated_at"])
    audit.log("shipment.request_updated", actor_type=_actor_type_for(actor_name, "agent"), actor_name=actor_name,
              run=run, shipment=shipment, before=before, after=new)
    return shipment
