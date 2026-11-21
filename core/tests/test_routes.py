"""v2 routing: planning, route options, approve_route (plan / replan / wait), corridor alerts, progress."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from autopilot.domain.schemas import RouteOptionDTO
from core.geo import feature_collection
from core.models import AuditEvent, CostLine, PreApproval, RouteOption, Shipment
from core.services import approvals, shipments
from core.services.errors import InvalidTransition
from core.tests.helpers import CAPE_PATH, RED_SEA_PATH, route_dto

pytestmark = pytest.mark.django_db


def events(action, **filters):
    return AuditEvent.objects.filter(action=action, **filters)


# ------------------------------------------------------------------ planning
def test_create_planning_shipment(planning_shipment, django_user_model):
    s = planning_shipment
    assert s.status == "PLANNING" and s.route is None and s.request["goal"] == "Balanced"
    assert s.cargo_value == Decimal("7640000") and s.origin_latlon == [3.78, 98.69]
    ev = events("shipment.planning_created").get()
    assert ev.actor_user.username == "elena" and ev.input["cargo"] == "Crude palm oil" and len(ev.input_sha256) == 64

    nobody = django_user_model.objects.create_user("nobody", password="x")
    with pytest.raises(PermissionDenied):
        shipments.create_planning_shipment({"cargo": "x", "origin": "a", "destination": "b"}, user=nobody)


def test_create_planning_shipment_validates(elena):
    with pytest.raises(ValueError):
        shipments.create_planning_shipment({"cargo": "Copper"}, user=elena)


# ------------------------------------------------------------- route options
def test_save_route_options_keeps_every_run(planning_shipment, plan):
    plan(planning_shipment, [route_dto("A", RED_SEA_PATH, ["SUEZ"])])
    plan(planning_shipment, [route_dto("B", RED_SEA_PATH, ["SUEZ"]), route_dto("C", CAPE_PATH, ["CAPE"])])
    assert planning_shipment.route_options.count() == 3  # never deleted: the trail keeps what was shown
    assert planning_shipment.route_options.values("run").distinct().count() == 2
    ev = events("route_options.saved").last()
    assert ev.run is not None and len(ev.output["options"]) == 2


def test_save_route_options_validates(planning_shipment, plan):
    with pytest.raises(ValueError, match="ATLANTIS"):
        plan(planning_shipment, [route_dto("x", RED_SEA_PATH, ["ATLANTIS"])])
    with pytest.raises(ValueError, match="contingency"):
        plan(planning_shipment, [route_dto("x", RED_SEA_PATH, ["SUEZ"], contingency=0)])


def test_contingency_and_geometry_normalized(planning_shipment, plan):
    red, cape = plan(planning_shipment, [
        route_dto("Suez", RED_SEA_PATH, ["SUEZ"], contingency=1, recommended=True,
                  possible_disruptions=[{"corridor": "BAB_EL_MANDEB", "p_close": 0.18, "delay_days": 12,
                                         "cost_impact": "9000", "contingency_label": "Cape"}]),
        route_dto("Cape", CAPE_PATH, ["CAPE"]),
    ])
    assert red.contingency == cape and red.recommended and not cape.recommended
    assert red.geometry["type"] == "FeatureCollection"
    assert red.geometry["features"][0]["geometry"]["coordinates"][0] == [98.69, 3.78]  # GeoJSON is [lon, lat]
    assert red.corridor_codes() == ["SUEZ"] and red.legs[0]["from"] == "A"


# ------------------------------------------------------------- approve_route
def test_approve_route_from_planning(planning_shipment, plan, elena, marco):
    red, cape = plan(planning_shipment, [
        route_dto("Suez", RED_SEA_PATH, ["BAB_EL_MANDEB", "SUEZ"], days="23",
                  breakdown={"FREIGHT": "312000", "WAR_RISK": "11460", "CUSTOMS_DUTY": "100", "IMPORT_VAT": "50"},
                  recommended=True),
        route_dto("Cape", CAPE_PATH, ["CAPE"], days="35"),
    ])
    with pytest.raises(PermissionDenied):
        approvals.approve_route(red.pk, elena)

    before = timezone.now()
    s = approvals.approve_route(red.pk, marco, note="balanced pick")
    red.refresh_from_db()
    assert s.status == "ON_TRACK" and s.route == red and red.chosen and s.departed_at
    assert s.eta_planned == s.eta_current and s.eta_current - before == pytest.approx(timedelta(days=23), abs=timedelta(minutes=1))
    lines = {c.category: c for c in CostLine.objects.filter(shipment=s)}
    assert lines["FREIGHT"].quoted == lines["FREIGHT"].projected == Decimal("312000.00")
    assert lines["CUSTOMS"].quoted == Decimal("150.00")  # CUSTOMS_DUTY + IMPORT_VAT -> CUSTOMS

    ev = events("route.approved").get()
    assert ev.actor_user == marco and ev.run == red.run and ev.route_option == red and ev.detail == "balanced pick"
    assert ev.input["option"]["transit_days"] == "23.0" and ev.input["option"]["id"] == red.pk  # what marco saw
    with pytest.raises(InvalidTransition):
        approvals.approve_route(red.pk, marco)


def test_replan_to_cape_clears_closure(shipment, plan, marco):
    shipments.set_corridor_status("BAB_EL_MANDEB", "CLOSED", "attacks on shipping", "manual",
                                  expected_duration_days=14, actor_name="marco", actor_user=marco)
    shipment.refresh_from_db()
    assert shipment.status == "PROBLEM"
    alert = shipment.alerts.get(code="CORRIDOR_CLOSED", resolved=False)
    assert "~14 days" in alert.message

    cape, wait = plan(shipment, [
        route_dto("Cape of Good Hope", CAPE_PATH, ["CAPE"], days="30", delay_days="11",
                  breakdown={"FREIGHT": "321000", "INSURANCE": "11460", "DEMURRAGE": "2500"}),
        route_dto("Wait 14 days", None, [], days="33", kind="WAIT", delay_days="14"),
    ], pipeline="replan")
    assert wait.legs and wait.geometry["features"]  # WAIT inherits the current route

    s = approvals.approve_route(cape.pk, marco)
    assert s.route == cape and s.status == "ON_TRACK" and s.current_leg_index == 0
    assert not s.alerts.filter(resolved=False).exists()
    freight = CostLine.objects.get(shipment=s, category="FREIGHT")
    assert freight.quoted == Decimal("312000.00") and freight.projected == Decimal("321000.00")  # quote kept
    assert RouteOption.objects.filter(shipment=s, chosen=True).get() == cape


def test_approving_wait_keeps_closure_alert(shipment, plan, marco):
    shipments.set_corridor_status("BAB_EL_MANDEB", "CLOSED", "attacks", "manual", actor_name="marco", actor_user=marco)
    [wait] = plan(shipment, [route_dto("Wait it out", None, [], days="30", kind="WAIT", delay_days="7")], "replan")
    s = approvals.approve_route(wait.pk, marco)
    assert s.route == wait and s.status == "PROBLEM"  # still on a closed corridor
    assert s.alerts.filter(code="CORRIDOR_CLOSED", resolved=False).exists()


# --------------------------------------------------------- corridors & alerts
def test_corridor_status_ladder(shipment, marco):
    shipments.set_corridor_status("SUEZ", "RESTRICTED", "war-risk premium up", "search", p_close_30d="0.2",
                                  sources=[{"title": "Lloyd's List", "url": "https://example.org/a"}],
                                  actor_name="agent.disruption")
    shipment.refresh_from_db()
    assert shipment.status == "ATTENTION" and shipment.alerts.get(resolved=False).code == "CORRIDOR_AT_RISK"
    ev = events("corridor.status_changed").get()
    assert ev.actor_type == "agent" and ev.sources[0]["url"] == "https://example.org/a"

    shipments.set_corridor_status("SUEZ", "CLOSED", "blocked", "search", actor_name="agent.disruption")
    shipment.refresh_from_db()
    assert shipment.status == "PROBLEM"
    assert [a.code for a in shipment.alerts.filter(resolved=False)] == ["CORRIDOR_CLOSED"]

    shipments.set_corridor_status("SUEZ", "OPEN", "reopened", "manual", actor_name="marco", actor_user=marco)
    shipment.refresh_from_db()
    assert shipment.status == "ON_TRACK" and not shipment.alerts.filter(resolved=False).exists()


def test_corridor_update_without_change_is_silent(shipment):
    shipments.set_corridor_status("CAPE", "OPEN", "", "search", actor_name="agent.disruption")
    assert not events("corridor.status_changed").exists()


def test_corridor_only_alerts_shipments_still_due_to_pass_it(planning_shipment, plan, marco, corridors):
    legs = [{"from": "Belawan", "to": "Port Said", "mode": "SEA", "corridors": ["SUEZ"]},
            {"from": "Port Said", "to": "Mersin", "mode": "SEA", "corridors": []}]
    dto = RouteOptionDTO(label="two legs", legs=legs, transit_days=Decimal("20"),
                         geometry=feature_collection([RED_SEA_PATH[:6], RED_SEA_PATH[5:]], legs))
    [option] = plan(planning_shipment, [dto])
    s = approvals.approve_route(option.pk, marco)
    Shipment.objects.filter(pk=s.pk).update(current_leg_index=1)  # already through Suez
    shipments.set_corridor_status("SUEZ", "CLOSED", "blocked", "manual", actor_name="marco", actor_user=marco)
    assert not s.alerts.exists()


# ------------------------------------------------------------ progress / ETA
def test_record_position_updates_progress_and_eta(shipment):
    now = timezone.now()
    shipments.record_position(shipment, 12.0, 51.0, now, "aisstream", speed=12.0)  # Gulf of Aden
    shipment.refresh_from_db()
    assert 30 < shipment.progress_pct < 60 and shipment.current_leg_index == 0
    eta = shipment.eta_current
    assert eta is not None and timedelta(days=5) < eta - now < timedelta(days=15)

    shipments.record_position(shipment, 12.5, 45.0, now + timedelta(minutes=1), "replay", speed=12.5)
    shipment.refresh_from_db()
    assert shipment.eta_current == eta  # replay moves the map, not the ETA
    assert shipment.progress_pct > 40


def test_signal_lost_cleared_only_by_live_fix(shipment):
    shipments.raise_alert(shipment, "AIS_SIGNAL_LOST", "warn", "no AIS for 7h", actor_name="collector.watchdog")
    shipments.record_position(shipment, 12, 50, timezone.now(), "interpolated", speed=12)
    assert shipment.alerts.filter(code="AIS_SIGNAL_LOST", resolved=False).exists()
    shipments.record_position(shipment, 12.1, 49.9, timezone.now(), "aisstream", speed=12)
    assert not shipment.alerts.filter(code="AIS_SIGNAL_LOST", resolved=False).exists()


def test_record_position_dedupes(shipment):
    ts = timezone.now()
    a = shipments.record_position(shipment, 12.0, 45.0, ts, "aisstream", speed=12.1)
    b = shipments.record_position(shipment, 12.0, 45.0, ts, "aisstream", speed=12.1)
    assert a.pk == b.pk


# -------------------------------------------------------------- pre-approvals
def test_preapproval_is_a_spending_cap(shipment, plan, elena, marco):
    [cape] = plan(shipment, [route_dto("Cape", CAPE_PATH, ["CAPE"], days="30")], "replan")
    pa = approvals.request_preapproval(shipment, cape, Decimal("12000"), ["WAR_RISK", "FREIGHT"],
                                       "Cape reroute extra cost", requested_by="agent.reroute", run=cape.run)
    assert approvals.request_preapproval(shipment, cape, Decimal("12000"), ["FREIGHT"], "again",
                                         requested_by="agent.reroute").pk == pa.pk
    with pytest.raises(PermissionDenied):
        approvals.decide_preapproval(pa.pk, elena, True)

    approvals.decide_preapproval(pa.pk, marco, True, note="ok up to 12k")
    shipment.refresh_from_db()
    assert PreApproval.objects.get(pk=pa.pk).status == "APPROVED"
    assert shipment.route != cape  # the route is approve_route's decision, not the cap's
    assert CostLine.objects.get(shipment=shipment, category="WAR_RISK").preapproval == pa
    ev = events("preapproval.approved").get()
    assert ev.preapproval == pa and ev.route_option == cape and ev.run == cape.run
    with pytest.raises(InvalidTransition):
        approvals.decide_preapproval(pa.pk, marco, True)
