"""Audit trail. Owned by A."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import AuditEvent, ExpenseDocument, Shipment


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
    """Append one AuditEvent. Never updated or deleted afterwards."""
    raise NotImplementedError("TODO(A): audit.log")


def snapshot(obj) -> dict:
    """JSON-safe dict of the fields that matter, for before/after."""
    raise NotImplementedError("TODO(A): audit.snapshot")
