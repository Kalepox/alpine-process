"""Expense documents: intake, extracted fields, actual cost. Owned by A."""
from __future__ import annotations

import hashlib
from decimal import Decimal

from django.core.files.base import ContentFile, File
from django.db import transaction
from django.db.models import Sum

from core.banking import normalize_iban
from core.models import CostLine, ExpenseDocument, Shipment, Vendor
from core.services import audit
from core.services.errors import InvalidTransition

# Statuses in which extracted fields may still change. After READY the payment
# payload is frozen (decide_payment re-checks it anyway).
EDITABLE_STATUSES = {
    ExpenseDocument.Status.RECEIVED,
    ExpenseDocument.Status.PROCESSING,
    ExpenseDocument.Status.NEEDS_REVIEW,
    ExpenseDocument.Status.FAILED,
}
EXTRACTION_FIELDS = {
    "vendor", "shipment", "doc_type", "number", "date", "category",
    "net", "vat", "total", "currency", "iban", "quantity",
}
# Spend counted as "actual" once a human approved it.
SPENT_STATUSES = [ExpenseDocument.Status.APPROVED, ExpenseDocument.Status.EXPORTED]


def receive_document(
    file,
    *,
    filename: str | None = None,
    actor_type: str,
    actor_name: str,
    actor_user=None,
    shipment: Shipment | None = None,
) -> ExpenseDocument:
    """Create an ExpenseDocument(RECEIVED) from an upload (UploadedFile / File / bytes).

    Duplicate files are NOT refused here: the DUPLICATE rule (B) flags them so a
    human sees it.
    """
    if isinstance(file, (bytes, bytearray)):
        file = ContentFile(bytes(file), name=filename or "document")
    elif not isinstance(file, File):
        file = File(file, name=filename)
    name = filename or getattr(file, "name", None) or "document"
    name = name.replace("\\", "/").rsplit("/", 1)[-1]

    sha = hashlib.sha256()
    for chunk in file.chunks():
        sha.update(chunk)
    file.seek(0)

    with transaction.atomic():
        doc = ExpenseDocument(shipment=shipment, file_sha256=sha.hexdigest())
        doc.file.save(name, file, save=False)
        doc.save()
        audit.log(
            "document.received", actor_type=actor_type, actor_name=actor_name, actor_user=actor_user,
            document=doc, after=audit.snapshot(doc), detail=name,
        )
    return doc


def apply_extraction(
    doc: ExpenseDocument,
    fields: dict,
    *,
    actor_name: str,
    actor_type: str = "agent",
    actor_user=None,
    extracted: dict | None = None,
    agent_summary: str | None = None,
) -> ExpenseDocument:
    """Write extracted / matched fields onto a document (never `status`).

    `fields` keys: vendor, shipment (instances or ids via vendor_id/shipment_id),
    doc_type, number, date, category, net, vat, total, currency, iban, quantity.
    """
    fields = dict(fields)
    for rel in ("vendor", "shipment"):
        if f"{rel}_id" in fields:
            model = Vendor if rel == "vendor" else Shipment
            rel_id = fields.pop(f"{rel}_id")
            fields[rel] = model.objects.get(pk=rel_id) if rel_id else None
    unknown = set(fields) - EXTRACTION_FIELDS
    if unknown:
        raise ValueError(f"apply_extraction: fields not allowed: {sorted(unknown)}")

    with transaction.atomic():
        locked = ExpenseDocument.objects.select_for_update().get(pk=doc.pk)
        if locked.status not in EDITABLE_STATUSES:
            raise InvalidTransition(f"document {doc.pk} is {locked.status}: fields are frozen")
        before = audit.snapshot(locked)
        for name, value in fields.items():
            if name == "iban":
                value = normalize_iban(value)
            elif name in {"net", "vat", "total", "quantity"} and value is not None:
                value = Decimal(str(value))
            elif value is None and name in {"doc_type", "number", "category", "iban", "currency"}:
                value = ""
            setattr(locked, name, value)
        if extracted is not None:
            locked.extracted = audit.json_safe(extracted)
        if agent_summary is not None:
            locked.agent_summary = agent_summary
        locked.save()
        audit.log(
            "document.fields_updated", actor_type=actor_type, actor_name=actor_name, actor_user=actor_user,
            document=locked, before=before, after=audit.snapshot(locked),
        )
    doc.refresh_from_db()
    return doc


def apply_actual_cost(doc: ExpenseDocument) -> CostLine | None:
    """Recompute CostLine.actual for the doc's (shipment, category) from all
    approved/exported docs. Idempotent. Returns None for docs without a
    shipment or category (nothing to book against).

    Costs are booked net of VAT (VAT is recoverable); total is used when net is missing.
    """
    if not doc.shipment_id or not doc.category:
        return None
    with transaction.atomic():
        line = (
            CostLine.objects.select_for_update()
            .filter(shipment_id=doc.shipment_id, category=doc.category)
            .order_by("id")
            .first()
        )
        if line is None:
            line = CostLine.objects.create(shipment_id=doc.shipment_id, category=doc.category, note="created on first actual")
        before = audit.snapshot(line)
        docs = ExpenseDocument.objects.filter(
            shipment_id=doc.shipment_id, category=doc.category, status__in=SPENT_STATUSES
        )
        net_sum = docs.filter(net__isnull=False).aggregate(s=Sum("net"))["s"] or Decimal("0")
        total_sum = docs.filter(net__isnull=True).aggregate(s=Sum("total"))["s"] or Decimal("0")
        line.actual = net_sum + total_sum
        line.save(update_fields=["actual"])
        audit.log(
            "costline.actual_updated", actor_type="system", actor_name="expenses.apply_actual_cost",
            document=doc, before=before, after=audit.snapshot(line),
        )
    return line
