"""A.5: SEPA/CSV export, weekly report sending, demo_reset."""
import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from lxml import etree

from core.models import AuditEvent, CostLine, ExpenseDocument, ExportBatch, PendingAction, Shipment, WeeklyReport
from core.services import approvals
from core.services.errors import InvalidTransition, NothingToExport
from core.services.export import build_export_batch, execution_date

pytestmark = pytest.mark.django_db
NS = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}


@pytest.fixture
def approved_doc(ready_doc, marco):
    doc, pa = ready_doc
    approvals.decide_payment(pa.pk, marco, True)
    doc.refresh_from_db()
    return doc


# ------------------------------------------------------------------ export
def test_ops_cannot_export(approved_doc, elena):
    with pytest.raises(PermissionDenied):
        build_export_batch(elena)


def test_nothing_to_export(marco):
    with pytest.raises(NothingToExport):
        build_export_batch(marco)


def test_export_only_approved_and_files_are_valid(approved_doc, make_doc, marco, settings):
    still_ready = make_doc(number="NMU-2", total="500.00")
    approvals.set_doc_status(still_ready, "READY", actor_type="agent", actor_name="agent.x")

    batch = build_export_batch(marco)

    approved_doc.refresh_from_db()
    still_ready.refresh_from_db()
    assert approved_doc.status == "EXPORTED" and still_ready.status == "READY"
    assert list(batch.documents.all()) == [approved_doc] and batch.total == Decimal("14800.00")

    xml = etree.fromstring(batch.sepa_file.read())
    assert xml.findtext(".//p:GrpHdr/p:NbOfTxs", namespaces=NS) == "1"
    assert xml.findtext(".//p:GrpHdr/p:CtrlSum", namespaces=NS) == "14800.00"
    assert xml.findtext(".//p:Dbtr/p:Nm", namespaces=NS) == settings.DEMO_COMPANY["name"]
    assert xml.findtext(".//p:DbtrAcct//p:IBAN", namespaces=NS) == settings.DEMO_COMPANY["iban"]
    assert xml.findtext(".//p:CdtrAcct//p:IBAN", namespaces=NS) == "NL91ABNA0417164300"
    assert xml.findtext(".//p:EndToEndId", namespaces=NS) == f"MONORA-DOC-{approved_doc.pk}"
    assert xml.findtext(".//p:ReqdExctnDt", namespaces=NS) == execution_date().isoformat()

    [row] = list(csv.DictReader(io.StringIO(batch.csv_file.read().decode())))
    assert row == {"doc_number": "NMU-1", "vendor": "Northsea Marine Underwriters B.V.", "vat": "NL859102374B01",
                   "iban": "NL91ABNA0417164300", "amount": "14800.00", "currency": "EUR",
                   "category": "INSURANCE", "shipment": "MT Aurelia Grace"}

    ev = AuditEvent.objects.get(action="export.batch_created")
    assert ev.actor_user == marco and ev.after is not None and ev.after["count"] == 1
    assert AuditEvent.objects.filter(action="document.status_changed", document=approved_doc,
                                     after__status="EXPORTED").exists()
    assert CostLine.objects.get(shipment=approved_doc.shipment, category="INSURANCE").actual == Decimal("14800.00")

    with pytest.raises(NothingToExport):  # already exported: never paid twice
        build_export_batch(marco)


def test_export_skips_document_changed_after_approval(approved_doc, marco):
    ExpenseDocument.objects.filter(pk=approved_doc.pk).update(iban="TR180006200119000006672315")  # tampering
    with pytest.raises(NothingToExport):
        build_export_batch(marco)
    approved_doc.refresh_from_db()
    assert approved_doc.status == "APPROVED" and not ExportBatch.objects.exists()
    ev = AuditEvent.objects.get(action="export.document_skipped")  # kept despite the raise
    assert "iban" in ev.detail


def test_export_only_ids(approved_doc, make_doc, marco):
    other = make_doc(number="NMU-3", total="100.00")
    approvals.set_doc_status(other, "READY", actor_type="agent", actor_name="agent.x")
    approvals.decide_payment(approvals.request_payment(other, requested_by="agent.x").pk, marco, True)
    batch = build_export_batch(marco, only_ids=[other.pk])
    approved_doc.refresh_from_db()
    assert list(batch.documents.all()) == [other] and approved_doc.status == "APPROVED"


def test_execution_date_skips_weekend():
    assert execution_date(date(2026, 11, 21)) == date(2026, 11, 23)  # Saturday -> Monday
    assert execution_date(date(2026, 11, 24)) == date(2026, 11, 24)


# --------------------------------------------------------------- reports
@pytest.fixture
def report_request(settings, tmp_path):
    settings.OUTBOX_DIR = tmp_path / "outbox"
    report = WeeklyReport.objects.create(period_start=date(2026, 11, 16), period_end=date(2026, 11, 22),
                                         body_md="# Logistics spend\n\nFreight +4% vs projected.")
    return report, approvals.request_report_send(report, requested_by="agent.report")


def test_report_send_needs_permission(report_request, elena):
    _, pa = report_request
    with pytest.raises(PermissionDenied):
        approvals.decide_report_send(pa.pk, elena, True)


def test_report_send_writes_outbox_and_emails(report_request, marco, settings):
    report, pa = report_request
    approvals.decide_report_send(pa.pk, marco, True, note="ok to send")
    report.refresh_from_db()
    assert report.status == "SENT" and report.approved_by == marco and report.sent_at
    out = settings.OUTBOX_DIR / "weekly_report_2026-11-16_2026-11-22.md"
    assert out.read_text(encoding="utf-8").startswith("# Logistics spend")
    assert len(mail.outbox) == 1 and mail.outbox[0].to == settings.REPORT_RECIPIENTS
    assert AuditEvent.objects.filter(action="report.sent", actor_user=marco).exists()
    with pytest.raises(InvalidTransition):
        approvals.decide_report_send(pa.pk, marco, True)


def test_report_send_rejected_stays_draft(report_request, marco, settings):
    report, pa = report_request
    approvals.decide_report_send(pa.pk, marco, False, note="numbers look off")
    report.refresh_from_db()
    assert report.status == "DRAFT" and PendingAction.objects.get(pk=pa.pk).status == "REJECTED"
    assert not settings.OUTBOX_DIR.exists() and not mail.outbox


# ------------------------------------------------------------- demo_reset
@pytest.mark.django_db(transaction=True)
def test_demo_reset(settings, tmp_path):
    settings.OUTBOX_DIR = tmp_path / "outbox"
    settings.OUTBOX_DIR.mkdir()
    (settings.OUTBOX_DIR / "old.md").write_text("old")
    call_command("seed_demo")
    Shipment.objects.filter(ref="MV Atacama Star").update(cargo="edited during rehearsal")

    call_command("demo_reset", "--noinput")

    assert Shipment.objects.get(ref="MV Atacama Star").cargo == "Copper cathodes"
    assert Shipment.objects.count() == 6 and ExportBatch.objects.count() == 1
    assert not settings.OUTBOX_DIR.exists()
