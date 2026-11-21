from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.management.commands.seed_demo import Command as SeedCommand
from core.models import Corridor, CostLine, Shipment, Vendor
from core.services import approvals, expenses


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
    red = Corridor.objects.create(code="RED_SEA_SUEZ", name="Red Sea / Suez", mode="SEA",
                                  waypoints=[[12.6, 43.4], [30.0, 32.55]], war_risk_pct=Decimal("0.15"))
    cape = Corridor.objects.create(code="CAPE", name="Cape of Good Hope", mode="SEA",
                                   waypoints=[[-34.8, 20.0], [36.0, -6.0]])
    return {"RED_SEA_SUEZ": red, "CAPE": cape}


@pytest.fixture
def vendor(db):
    return Vendor.objects.create(name="Northsea Marine Underwriters B.V.", vat_number="NL859102374B01",
                                 iban="NL91ABNA0417164300", country="NL", kind="insurer")


@pytest.fixture
def shipment(corridors):
    eta = timezone.now() + timedelta(days=10)
    s = Shipment.objects.create(
        ref="MT Aurelia Grace", mode="SEA", cargo="Crude palm oil", quantity=Decimal("8000"), unit="mt",
        cargo_value=Decimal("7640000"), origin="Belawan, ID", destination="Mersin, TR",
        origin_latlon=[3.78, 98.69], dest_latlon=[36.78, 34.63], corridor=corridors["RED_SEA_SUEZ"],
        eta_planned=eta, eta_current=eta,
    )
    CostLine.objects.create(shipment=s, category="FREIGHT", quoted=Decimal("312000"), projected=Decimal("312000"))
    CostLine.objects.create(shipment=s, category="INSURANCE", quoted=Decimal("11460"), projected=Decimal("11460"))
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
