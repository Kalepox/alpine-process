"""Human-in-the-loop gates: "asks first". Owned by A.

Agents may call:   request_preapproval, request_payment, request_report_send,
                   set_doc_status, create_issues
Humans only:       decide_preapproval, decide_payment, decide_report_send, resolve_issue
                   (autopilot/ importing a decide_* function fails core/tests/test_safety.py)
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autopilot.domain.schemas import IssueDraft
    from core.models import (
        ExpenseDocument,
        Issue,
        PendingAction,
        PreApproval,
        RouteOption,
        Shipment,
        WeeklyReport,
    )


class InvalidTransition(Exception):
    """Status change not allowed by DOC_TRANSITIONS (or action already decided)."""


# ExpenseDocument.status state machine (DEV_PLAN 0.6). EXPORTED is set only by
# core.services.export.build_export_batch, APPROVED only by decide_payment.
DOC_TRANSITIONS: dict[str, set[str]] = {
    "RECEIVED": {"PROCESSING"},
    "PROCESSING": {"NEEDS_REVIEW", "READY", "FAILED"},
    "FAILED": {"PROCESSING"},
    "NEEDS_REVIEW": {"READY", "REJECTED"},
    "READY": {"APPROVED", "REJECTED"},
    "APPROVED": {"EXPORTED"},
    "REJECTED": set(),
    "EXPORTED": set(),
}


# ---- pre-approval of extra spend (reroute) --------------------------------
def request_preapproval(
    shipment: Shipment,
    route_option: RouteOption | None,
    amount_cap: Decimal,
    categories: list[str],
    reason: str,
    *,
    requested_by: str,
) -> PreApproval:
    """Create a PENDING PreApproval. Never approves anything."""
    raise NotImplementedError("TODO(A): request_preapproval")


def decide_preapproval(preapproval_id: int, user, approve: bool, note: str = "") -> PreApproval:
    """perm core.approve_spend. On approve: mark RouteOption.chosen, update
    Shipment.corridor / eta_current, write CostLine.projected per category."""
    raise NotImplementedError("TODO(A): decide_preapproval")


# ---- expense documents ----------------------------------------------------
def set_doc_status(
    doc: ExpenseDocument,
    new_status: str,
    *,
    actor_type: str,
    actor_name: str,
    actor_user=None,
    detail: str = "",
) -> ExpenseDocument:
    """Validated against DOC_TRANSITIONS; raises InvalidTransition."""
    raise NotImplementedError("TODO(A): set_doc_status")


def create_issues(
    doc_or_shipment: ExpenseDocument | Shipment,
    drafts: list[IssueDraft],
    *,
    actor_name: str,
) -> list[Issue]:
    raise NotImplementedError("TODO(A): create_issues")


def resolve_issue(issue_id: int, user, resolution: str, note: str = "") -> Issue:
    """resolution: "accepted" | "overridden" | "rejected".
    May move the doc to READY + request_payment when no block issues remain."""
    raise NotImplementedError("TODO(A): resolve_issue")


# ---- payments -------------------------------------------------------------
def request_payment(doc: ExpenseDocument, *, requested_by: str) -> PendingAction:
    """Create PendingAction(PAY, PENDING). Never pays."""
    raise NotImplementedError("TODO(A): request_payment")


def decide_payment(pending_action_id: int, user, approve: bool, note: str = "") -> PendingAction:
    """perm core.approve_payment. Refuses if payload amount/IBAN no longer match the doc."""
    raise NotImplementedError("TODO(A): decide_payment")


# ---- weekly report --------------------------------------------------------
def request_report_send(report: WeeklyReport, *, requested_by: str) -> PendingAction:
    """Create PendingAction(SEND_REPORT, PENDING). Never sends."""
    raise NotImplementedError("TODO(A): request_report_send")


def decide_report_send(pending_action_id: int, user, approve: bool, note: str = "") -> WeeklyReport:
    """perm core.approve_spend. On approve -> SENT (demo: write to outbox/)."""
    raise NotImplementedError("TODO(A): decide_report_send")
