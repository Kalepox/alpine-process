"""
Write side of the UI. Every state change the UI makes goes through here, and
from here only through core.services.* (A) or autopilot.pipelines.* (B).

`user` is the dict from web.auth.current_user(request); user["obj"] is the
Django User the services check permissions on. Service errors become an
ActionError, which the views show as an error toast.
"""
from __future__ import annotations

import logging
import threading
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import close_old_connections

from core.models import AgentRun, CostCategory, ExpenseDocument, PendingAction, RouteOption, Shipment, WeeklyReport
from core.services import approvals, audit, expenses, export, shipments
from core.services.errors import InvalidTransition, NothingToExport, PayloadMismatch

log = logging.getLogger(__name__)
SERVICE_ERRORS = (PermissionDenied, InvalidTransition, PayloadMismatch, NothingToExport, ValueError)
AGENT_MISSING = "The autopilot agent for this isn't connected yet (Part B)."


class ActionError(Exception):
    """Shown to the user as an error toast."""


def _u(user):
    if not user or "obj" not in user:
        raise ActionError("Please sign in again.")
    return user["obj"]


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except PermissionDenied:
        raise ActionError("You don't have permission to do that (manager only).")
    except PayloadMismatch:
        raise ActionError("Refused: the document changed after the payment was requested. Reject it and re-check.")
    except (InvalidTransition, NothingToExport, ValueError) as exc:
        raise ActionError(str(exc))


def _pipeline(name):
    """Import an autopilot pipeline lazily; None until B implements it."""
    from autopilot import pipelines

    return getattr(pipelines, name)


def _in_thread(fn, *args):
    """Long agent calls run in a thread (no Celery); errors are logged, never raised."""
    def run():
        close_old_connections()
        try:
            fn(*args)
        except NotImplementedError:
            log.info("%s: agent not connected yet", getattr(fn, "__name__", fn))
        except Exception:
            log.exception("background %s failed", getattr(fn, "__name__", fn))
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- issues  -> approvals.resolve_issue
def resolve_issue(issue_id, user, resolution, note=""):
    issue = _call(approvals.resolve_issue, issue_id, _u(user), resolution, note)
    doc = ExpenseDocument.objects.get(pk=issue.document_id) if issue.document_id else None
    return {"id": doc.pk, "status": doc.status} if doc else {"id": None, "status": ""}


# ---------------------------------------------------------------- payments  -> approvals.decide_payment
def decide_payments(doc_ids, user, approve, note=""):
    u = _u(user)
    done, errors = [], []
    for did in doc_ids:
        pa = PendingAction.objects.filter(document_id=did, action_type="PAY", status="PENDING").first()
        if pa is None:
            continue
        try:
            _call(approvals.decide_payment, pa.pk, u, approve, note)
            done.append({"id": did})
        except ActionError as exc:
            errors.append(f"#{did}: {exc}")
    if errors and not done:
        raise ActionError("; ".join(errors))
    return done


# ---------------------------------------------------------------- routes  -> approvals.approve_route (+ budget cap)
def approve_route(option_id, user):
    """The manager approves a route. If it costs more than the current one, the extra
    is committed as an approved pre-approval (the cap expense documents are checked against)."""
    u = _u(user)
    option = RouteOption.objects.select_related("shipment__route").filter(pk=option_id).first()
    if option is None:
        raise ActionError("Route option not found.")
    current = option.shipment.route
    s = _call(approvals.approve_route, option_id, u)

    valid = {}
    if current is not None and current.pk != option.pk:
        for cat, value in (option.cost_breakdown or {}).items():
            delta = D(str(value)) - D(str((current.cost_breakdown or {}).get(cat, 0)))
            if delta > 0 and cat in CostCategory.values:
                valid[cat] = delta
    if valid:
        pa = _call(approvals.request_preapproval, s, option, sum(valid.values(), D(0)), list(valid),
                   f"Reroute: {option.label}", requested_by=u.get_username(), run=option.run)
        _call(approvals.decide_preapproval, pa.pk, u, True, "committed with the route approval")
    return {"id": s.pk, "ref": s.ref}


def decide_preapproval(pa_id, user, approve, note=""):
    p = _call(approvals.decide_preapproval, pa_id, _u(user), approve, note)
    return {"id": p.pk, "status": p.status}


# ---------------------------------------------------------------- disruptions -> A's service (manual) / B's pipelines
def simulate_disruption(code, user, status="CLOSED", duration_days=14):
    """Works without B: the corridor goes CLOSED/RESTRICTED and affected shipments get alerts."""
    u = _u(user)
    hit = [s.ref for s in shipments.shipments_using(code)]
    reason = ("Simulated: closed to commercial traffic after a security incident."
              if status == "CLOSED" else "Simulated restriction.")
    _call(shipments.set_corridor_status, code, status, reason, "manual",
          expected_duration_days=duration_days if status == "CLOSED" else None,
          actor_name=u.get_username(), actor_user=u)
    return hit


def reopen_corridor(code, user):
    u = _u(user)
    _call(shipments.set_corridor_status, code, "OPEN", "Reopened (manual).", "manual",
          actor_name=u.get_username(), actor_user=u)


def scan_disruptions(user):
    try:
        return _pipeline("scan_disruptions")(_u(user).pk)
    except NotImplementedError:
        raise ActionError(AGENT_MISSING)


def propose_reroutes(shipment_id, user):
    try:
        return _pipeline("replan")(shipment_id, _u(user).pk)
    except NotImplementedError:
        raise ActionError(AGENT_MISSING + " Route alternatives appear here once the reroute agent runs.")


# ---------------------------------------------------------------- documents -> expenses.receive_document + pipelines
def upload_documents(files, user):
    u = _u(user)
    created = []
    for f in files:
        doc = _call(expenses.receive_document, f, filename=f.name, actor_type="user",
                    actor_name=u.get_username(), actor_user=u)
        created.append(doc.pk)
        _in_thread(_pipeline("process_document"), doc.pk)
    return created


def process_all(user):
    _u(user)
    ids = list(ExpenseDocument.objects.filter(status__in=["RECEIVED", "FAILED"]).values_list("pk", flat=True))
    for did in ids:
        _in_thread(_pipeline("process_document"), did)
    return len(ids)


# ---------------------------------------------------------------- export  -> export.build_export_batch
def build_export_batch(user):
    b = _call(export.build_export_batch, _u(user))
    return {"id": b.pk, "count": b.documents.count(), "total": b.total}


# ---------------------------------------------------------------- reports -> pipelines.draft_weekly_report / decide_report_send
def draft_weekly_report(user):
    from datetime import timedelta

    from django.utils import timezone

    today = timezone.localdate()
    start = today - timedelta(days=today.weekday() + 7)
    try:
        rid = _pipeline("draft_weekly_report")(start, start + timedelta(days=6))
    except NotImplementedError:
        raise ActionError(AGENT_MISSING)
    return {"id": rid}


def decide_report_send(rid, user, approve):
    u = _u(user)
    report = WeeklyReport.objects.filter(pk=rid).first()
    if report is None:
        raise ActionError("Report not found.")
    pa = PendingAction.objects.filter(report=report, action_type="SEND_REPORT", status="PENDING").first()
    if pa is None:  # a draft nobody asked to send yet: the manager asks and decides
        pa = _call(approvals.request_report_send, report, requested_by=u.get_username())
    r = _call(approvals.decide_report_send, pa.pk, u, approve)
    return {"id": r.pk, "status": r.status}


# ---------------------------------------------------------------- assistant -> pipelines.ask
def ask(task, user):
    try:
        return _pipeline("ask")(task, _u(user).pk)
    except NotImplementedError:
        return "The Monora agent (Google ADK) isn't connected yet. Once Part B is merged, it answers here."


def reset_demo():
    call_command("demo_reset", "--noinput")


# ---------------------------------------------------------------- route planning -> create_planning_shipment + options
def add_planned_shipment(user, name, cargo, plan):
    """Save the route computed by web/routing.py: a PLANNING shipment with one route option
    (one AgentRun), approved right away when the user is a manager."""
    u = _u(user)
    lat, lon = plan["start"]
    request = {
        "ref": name or f"Vessel to {plan['destination']}"[:80], "mode": "SEA", "cargo": cargo or "General cargo",
        "origin": f"At sea ({lat:.2f}, {lon:.2f})", "origin_latlon": [lat, lon],
        "destination": plan["destination"], "dest_latlon": plan["dest_latlon"], "goal": "Fastest",
        "via_port": plan["port"],
    }
    s = _call(shipments.create_planning_shipment, request, user=u)
    legs = [{"from_node": request["origin"] if i == 0 else plan["port"],
             "to_node": plan["port"] if i == 0 else plan["destination"],
             "mode": "ROAD" if leg["mode"] != "SEA" else "SEA", "km": leg["km"], "days": leg["days"], "corridors": [],
             "geometry": [[pt[1], pt[0]] for pt in leg["coords"]]}  # B's Leg: GeoJSON [lon, lat]
            for i, leg in enumerate(plan["legs"])]
    from autopilot.domain.schemas import RouteOptionDTO

    run = audit.start_run(AgentRun.Pipeline.PLAN_ROUTES, trigger="user", triggered_by=u, shipment=s,
                          input={"start": plan["start"], "destination": plan["destination"]},
                          model="searoute (no LLM)", prompt_version="ui-planner")
    days = float(plan["total_days"])
    [option] = _call(shipments.save_route_options, s, [RouteOptionDTO.model_validate(dict(
        key="P1", label=f"Via {plan['port']}", legs=legs, transit_days=days, days_p10=days, days_p90=days,
        landed_cost=D("0"), co2_kg=0.0, risk_score=0.0,
        rationale="Computed by the UI planner (sea lanes + road).", recommended=True,
    ))], run=run, actor_name="web.routing")
    audit.finish_run(run, AgentRun.Status.OK, summary=f"1 option via {plan['port']}")
    if user.get("is_manager"):
        _call(approvals.approve_route, option.pk, u, "planned from the map")
    return {"id": s.pk, "ref": Shipment.objects.get(pk=s.pk).ref}


# ---------------------------------------------------------------- real flights -> integrations.flights (OpenSky)
def find_flights(shipment_id, user, callsign_prefix=""):
    from integrations.flights import FlightError, bbox_for
    from integrations.flights import find_flights as _find

    _u(user)
    s = Shipment.objects.filter(pk=shipment_id).first()
    try:
        return _find(callsign_prefix, bbox_for(s))
    except FlightError as exc:
        raise ActionError(str(exc))


def track_flight(shipment_id, user, flight):
    from integrations.flights import FlightError, bind

    u = _u(user)
    s = Shipment.objects.filter(pk=shipment_id).first()
    if s is None:
        raise ActionError("Shipment not found.")
    try:
        f = bind(s, flight, actor_name=u.get_username(), actor_user=u)
    except FlightError as exc:
        raise ActionError(str(exc))
    return {"id": s.pk, "ref": s.ref, "callsign": f["callsign"] or f["icao24"], "icao24": f["icao24"],
            "suggestion": f["suggestion"]}


def align_flight_route(shipment_id, user, codes_text):
    """Route = the flight's airports (start, stopovers, end). Manager only (it's a route approval)."""
    from integrations.airports import parse_codes
    from integrations.flights import FlightError, align_route

    u = _u(user)
    s = Shipment.objects.filter(pk=shipment_id).first()
    if s is None:
        raise ActionError("Shipment not found.")
    codes = parse_codes(codes_text)
    try:
        s = _call(align_route, s, codes, user=u, note="confirmed in the UI")
    except FlightError as exc:
        raise ActionError(str(exc))
    return {"id": s.pk, "ref": s.ref, "route": " → ".join(codes)}
