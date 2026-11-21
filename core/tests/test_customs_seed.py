"""A.4: customs reference data + shipping-document checklists."""
from decimal import Decimal

import pytest
from django.core.management import call_command

from core.checklists import missing_kinds, required_kinds
from core.models import CustomsRate, Shipment, ShipmentDocument

pytestmark = pytest.mark.django_db


def test_required_kinds_by_mode_and_preference():
    sea = Shipment(mode="SEA", preferential_origin_claimed=True)
    air = Shipment(mode="AIR", preferential_origin_claimed=False)
    road = Shipment(mode="ROAD", preferential_origin_claimed=False)
    assert required_kinds(sea) == [
        "commercial_invoice", "bill_of_lading", "packing_list", "inspection_certificate", "certificate_of_origin",
    ]
    assert required_kinds(air) == ["commercial_invoice", "airway_bill", "packing_list", "inspection_certificate"]
    assert required_kinds(road) == ["commercial_invoice", "packing_list"]


def test_seed_customs_rates_and_checklists():
    call_command("seed_demo")
    call_command("seed_demo")  # idempotent
    assert CustomsRate.objects.count() == 7
    palm_id = CustomsRate.objects.get(hs_code="1511.10", origin_country="ID")
    assert palm_id.duty_pct == Decimal("3.800") and palm_id.preferential_duty_pct == Decimal("0.000")
    assert palm_id.requires_certificate

    sundara = Shipment.objects.get(ref="MV Sundara Bay")
    assert missing_kinds(sundara) == ["certificate_of_origin"]
    gap = sundara.documents.get(kind="certificate_of_origin")
    assert gap.status == "MISSING" and gap.number == ""

    for s in Shipment.objects.exclude(pk=sundara.pk):
        assert missing_kinds(s) == [], s.ref
        assert s.documents.count() == len(required_kinds(s))
    assert ShipmentDocument.objects.filter(kind="certificate_of_origin").count() == 2  # Atacama + Sundara


def test_every_shipment_hs_code_has_an_erga_omnes_rate():
    call_command("seed_demo")
    for s in Shipment.objects.exclude(hs_code=""):
        assert CustomsRate.objects.filter(hs_code=s.hs_code, origin_country="*").exists(), s.ref
