"""Tools for monora_assistant (PLAN_B §B2.2 #5). Read-only, except propose_replan, which only proposes.

ADK runs tools inside its event loop, and Django's ORM refuses to run there, so each
tool is an async wrapper around a sync implementation (sync_to_async). Results are
plain JSON; numbers come straight from the database or the solver.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from asgiref.sync import sync_to_async

from autopilot.domain.tables import CORRIDORS


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def option_summary(o) -> dict:
    """Same fields for a RouteOptionDTO or a saved RouteOption."""
    return {
        "key": getattr(o, "key", None), "id": getattr(o, "id", None), "kind": o.kind, "label": o.label,
        "days_expected": o.transit_days, "days_p10": o.days_p10, "days_p90": o.days_p90,
        "landed_cost_eur": _plain(o.landed_cost), "co2_t": round(o.co2_kg / 1000, 1),
        "closure_risk_pct": round(o.risk_score * 100), "score": (o.score or {}).get("total"),
        "recommended": o.recommended, "break_even_days": o.break_even_days,
    }


# ---------------------------------------------------------------- sync implementations
def _what_if(origin, destination, cargo, cargo_class, weight_t, value_eur, deadline, preset) -> dict:
    from django.utils import timezone

    from core.models import Corridor

    from autopilot.domain.network import UnknownPlace
    from autopilot.domain.risk import RiskOverlay
    from autopilot.domain.schemas import CargoSpec, RoutingRequest
    from autopilot.domain.solver import NoRouteFound, RouteSolver

    try:
        req = RoutingRequest(
            origin=origin, destination=destination, preset=preset or "BEST", deadline=deadline or None,
            cargo=CargoSpec(description=cargo, cargo_class=cargo_class or "CONTAINER_GENERAL",
                            weight_t=weight_t, value_eur=Decimal(str(value_eur))))
        result = RouteSolver().plan(req, RiskOverlay.from_rows(Corridor.objects.all()), today=timezone.localdate())
    except (UnknownPlace, NoRouteFound, ValueError) as e:
        return {"error": str(e)}
    return {"saved": False, "options": [option_summary(o) for o in result.options],
            "note": "What-if only: nothing was saved. Use the Plan shipment screen to create real options."}


def _risk_overlay() -> dict:
    from core.models import Corridor

    rows = []
    for c in Corridor.objects.all().order_by("code"):
        rows.append({
            "code": c.code, "name": CORRIDORS.get(c.code, (c.name,))[0], "status": c.status,
            "p_close_30d": _plain(getattr(c, "p_close_30d", None)),
            "expected_duration_days": getattr(c, "expected_duration_days", None),
            "reason": c.status_reason, "sources": getattr(c, "sources", []),
            "updated": _plain(c.status_updated_at),
        })
    return {"corridors": rows}


def _shipment(ref) -> dict:
    from django.db.models import Q

    from core.models import Shipment

    q = Q(ref__icontains=str(ref)) | (Q(pk=int(ref)) if str(ref).isdigit() else Q())
    s = Shipment.objects.filter(q).first()
    if s is None:
        return {"error": f"no shipment matching {ref!r}"}
    route = getattr(s, "route", None)
    return {
        "id": s.pk, "ref": s.ref, "status": s.status, "cargo": s.cargo, "origin": s.origin, "destination": s.destination,
        "progress_pct": _plain(getattr(s, "progress_pct", None)),
        "eta_planned": _plain(s.eta_planned), "eta_current": _plain(s.eta_current),
        "route": option_summary(route) if route else None,
        "open_alerts": [{"code": a.code, "severity": a.severity, "message": a.message}
                        for a in s.alerts.filter(resolved=False)],
    }


def _explain(option_id) -> dict:
    from core.models import AuditEvent, RouteOption

    o = RouteOption.objects.filter(pk=option_id).first()
    if o is None:
        return {"error": f"no route option {option_id}"}
    steps = AuditEvent.objects.filter(run=o.run).order_by("seq") if getattr(o, "run", None) else []
    return {
        "option": {**option_summary(o), "rationale": o.rationale,
                   "possible_disruptions": getattr(o, "possible_disruptions", [])},
        "why": [{"step": e.action, "by": e.actor_name, "detail": e.detail} for e in steps],
    }


def _propose_replan(shipment_id) -> dict:
    from core.models import RouteOption

    from autopilot.pipelines.replan import replan

    ids = replan(int(shipment_id), trigger={"kind": "ASK", "severity": "info"})
    return {"proposed": [option_summary(o) for o in RouteOption.objects.filter(pk__in=ids)],
            "note": "Proposals only: a manager must approve one in the Replan screen."}


# ---------------------------------------------------------------- tools (async, for ADK)
async def what_if_routes(origin: str, destination: str, cargo: str, weight_t: float, value_eur: float,
                         cargo_class: str = "CONTAINER_GENERAL", deadline: str = "", preset: str = "BEST") -> dict:
    """Computes 3-5 route options for a hypothetical shipment with today's corridor risks. Saves nothing.

    Args:
        origin: city or port, e.g. "Antofagasta".
        destination: city or port, e.g. "Milan".
        cargo: what is shipped, e.g. "copper cathodes".
        weight_t: weight in tonnes.
        value_eur: cargo value in EUR.
        cargo_class: DRY_BULK, LIQUID_BULK, CONTAINER_GENERAL, DANGEROUS_GOODS, PERISHABLE_REEFER, HIGH_VALUE or OVERSIZE.
        deadline: ISO date (YYYY-MM-DD) or empty.
        preset: BEST, FASTEST, MOST_RELIABLE, CHEAPEST or GREENEST.
    """
    return await sync_to_async(_what_if)(origin, destination, cargo, cargo_class, weight_t, value_eur, deadline, preset)


async def get_risk_overlay() -> dict:
    """Current status of every tracked corridor (open/restricted/closed, closure probability, reason, sources)."""
    return await sync_to_async(_risk_overlay)()


async def get_shipment(ref: str) -> dict:
    """A shipment's status, progress, ETAs, approved route and open alerts. ref: id, vessel name or reference."""
    return await sync_to_async(_shipment)(ref)


async def explain_option(option_id: int) -> dict:
    """A route option's numbers and rationale, plus the audit steps that produced it (the "Why?" chain)."""
    return await sync_to_async(_explain)(option_id)


async def propose_replan(shipment_id: int) -> dict:
    """Computes new route options (reroutes and WAIT) for an in-transit shipment. Only proposes: a manager approves."""
    return await sync_to_async(_propose_replan)(shipment_id)


ASSISTANT_TOOLS = [what_if_routes, get_risk_overlay, get_shipment, explain_option, propose_replan]
