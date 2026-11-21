"""Expense bookkeeping. Owned by A."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import CostLine, ExpenseDocument


def apply_actual_cost(doc: ExpenseDocument) -> CostLine:
    """Called when a doc is APPROVED/EXPORTED: fills CostLine.actual."""
    raise NotImplementedError("TODO(A): apply_actual_cost")
