"""Payment export: the irreversible step. Owned by A.

Human-only. autopilot/ must never import this module (core/tests/test_safety.py).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import ExportBatch


def build_export_batch(user) -> ExportBatch:
    """perm core.export_payments; only APPROVED docs; atomic; -> EXPORTED; SEPA pain.001 + CSV."""
    raise NotImplementedError("TODO(A): build_export_batch")
