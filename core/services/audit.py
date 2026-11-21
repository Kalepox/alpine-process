"""Audit trail. Owned by A.

Every service calls `log(...)`. AuditEvent rows are append-only (see core.models).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import models
from django.db.models.fields.files import FieldFile

from core.models import ActorType, AuditEvent

if TYPE_CHECKING:
    from core.models import ExpenseDocument, Shipment

# Fields worth recording in before/after, per model. Anything else falls back
# to "all concrete fields".
SNAPSHOT_FIELDS: dict[str, list[str]] = {
    "ExpenseDocument": [
        "status", "doc_type", "number", "date", "category", "net", "vat", "total",
        "currency", "iban", "quantity", "vendor_id", "shipment_id",
    ],
    "Shipment": ["status", "corridor_id", "eta_planned", "eta_current", "customs_status"],
    "Corridor": ["status", "status_reason", "status_source"],
    "PreApproval": ["status", "amount_cap", "categories", "route_option_id", "decided_by_id", "note"],
    "PendingAction": ["action_type", "status", "payload", "decided_by_id", "note"],
    "Issue": ["code", "severity", "resolved", "resolution", "resolution_note", "resolved_by_id"],
    "Alert": ["code", "severity", "resolved"],
    "CostLine": ["category", "quoted", "projected", "actual", "preapproval_id"],
    "WeeklyReport": ["status", "period_start", "period_end", "approved_by_id", "sent_at"],
}


def json_safe(value: Any) -> Any:
    """Make Decimals, dates, files and nested containers JSON-serialisable."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, FieldFile):
        return value.name or ""
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return value


def snapshot(obj) -> dict:
    """JSON-safe dict of the fields that matter, for before/after."""
    if obj is None:
        return {}
    names = SNAPSHOT_FIELDS.get(type(obj).__name__)
    if names is None:
        names = [f.attname for f in obj._meta.concrete_fields]
    return {name: json_safe(getattr(obj, name)) for name in names}


def log(
    action: str,
    *,
    actor_type: str,  # "agent" | "user" | "system"
    actor_name: str,
    actor_user=None,
    shipment: Shipment | None = None,
    document: ExpenseDocument | None = None,
    before: dict | None = None,
    after: dict | None = None,
    detail: str = "",
    input_sha256: str = "",
) -> AuditEvent:
    """Append one AuditEvent.

    If only `document` is given, the event is also linked to the document's
    shipment, so "filter by shipment" shows document steps too. `input_sha256`
    defaults to the document's file hash.
    """
    if actor_type not in ActorType.values:
        raise ValueError(f"actor_type must be one of {ActorType.values}, got {actor_type!r}")
    if document is not None:
        if shipment is None and document.shipment_id:
            shipment = document.shipment
        input_sha256 = input_sha256 or document.file_sha256
    return AuditEvent.objects.create(
        action=action,
        actor_type=actor_type,
        actor_name=actor_name or (actor_user.get_username() if actor_user else actor_type),
        actor_user=actor_user,
        shipment=shipment,
        document=document,
        before=json_safe(before) if before is not None else None,
        after=json_safe(after) if after is not None else None,
        detail=detail,
        input_sha256=input_sha256,
    )
