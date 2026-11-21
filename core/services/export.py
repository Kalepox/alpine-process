"""Payment export: the irreversible step. Owned by A.

Human-only. autopilot/ must never import this module (core/tests/test_safety.py).
This is the ONLY code path that sets ExpenseDocument.status = EXPORTED.

Pays exactly what the human approved: amount / IBAN / currency come from the
APPROVED PendingAction payload, and a document whose data no longer matches it
is skipped (stays APPROVED, audited) instead of being paid.
"""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.models import ApprovalStatus, ExpenseDocument, ExportBatch, PendingAction
from core.services import audit, expenses
from core.services.approvals import _payload_mismatch, _require_perm, _transition
from core.services.errors import NothingToExport

CSV_COLUMNS = ["doc_number", "vendor", "vat", "iban", "amount", "currency", "category", "shipment"]


def execution_date(today: date | None = None) -> date:
    """Requested execution date: today, or the next Monday on a weekend."""
    today = today or timezone.localdate()
    while today.weekday() >= 5:
        today += timedelta(days=1)
    return today


def _approved_payment(doc: ExpenseDocument) -> PendingAction | None:
    return (
        doc.pending_actions.filter(action_type=PendingAction.ActionType.PAY, status=ApprovalStatus.APPROVED)
        .order_by("-decided_at")
        .first()
    )


def _sepa_xml(rows: list[dict], when: date) -> bytes:
    from sepaxml import SepaTransfer

    company = settings.DEMO_COMPANY
    sepa = SepaTransfer({"name": company["name"], "IBAN": company["iban"], "batch": True, "currency": "EUR"})
    for r in rows:
        sepa.add_payment({
            "name": r["vendor"] or "Unknown creditor",
            "IBAN": r["iban"],
            "amount": int((r["amount"] * 100).to_integral_value()),
            "execution_date": when,
            "description": f"{r['doc_number']} {r['shipment'] or ''}".strip()[:140],
            "endtoend_id": r["end_to_end_id"],
        })
    return sepa.export(validate=True, pretty_print=True)


def _csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({**r, "amount": f"{r['amount']:.2f}"})
    return buf.getvalue().encode("utf-8")


def build_export_batch(user, *, only_ids: list[int] | None = None) -> ExportBatch:
    """perm core.export_payments. Exports every APPROVED EUR document (or only
    `only_ids`) into one SEPA pain.001.001.03 file + CSV, atomically.

    Raises NothingToExport when nothing qualifies, PermissionDenied without the perm.
    """
    _require_perm(user, "core.export_payments")
    username = user.get_username()
    when = execution_date()

    with transaction.atomic():
        locked = ExpenseDocument.objects.select_for_update().filter(status=ExpenseDocument.Status.APPROVED)
        if only_ids is not None:
            locked = locked.filter(pk__in=only_ids)
        ids = list(locked.order_by("pk").values_list("pk", flat=True))
        docs = list(ExpenseDocument.objects.filter(pk__in=ids).select_related("vendor", "shipment").order_by("pk"))

        rows, exported, skipped = [], [], []
        for doc in docs:
            pa = _approved_payment(doc)
            if pa is None:
                skipped.append((doc, "no approved payment"))
                continue
            payload = pa.payload
            if payload.get("currency") != "EUR":
                skipped.append((doc, f"currency {payload.get('currency')} is not SEPA (EUR only)"))
                continue
            if mismatch := _payload_mismatch(payload, doc):
                skipped.append((doc, f"document changed after approval: {sorted(mismatch)}"))
                continue
            vendor, shipment = doc.vendor, doc.shipment
            rows.append({
                "doc_number": doc.number,
                "vendor": vendor.name if vendor is not None else payload.get("vendor", ""),
                "vat": vendor.vat_number if vendor is not None else "",
                "iban": payload["iban"],
                "amount": Decimal(payload["amount"]),
                "currency": payload["currency"],
                "category": doc.category,
                "shipment": shipment.ref if shipment is not None else "",
                "end_to_end_id": f"MONORA-DOC-{doc.pk}",
            })
            exported.append(doc)

        batch = _write_batch(user, when, rows, exported, len(skipped)) if exported else None

    # Logged after the transaction so the trail keeps them even when we raise below.
    for doc, reason in skipped:
        audit.log("export.document_skipped", actor_type="user", actor_name=username, actor_user=user,
                  document=doc, detail=reason)
    if batch is None:
        raise NothingToExport(
            "no approved document to export" + (f" ({len(skipped)} skipped, see audit)" if skipped else "")
        )
    return batch


def _write_batch(user, when: date, rows: list[dict], exported: list[ExpenseDocument], n_skipped: int) -> ExportBatch:
    """Inside build_export_batch's transaction: files, EXPORTED statuses, actual costs, audit."""
    username = user.get_username()
    total = sum((r["amount"] for r in rows), Decimal("0"))
    batch = ExportBatch.objects.create(created_by=user, total=total)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    batch.sepa_file.save(f"sepa_batch_{batch.pk}_{stamp}.xml", ContentFile(_sepa_xml(rows, when)), save=False)
    batch.csv_file.save(f"payments_batch_{batch.pk}_{stamp}.csv", ContentFile(_csv(rows)), save=False)
    batch.save()
    batch.documents.set(exported)

    for doc in exported:
        _transition(doc, ExpenseDocument.Status.EXPORTED, actor_type="user", actor_name=username,
                    actor_user=user, detail=f"exported in SEPA batch #{batch.pk}")
        expenses.apply_actual_cost(doc)

    audit.log(
        "export.batch_created", actor_type="user", actor_name=username, actor_user=user,
        after={"batch": batch.pk, "documents": [d.pk for d in exported], "count": len(exported),
               "total": total, "execution_date": when, "sepa_file": batch.sepa_file.name,
               "csv_file": batch.csv_file.name},
        detail=f"{len(exported)} payment(s), EUR {total:,.2f}" + (f"; {n_skipped} skipped" if n_skipped else ""),
    )
    return batch
