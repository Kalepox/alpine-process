"""A.2 services: transitions, permissions, approval gates, audit."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command

from autopilot.domain.schemas import IssueDraft, RouteOptionDTO
from core.models import (
    Alert, AuditEvent, CostLine, ExpenseDocument, Issue, PendingAction, PreApproval, Shipment, WeeklyReport,
)
from core.services import approvals, audit, expenses, shipments
from core.services.errors import InvalidTransition, PayloadMismatch

pytestmark = pytest.mark.django_db


def actions(**filters):
    return list(AuditEvent.objects.filter(**filters).order_by("id").values_list("action", flat=True))


# ------------------------------------------------------------------- audit
def test_audit_log_links_document_shipment_and_hash(make_doc):
    doc = make_doc()
    ev = audit.log("test.event", actor_type="agent", actor_name="agent.x", document=doc,
                   before={"total": Decimal("1.50")})
    assert ev.shipment_id == doc.shipment_id
    assert ev.input_sha256 == doc.file_sha256 and len(ev.input_sha256) == 64
    assert ev.before == {"total": "1.50"}


def test_audit_rejects_unknown_actor_type():
    with pytest.raises(ValueError):
        audit.log("x", actor_type="robot", actor_name="r2d2")


def test_every_step_is_audited(make_doc):
    doc = make_doc()
    assert actions(document=doc) == ["document.received", "document.status_changed", "document.fields_updated"]


# ------------------------------------------------------------- state machine
def test_illegal_transition_raises(make_doc):
    doc = make_doc()
    with pytest.raises(InvalidTransition):
        approvals.set_doc_status(doc, "RECEIVED", actor_type="agent", actor_name="agent.x")


@pytest.mark.parametrize("target", ["APPROVED", "EXPORTED", "REJECTED"])
def test_set_doc_status_refuses_human_only_targets(make_doc, target):
    doc = make_doc()
    approvals.set_doc_status(doc, "READY", actor_type="agent", actor_name="agent.x")
    with pytest.raises(InvalidTransition):
        approvals.set_doc_status(doc, target, actor_type="agent", actor_name="agent.x")


def test_ready_blocked_by_open_block_issue(make_doc):
    doc = make_doc()
    approvals.create_issues(doc, [IssueDraft(code="OVER_PREAPPROVAL", message="over cap")], actor_name="agent.x")
    with pytest.raises(InvalidTransition):
        approvals.set_doc_status(doc, "READY", actor_type="agent", actor_name="agent.x")


def test_fields_frozen_after_ready(ready_doc):
    doc, _ = ready_doc
    with pytest.raises(InvalidTransition):
        expenses.apply_extraction(doc, {"iban": "NL02ABNA0123456789"}, actor_name="agent.x")


def test_apply_extraction_refuses_status(make_doc):
    with pytest.raises(ValueError):
        expenses.apply_extraction(make_doc(), {"status": "APPROVED"}, actor_name="agent.x")


# ------------------------------------------------------------------- issues
def test_create_issues_is_idempotent(make_doc):
    doc = make_doc()
    drafts = [IssueDraft(code="IBAN_CHANGED", message="IBAN differs", facts={"iban": "X"})]
    first = approvals.create_issues(doc, drafts, actor_name="agent.x")
    again = approvals.create_issues(doc, drafts, actor_name="agent.x")
    assert [i.pk for i in first] == [i.pk for i in again]
    assert doc.issues.count() == 1


def test_create_issues_rejects_unknown_code(make_doc):
    with pytest.raises(ValueError):
        approvals.create_issues(make_doc(), [{"code": "MADE_UP", "message": "?"}], actor_name="agent.x")


def test_resolve_last_block_issue_moves_to_ready_and_requests_payment(make_doc, elena):
    doc = make_doc()
    [issue] = approvals.create_issues(doc, [IssueDraft(code="OVER_PREAPPROVAL", message="over")], actor_name="agent.x")
    approvals.set_doc_status(doc, "NEEDS_REVIEW", actor_type="agent", actor_name="agent.x")

    with pytest.raises(ValueError):  # override needs a note
        approvals.resolve_issue(issue.pk, elena, "overridden")
    approvals.resolve_issue(issue.pk, elena, "overridden", note="Cap raised by phone with Marco")

    doc.refresh_from_db()
    assert doc.status == "READY"
    pa = doc.pending_actions.get()
    assert pa.status == "PENDING" and pa.requested_by == "elena"
    with pytest.raises(InvalidTransition):
        approvals.resolve_issue(issue.pk, elena, "accepted")


def test_reject_via_issue_rejects_document(make_doc, elena):
    doc = make_doc()
    issues = approvals.create_issues(
        doc, [IssueDraft(code="IBAN_CHANGED", message="fraud?"), IssueDraft(code="VAT_MISMATCH", message="vat", severity="warn")],
        actor_name="agent.x",
    )
    approvals.set_doc_status(doc, "NEEDS_REVIEW", actor_type="agent", actor_name="agent.x")
    approvals.resolve_issue(issues[0].pk, elena, "rejected", note="Vendor confirmed they did not change bank")
    doc.refresh_from_db()
    assert doc.status == "REJECTED"
    assert not doc.issues.filter(resolved=False).exists()


def test_resolve_issue_needs_permission(make_doc, django_user_model):
    doc = make_doc()
    [issue] = approvals.create_issues(doc, [IssueDraft(code="DUPLICATE", message="dup")], actor_name="agent.x")
    nobody = django_user_model.objects.create_user("nobody", password="x")
    with pytest.raises(PermissionDenied):
        approvals.resolve_issue(issue.pk, nobody, "accepted")


# ----------------------------------------------------------------- payments
def test_request_payment_needs_ready_and_is_idempotent(make_doc):
    doc = make_doc()
    with pytest.raises(InvalidTransition):
        approvals.request_payment(doc, requested_by="agent.x")
    approvals.set_doc_status(doc, "READY", actor_type="agent", actor_name="agent.x")
    a = approvals.request_payment(doc, requested_by="agent.x")
    b = approvals.request_payment(doc, requested_by="agent.x")
    assert a.pk == b.pk
    assert a.payload["amount"] == "14800.00" and a.payload["iban"] == "NL91ABNA0417164300"


def test_ops_cannot_approve_payment(ready_doc, elena):
    _, pa = ready_doc
    with pytest.raises(PermissionDenied):
        approvals.decide_payment(pa.pk, elena, True)


def test_manager_approves_payment_and_actual_cost_is_booked(ready_doc, marco):
    doc, pa = ready_doc
    approvals.decide_payment(pa.pk, marco, True, note="ok")
    doc.refresh_from_db()
    pa.refresh_from_db()
    assert doc.status == "APPROVED" and pa.status == "APPROVED" and pa.decided_by == marco
    line = CostLine.objects.get(shipment=doc.shipment, category="INSURANCE")
    assert line.actual == Decimal("14800.00")
    assert "payment.approved" in actions(document=doc)
    with pytest.raises(InvalidTransition):
        approvals.decide_payment(pa.pk, marco, True)


def test_payment_refused_when_iban_changed_after_request(ready_doc, marco):
    doc, pa = ready_doc
    ExpenseDocument.objects.filter(pk=doc.pk).update(iban="TR180006200119000006672315")  # tampering
    with pytest.raises(PayloadMismatch):
        approvals.decide_payment(pa.pk, marco, True)
    doc.refresh_from_db()
    pa.refresh_from_db()
    assert doc.status == "READY" and pa.status == "PENDING"
    ev = AuditEvent.objects.get(action="payment.approval_refused")
    assert ev.actor_user == marco and "iban" in ev.detail


def test_payment_refused_when_amount_changed_after_request(ready_doc, marco):
    doc, pa = ready_doc
    ExpenseDocument.objects.filter(pk=doc.pk).update(total=Decimal("18800.00"))
    with pytest.raises(PayloadMismatch):
        approvals.decide_payment(pa.pk, marco, True)


def test_reject_payment_rejects_document(ready_doc, marco):
    doc, pa = ready_doc
    approvals.decide_payment(pa.pk, marco, False, note="not ours")
    doc.refresh_from_db()
    assert doc.status == "REJECTED"


# ------------------------------------------------------------ pre-approvals
def _options():
    return [
        RouteOptionDTO(label="Via Cape of Good Hope", corridor_code="CAPE", mode="SEA", transit_days=38,
                       delay_days=12, cost_breakdown={"FREIGHT": Decimal("9000"), "DEMURRAGE": Decimal("2500")},
                       total_extra_cost=Decimal("11500"), rationale="avoids the closure"),
        RouteOptionDTO(label="Stay, war-risk cover", corridor_code="RED_SEA_SUEZ", mode="SEA", transit_days=23,
                       delay_days=0, cost_breakdown={"WAR_RISK": Decimal("11460")}, total_extra_cost=Decimal("11460")),
    ]


def test_save_route_options_replaces_unchosen(shipment):
    shipments.save_route_options(shipment, _options(), actor_name="agent.reroute")
    shipments.save_route_options(shipment, _options()[:1], actor_name="agent.reroute")
    assert shipment.route_options.count() == 1


def test_save_route_options_rejects_unknown_corridor(shipment):
    bad = RouteOptionDTO(label="x", corridor_code="ATLANTIS", mode="SEA", transit_days=1, delay_days=0,
                         total_extra_cost=Decimal("1"))
    with pytest.raises(ValueError):
        shipments.save_route_options(shipment, [bad], actor_name="agent.reroute")


def test_preapproval_flow_reroutes_shipment(shipment, corridors, elena, marco):
    shipments.set_corridor_status("RED_SEA_SUEZ", "CLOSED", "attacks on shipping", "manual", actor_name="elena", actor_user=elena)
    shipment.refresh_from_db()
    assert shipment.status == "PROBLEM"

    cape, _ = shipments.save_route_options(shipment, _options(), actor_name="agent.reroute")
    pa = approvals.request_preapproval(shipment, cape, Decimal("12000"), ["FREIGHT", "DEMURRAGE"],
                                       "Red Sea closed", requested_by="elena")
    assert approvals.request_preapproval(shipment, cape, Decimal("12000"), ["FREIGHT"], "again",
                                         requested_by="elena").pk == pa.pk
    with pytest.raises(PermissionDenied):
        approvals.decide_preapproval(pa.pk, elena, True)

    approvals.decide_preapproval(pa.pk, marco, True, note="go Cape")
    shipment.refresh_from_db()
    cape.refresh_from_db()
    assert cape.chosen
    assert shipment.corridor == corridors["CAPE"]
    assert shipment.eta_current == shipment.eta_planned + timedelta(days=12)
    assert shipment.status == "ON_TRACK"
    freight = CostLine.objects.get(shipment=shipment, category="FREIGHT")
    assert freight.projected == Decimal("321000.00") and freight.preapproval == pa
    assert CostLine.objects.get(shipment=shipment, category="DEMURRAGE").projected == Decimal("2500.00")
    with pytest.raises(InvalidTransition):
        approvals.decide_preapproval(pa.pk, marco, True)


def test_rejected_preapproval_changes_nothing(shipment, corridors, marco):
    cape, _ = shipments.save_route_options(shipment, _options(), actor_name="agent.reroute")
    pa = approvals.request_preapproval(shipment, cape, Decimal("12000"), ["FREIGHT"], "x", requested_by="agent.reroute")
    approvals.decide_preapproval(pa.pk, marco, False, note="too expensive")
    shipment.refresh_from_db()
    assert PreApproval.objects.get(pk=pa.pk).status == "REJECTED"
    assert shipment.corridor == corridors["RED_SEA_SUEZ"]


# ------------------------------------------------- corridors, alerts, positions
def test_corridor_closed_raises_alerts_and_reopen_clears(shipment):
    shipments.set_corridor_status("RED_SEA_SUEZ", "CLOSED", "attacks", "search", actor_name="agent.disruption")
    shipments.set_corridor_status("RED_SEA_SUEZ", "CLOSED", "still", "search", actor_name="agent.disruption")
    assert shipment.alerts.filter(code="CORRIDOR_CLOSED", resolved=False).count() == 1
    shipments.set_corridor_status("RED_SEA_SUEZ", "OPEN", "reopened", "search", actor_name="agent.disruption")
    shipment.refresh_from_db()
    assert shipment.status == "ON_TRACK"
    assert not shipment.alerts.filter(resolved=False).exists()


def test_restricted_corridor_means_attention(shipment):
    shipments.set_corridor_status("RED_SEA_SUEZ", "RESTRICTED", "war-risk premium up", "manual", actor_name="system")
    shipment.refresh_from_db()
    assert shipment.status == "ATTENTION"


def test_record_position_dedupes_and_clears_signal_lost(shipment):
    shipments.raise_alert(shipment, "AIS_SIGNAL_LOST", "warn", "no AIS for 7h", actor_name="collector.watchdog")
    ts = shipment.eta_planned - timedelta(days=5)
    a = shipments.record_position(shipment, 12.0, 45.0, ts, "aisstream", speed=12.1)
    b = shipments.record_position(shipment, 12.0, 45.0, ts, "aisstream", speed=12.1)
    assert a.pk == b.pk
    assert not shipment.alerts.filter(code="AIS_SIGNAL_LOST", resolved=False).exists()


# ---------------------------------------------------------------- reports
def test_request_report_send_is_idempotent():
    from datetime import date

    report = WeeklyReport.objects.create(period_start=date(2026, 11, 16), period_end=date(2026, 11, 22))
    a = approvals.request_report_send(report, requested_by="agent.report")
    b = approvals.request_report_send(report, requested_by="agent.report")
    assert a.pk == b.pk and a.action_type == PendingAction.ActionType.SEND_REPORT


# ------------------------------------------------------------------- seed
def test_seed_demo_is_idempotent_and_books_history():
    call_command("seed_demo")
    counts = (Shipment.objects.count(), ExpenseDocument.objects.count(), CostLine.objects.count())
    call_command("seed_demo")
    assert counts == (Shipment.objects.count(), ExpenseDocument.objects.count(), CostLine.objects.count())
    assert counts[0] == 6 and counts[1] == 13
    assert set(ExpenseDocument.objects.values_list("status", flat=True)) == {"APPROVED"}
    assert Shipment.objects.get(ref="MV Karoo Spirit").status == "ATTENTION"
    freight = CostLine.objects.get(shipment__ref="MV Sundara Bay", category="FREIGHT")
    assert freight.actual == Decimal("247000.00")
    assert all(s.positions.exists() for s in Shipment.objects.all())


# ------------------------------------------------------------------- admin
def test_audit_admin_is_read_only(client, roles):
    client.force_login(roles["admin"])
    assert client.get("/admin/core/auditevent/").status_code == 200
    assert client.get("/admin/core/auditevent/add/").status_code == 403


@pytest.mark.parametrize("model", ["shipment", "expensedocument", "pendingaction", "preapproval", "vendor", "issue"])
def test_admin_changelists_load(client, roles, model):
    call_command("seed_demo")
    client.force_login(roles["admin"])
    assert client.get(f"/admin/core/{model}/").status_code == 200


def test_admin_shipment_change_page_loads(client, roles):
    call_command("seed_demo")
    client.force_login(roles["admin"])
    s = Shipment.objects.get(ref="MV Atacama Star")
    assert client.get(f"/admin/core/shipment/{s.pk}/change/").status_code == 200
