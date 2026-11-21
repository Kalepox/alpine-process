from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.management.commands.seed_demo import Command as SeedCommand
from core.models import Corridor, Shipment, Vendor
from core.services import approvals, audit, expenses, shipments
from core.tests.helpers import RED_SEA_PATH, route_dto

@pytest.fixture(autouse=True)
def _media_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def roles(db):
    SeedCommand().seed_roles()
    User = get_user_model()
    return {u.username: u for u in User.objects.filter(username__in=["elena", "marco", "admin"])}


@pytest.fixture
def elena(roles):
    return roles["elena"]


@pytest.fixture
def marco(roles):
    return roles["marco"]


@pytest.fixture
def corridors(db):
    def line(*latlon):
        return {"type": "LineString", "coordinates": [[p[1], p[0]] for p in latlon]}

    return {
        c.code: c for c in [
            Corridor.objects.create(code="BAB_EL_MANDEB", name="Bab-el-Mandeb", mode="SEA",
                                    geometry=line([12.45, 43.25], [12.85, 43.6]), war_risk_pct=Decimal("0.15")),
            Corridor.objects.create(code="SUEZ", name="Suez Canal", mode="SEA", geometry=line([29.95, 32.55], [31.25, 32.33])),
            Corridor.objects.create(code="CAPE", name="Cape of Good Hope", mode="SEA", geometry=line([-34.4, 18.4], [-34.8, 20.0])),
        ]
    }


@pytest.fixture
def plan(marco):
    """plan(shipment, [dto, ...]) -> options saved under one AgentRun."""

    def _plan(shipment, dtos, pipeline="plan_routes"):
        run = audit.start_run(pipeline, trigger="user", triggered_by=marco, shipment=shipment, input={"test": True})
        options = shipments.save_route_options(shipment, dtos, run=run, actor_name="agent.solver")
        audit.finish_run(run, "OK", summary=f"{len(options)} options")
        return options

    return _plan


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="Northsea Marine Underwriters B.V.", vat_number="NL859102374B01",
                                 iban="NL91ABNA0417164300", country="NL", kind="insurer")


@pytest.fixture
def planning_shipment(elena, corridors):
    return shipments.create_planning_shipment(
        {"cargo": "Crude palm oil", "quantity": "8000", "unit": "mt", "cargo_value": "7640000",
         "origin": "Belawan, ID", "origin_latlon": [3.78, 98.69], "destination": "Mersin, TR",
         "dest_latlon": [36.78, 34.63], "ref": "MT Aurelia Grace", "goal": "Balanced"},
        user=elena,
    )


@pytest.fixture
def shipment(planning_shipment, plan, marco):
    """MT Aurelia Grace in transit on the approved Red Sea route, quoted FREIGHT + INSURANCE."""
    [red] = plan(planning_shipment, [route_dto(
        "Via Bab-el-Mandeb / Suez", RED_SEA_PATH, ["BAB_EL_MANDEB", "SUEZ"], days="23",
        breakdown={"FREIGHT": Decimal("312000"), "INSURANCE": Decimal("11460")}, recommended=True)])
    approvals.approve_route(red.pk, marco, note="booked")
    s = Shipment.objects.get(pk=planning_shipment.pk)
    shipments.record_position(s, 5.9, 95.3, timezone.now() - timedelta(minutes=5), "aisstream", speed=12.5)
    s.refresh_from_db()
    return s


@pytest.fixture
def make_doc(shipment, vendor):
    """A document already extracted and sitting in PROCESSING."""

    def _make(total="14800.00", iban=None, category="INSURANCE", number="NMU-1"):
        doc = expenses.receive_document(b"%PDF-1.4 test " + number.encode(), filename=f"{number}.pdf",
                                        actor_type="user", actor_name="elena", shipment=shipment)
        approvals.set_doc_status(doc, "PROCESSING", actor_type="agent", actor_name="agent.doc_pipeline")
        expenses.apply_extraction(
            doc,
            {"vendor": vendor, "doc_type": "insurance_invoice", "number": number, "category": category,
             "net": Decimal(total), "vat": Decimal("0"), "total": Decimal(total), "iban": iban or vendor.iban},
            actor_name="agent.doc_pipeline",
        )
        return doc

    return _make


@pytest.fixture
def ready_doc(make_doc):
    """A clean document: READY with a pending payment."""
    doc = make_doc()
    approvals.set_doc_status(doc, "READY", actor_type="agent", actor_name="agent.doc_pipeline")
    pa = approvals.request_payment(doc, requested_by="agent.doc_pipeline")
    return doc, pa

