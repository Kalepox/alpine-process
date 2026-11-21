"""Part 0 contract checks: models, roles, append-only audit, importable stubs."""
import inspect

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from core import models as m


def test_pending_action_permissions():
    codenames = {c for c, _ in m.PendingAction._meta.permissions}
    assert codenames == {"approve_spend", "approve_payment", "export_payments"}


def test_doc_status_choices_match_contract():
    assert set(m.ExpenseDocument.Status.values) == {
        "RECEIVED", "PROCESSING", "NEEDS_REVIEW", "READY", "APPROVED", "REJECTED", "EXPORTED", "FAILED",
    }


@pytest.mark.django_db
def test_seed_demo_roles():
    call_command("seed_demo")
    call_command("seed_demo")  # idempotent
    User = get_user_model()
    elena, marco = User.objects.get(username="elena"), User.objects.get(username="marco")
    assert elena.check_password("demo1234")
    assert not elena.has_perm("core.approve_payment")
    assert elena.has_perm("core.change_issue")
    for perm in ("approve_spend", "approve_payment", "export_payments"):
        assert marco.has_perm(f"core.{perm}")
    assert User.objects.get(username="admin").is_superuser


@pytest.mark.django_db
def test_audit_event_is_append_only():
    ev = m.AuditEvent.objects.create(actor_type="system", actor_name="test", action="test.created")
    ev.detail = "tampered"
    with pytest.raises(m.AppendOnlyError):
        ev.save()
    with pytest.raises(m.AppendOnlyError):
        ev.delete()
    with pytest.raises(m.AppendOnlyError):
        m.AuditEvent.objects.all().update(detail="x")
    with pytest.raises(m.AppendOnlyError):
        m.AuditEvent.objects.all().delete()


def test_contract_signatures_importable():
    from autopilot import pipelines
    from autopilot.domain.schemas import CostBreakdown, ExtractedExpense, IssueDraft, RouteOptionDTO  # noqa: F401
    from core.services import approvals, audit, expenses, export, shipments
    from integrations.base import PositionDTO, TrackingProvider  # noqa: F401

    expected = {
        audit: ["log", "snapshot"],
        shipments: ["record_position", "raise_alert", "set_corridor_status", "save_route_options"],
        approvals: [
            "request_preapproval", "decide_preapproval", "set_doc_status", "create_issues", "resolve_issue",
            "request_payment", "decide_payment", "request_report_send", "decide_report_send",
        ],
        expenses: ["apply_actual_cost"],
        export: ["build_export_batch"],
        pipelines: ["process_document", "scan_disruptions", "propose_reroutes", "draft_weekly_report", "ask"],
    }
    for module, names in expected.items():
        for name in names:
            assert inspect.isfunction(getattr(module, name)), f"{module.__name__}.{name}"


@pytest.mark.django_db
def test_home_and_login_pages(client):
    # C: the UI sends anonymous visitors to its login page
    home = client.get("/")
    assert home.status_code == 302 and "/login/" in home["Location"]
    assert client.get("/login/").status_code == 200
    assert client.get("/admin/login/").status_code == 200
