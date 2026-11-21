"""Human-in-the-loop gates: "asks first" (DEV_PLAN v2 §0.8, §0.10). Owned by A.

Agents may call:   request_preapproval, request_payment, request_report_send,
                   set_doc_status, create_issues
Humans only:       approve_route, decide_preapproval, decide_payment, decide_report_send,
                   resolve_issue (autopilot/ importing approve_route / decide_* fails
                   core/tests/test_safety.py; resolve_issue needs core.change_issue)
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from core.banking import normalize_iban
from core.models import (
    AgentRun,
    ApprovalStatus,
    CostCategory,
    CostLine,
    Corridor,
    ExpenseDocument,
    Issue,
    PendingAction,
    PreApproval,
    RouteOption,
    Severity,
    Shipment,
    WeeklyReport,
)
from core.services import audit, expenses, shipments
from core.services.errors import InvalidTransition, PayloadMismatch

if TYPE_CHECKING:
    from autopilot.domain.schemas import IssueDraft

__all__ = [
    "DOC_TRANSITIONS", "InvalidTransition", "PayloadMismatch",
    "approve_route", "request_preapproval", "decide_preapproval", "set_doc_status", "create_issues", "resolve_issue",
    "request_payment", "decide_payment", "request_report_send", "decide_report_send",
]

S = ExpenseDocument.Status

# ExpenseDocument.status state machine (DEV_PLAN 0.6).
DOC_TRANSITIONS: dict[str, set[str]] = {
    S.RECEIVED: {S.PROCESSING},
    S.PROCESSING: {S.NEEDS_REVIEW, S.READY, S.FAILED},
    S.FAILED: {S.PROCESSING},
    S.NEEDS_REVIEW: {S.READY, S.REJECTED},
    S.READY: {S.APPROVED, S.REJECTED},
    S.APPROVED: {S.EXPORTED},
    S.REJECTED: set(),
    S.EXPORTED: set(),
}
# Cost-breakdown keys that are not invoiced spend (kept on the option, never booked to CostLine).
NOT_SPEND = {"CARRYING"}

# Targets only a human path may reach: APPROVED via decide_payment, EXPORTED via
# export.build_export_batch, REJECTED via resolve_issue / decide_payment.
HUMAN_ONLY_TARGETS = {S.APPROVED, S.EXPORTED, S.REJECTED}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _require_perm(user, perm: str) -> None:
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active or not user.has_perm(perm):
        who = getattr(user, "username", None)
        raise PermissionDenied(f"user {who!r} lacks permission {perm}")


def _actor_type_for(name: str) -> str:
    """'system…' / 'seed…' / 'collector…' -> system; a real username -> user; else agent."""
    if name.startswith(("system", "seed", "collector")):
        return "system"
    if get_user_model().objects.filter(username=name).exists():
        return "user"
    return "agent"


def _has_open_block_issues(doc: ExpenseDocument) -> bool:
    return doc.issues.filter(resolved=False, severity=Severity.BLOCK).exists()


def _transition(doc: ExpenseDocument, new_status: str, *, actor_type, actor_name, actor_user=None, detail=""):
    """Internal: validate + apply a status change on a row the caller has locked."""
    old = doc.status
    if new_status not in DOC_TRANSITIONS.get(old, set()):
        raise InvalidTransition(f"document {doc.pk}: {old} -> {new_status} is not allowed")
    if new_status == S.READY and _has_open_block_issues(doc):
        raise InvalidTransition(f"document {doc.pk}: open blocking issues, cannot be READY")
    doc.status = new_status
    doc.save(update_fields=["status", "updated_at"])
    audit.log(
        "document.status_changed", actor_type=actor_type, actor_name=actor_name, actor_user=actor_user,
        document=doc, before={"status": old}, after={"status": new_status}, detail=detail,
    )
    return doc


def _lock_doc(doc_or_id) -> ExpenseDocument:
    pk = doc_or_id.pk if isinstance(doc_or_id, ExpenseDocument) else doc_or_id
    return ExpenseDocument.objects.select_for_update().get(pk=pk)


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _to_category(key: str) -> str:
    """Map cost-breakdown keys (e.g. CUSTOMS_DUTY, IMPORT_VAT) onto CostCategory."""
    key = key.upper()
    if key in CostCategory.values:
        return key
    if key.startswith("CUSTOMS") or key.startswith("IMPORT_VAT") or key == "DUTY":
        return CostCategory.CUSTOMS
    return CostCategory.OTHER


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def approve_route(route_option_id: int, user, note: str = "") -> Shipment:
    """perm core.approve_route. The human decision on a route (plan, replan or wait).

    * marks the option chosen (any previously chosen option of the shipment is un-chosen)
    * Shipment.route = option; current_leg_index = 0; progress re-projected
    * PLANNING -> ON_TRACK: departed_at = now, eta_planned = eta_current = now + transit_days,
      CostLine.quoted = projected = cost_breakdown
    * replan / wait: eta_current = now + transit_days (options are computed from the
      cargo's current position), CostLine.projected = cost_breakdown
    * corridor alerts are cleared when every corridor left on the new route is OPEN
    * the audit event links run + route_option and stores the numbers the human saw
    """
    _require_perm(user, "core.approve_route")
    username = user.get_username()
    with transaction.atomic():
        option = RouteOption.objects.select_for_update().get(pk=route_option_id)
        shipment = Shipment.objects.select_for_update().get(pk=option.shipment_id)
        if option.chosen:
            raise InvalidTransition(f"route option {option.pk} is already the approved route")
        if shipment.status == Shipment.Status.ARRIVED:
            raise InvalidTransition(f"shipment {shipment.pk} has arrived")
        seen = audit.snapshot(option)  # what the human approved, frozen in the trail
        before = audit.snapshot(shipment)
        planning = shipment.status == Shipment.Status.PLANNING
        now = timezone.now()

        RouteOption.objects.filter(shipment=shipment, chosen=True).update(chosen=False)
        option.chosen = True
        option.save(update_fields=["chosen"])

        shipment.route = option
        shipment.current_leg_index = 0
        eta = now + timedelta(days=float(option.transit_days)) if option.transit_days else None
        if planning:
            shipment.status = Shipment.Status.ON_TRACK
            shipment.departed_at = shipment.departed_at or now
            shipment.eta_planned = shipment.eta_planned or eta
            shipment.progress_pct = Decimal("0")
        if eta is not None:
            shipment.eta_current = eta
        shipment.save()

        totals: dict[str, Decimal] = {}
        for key, value in (option.cost_breakdown or {}).items():
            if key.upper() in NOT_SPEND:
                continue
            cat = _to_category(key)
            totals[cat] = totals.get(cat, Decimal("0")) + _money(value)
        cost_lines = {}
        for cat, amount in sorted(totals.items()):
            line = CostLine.objects.filter(shipment=shipment, category=cat).order_by("id").first()
            if line is None:
                line = CostLine(shipment=shipment, category=cat)
            if planning:
                line.quoted = amount
            line.projected = amount
            line.save()
            cost_lines[cat] = audit.snapshot(line)

        latest = shipment.positions.order_by("-ts").first()
        if latest is not None and not planning:
            shipments.update_progress(shipment, latest.lat, latest.lon)
        still_bad = Corridor.objects.filter(code__in=option.corridor_codes()).exclude(status=Corridor.Status.OPEN)
        if not still_bad.exists():
            shipments.resolve_alerts(
                shipment, shipments.CORRIDOR_ALERTS, actor_name=username, actor_type="user", actor_user=user,
                run=option.run, reason=f"route approved: {option.label}",
            )
        shipments.refresh_status(shipment, actor_name=username, run=option.run)
        shipment.refresh_from_db()
        audit.log(
            "route.approved", actor_type="user", actor_name=username, actor_user=user,
            run=option.run, route_option=option, shipment=shipment,
            input={"option": seen}, before=before,
            after={"shipment": audit.snapshot(shipment), "cost_lines": cost_lines},
            detail=note or option.label,
        )
    return shipment


# --------------------------------------------------------------------------
# pre-approval of extra spend (a cap expense documents are checked against)
# --------------------------------------------------------------------------
def request_preapproval(
    shipment: Shipment,
    route_option: RouteOption | None,
    amount_cap: Decimal,
    categories: list[str],
    reason: str,
    *,
    requested_by: str,
    run: AgentRun | None = None,
) -> PreApproval:
    """Create a PENDING PreApproval (idempotent per shipment + route option). Never approves."""
    amount_cap = _money(amount_cap)
    if amount_cap <= 0:
        raise ValueError("amount_cap must be positive")
    bad = [c for c in categories if c not in CostCategory.values]
    if bad or not categories:
        raise ValueError(f"categories must be non-empty CostCategory values, bad: {bad}")
    if route_option is not None and route_option.shipment_id != shipment.pk:
        raise ValueError("route_option belongs to another shipment")

    with transaction.atomic():
        existing = PreApproval.objects.filter(
            shipment=shipment, route_option=route_option, status=ApprovalStatus.PENDING
        ).first()
        if existing:
            return existing
        pa = PreApproval.objects.create(
            shipment=shipment, route_option=route_option, amount_cap=amount_cap,
            categories=list(categories), reason=reason, requested_by=requested_by,
        )
        audit.log(
            "preapproval.requested", actor_type=_actor_type_for(requested_by), actor_name=requested_by,
            run=run, shipment=shipment, route_option=route_option, preapproval=pa,
            after=audit.snapshot(pa), detail=reason,
        )
    return pa


def decide_preapproval(preapproval_id: int, user, approve: bool, note: str = "") -> PreApproval:
    """perm core.approve_spend. Approve or reject a spending cap.

    On approve, the shipment's cost lines for the cap's categories are linked to
    it (expense rules check documents against the cap). Moving the shipment onto
    a route is approve_route's job, not this one.
    """
    _require_perm(user, "core.approve_spend")
    username = user.get_username()
    with transaction.atomic():
        pa = PreApproval.objects.select_for_update().get(pk=preapproval_id)
        if pa.status != ApprovalStatus.PENDING:
            raise InvalidTransition(f"pre-approval {pa.pk} already {pa.status}")
        before = audit.snapshot(pa)
        pa.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        pa.decided_by, pa.decided_at, pa.note = user, timezone.now(), note
        pa.save()

        cost_lines = {}
        if approve:
            for cat in pa.categories:
                line = CostLine.objects.filter(shipment_id=pa.shipment_id, category=cat).order_by("id").first()
                if line is None:
                    line = CostLine(shipment_id=pa.shipment_id, category=cat)
                line.preapproval = pa
                line.save()
                cost_lines[cat] = audit.snapshot(line)

        audit.log(
            "preapproval.approved" if approve else "preapproval.rejected",
            actor_type="user", actor_name=username, actor_user=user,
            run=pa.route_option.run if pa.route_option else None,
            shipment=pa.shipment, route_option=pa.route_option, preapproval=pa,
            input={"preapproval": before}, after={"preapproval": audit.snapshot(pa), "cost_lines": cost_lines},
            detail=note,
        )
    return pa


# --------------------------------------------------------------------------
# expense documents
# --------------------------------------------------------------------------
def set_doc_status(
    doc: ExpenseDocument,
    new_status: str,
    *,
    actor_type: str,
    actor_name: str,
    actor_user=None,
    detail: str = "",
) -> ExpenseDocument:
    """Validated against DOC_TRANSITIONS; raises InvalidTransition.

    APPROVED / EXPORTED / REJECTED are refused here: only decide_payment,
    build_export_batch and resolve_issue can reach them.
    """
    if new_status in HUMAN_ONLY_TARGETS:
        raise InvalidTransition(f"{new_status} can only be reached through a human decision")
    with transaction.atomic():
        locked = _lock_doc(doc)
        _transition(locked, new_status, actor_type=actor_type, actor_name=actor_name, actor_user=actor_user, detail=detail)
    doc.status = locked.status
    return locked


def create_issues(
    doc_or_shipment: ExpenseDocument | Shipment,
    drafts: Sequence[IssueDraft | dict],
    *,
    actor_name: str,
) -> list[Issue]:
    """Persist rule findings. Idempotent per open (target, code): re-processing a
    document does not duplicate its open issues."""
    if isinstance(doc_or_shipment, ExpenseDocument):
        document, shipment = doc_or_shipment, doc_or_shipment.shipment
    elif isinstance(doc_or_shipment, Shipment):
        document, shipment = None, doc_or_shipment
    else:
        raise TypeError("create_issues expects an ExpenseDocument or a Shipment")
    actor_type = _actor_type_for(actor_name)

    result = []
    with transaction.atomic():
        for draft in drafts:
            data = dict(draft) if isinstance(draft, dict) else draft.model_dump()
            code, severity = data["code"], data.get("severity", Severity.BLOCK)
            if code not in Issue.Code.values:
                raise ValueError(f"unknown issue code {code!r}")
            if severity not in (Severity.WARN, Severity.BLOCK):
                raise ValueError(f"issue severity must be warn|block, got {severity!r}")
            open_same = Issue.objects.filter(code=code, resolved=False)
            open_same = open_same.filter(document=document) if document else open_same.filter(document=None, shipment=shipment)
            existing = open_same.first()
            if existing:
                result.append(existing)
                continue
            issue = Issue.objects.create(
                document=document, shipment=shipment, code=code, severity=severity,
                message=data.get("message", ""), suggested_action=data.get("suggested_action", ""),
                facts=audit.json_safe(data.get("facts") or {}),
            )
            audit.log(
                "issue.created", actor_type=actor_type, actor_name=actor_name, shipment=shipment,
                document=document, after=audit.snapshot(issue), detail=issue.message,
            )
            result.append(issue)
    return result


def resolve_issue(issue_id: int, user, resolution: str, note: str = "") -> Issue:
    """perm core.change_issue. resolution: "accepted" | "overridden" (note required) | "rejected".

    rejected  -> the document is REJECTED, its pending payment cancelled.
    otherwise -> when no blocking issue is left on a NEEDS_REVIEW document it
                 moves to READY and a payment is requested (requested_by=user).
    """
    _require_perm(user, "core.change_issue")
    if resolution not in (Issue.Resolution.ACCEPTED, Issue.Resolution.OVERRIDDEN, Issue.Resolution.REJECTED):
        raise ValueError(f"unknown resolution {resolution!r}")
    if resolution == Issue.Resolution.OVERRIDDEN and not note.strip():
        raise ValueError("overriding an issue requires a note")
    username = user.get_username()

    with transaction.atomic():
        issue = Issue.objects.select_for_update().get(pk=issue_id)
        if issue.resolved:
            raise InvalidTransition(f"issue {issue.pk} is already resolved")
        doc = _lock_doc(issue.document_id) if issue.document_id else None
        before = audit.snapshot(issue)
        now = timezone.now()
        issue.resolved, issue.resolution, issue.resolution_note = True, resolution, note
        issue.resolved_by, issue.resolved_at = user, now
        issue.save()
        audit.log(
            "issue.resolved", actor_type="user", actor_name=username, actor_user=user,
            shipment=issue.shipment, document=doc, before=before, after=audit.snapshot(issue), detail=note,
        )

        if doc is None:
            return issue

        if resolution == Issue.Resolution.REJECTED:
            if doc.status in (S.NEEDS_REVIEW, S.READY):
                for other in doc.issues.filter(resolved=False):
                    other.resolved, other.resolution = True, Issue.Resolution.REJECTED
                    other.resolution_note = f"document rejected via issue #{issue.pk}"
                    other.resolved_by, other.resolved_at = user, now
                    other.save()
                _cancel_pending_payments(doc, user, "document rejected")
                _transition(doc, S.REJECTED, actor_type="user", actor_name=username, actor_user=user,
                            detail=f"rejected on {issue.code}: {note}")
        elif doc.status == S.NEEDS_REVIEW and not _has_open_block_issues(doc):
            _transition(doc, S.READY, actor_type="user", actor_name=username, actor_user=user,
                        detail="all blocking issues resolved")
            request_payment(doc, requested_by=username)
    return issue


def _cancel_pending_payments(doc: ExpenseDocument, user, reason: str) -> None:
    for pa in doc.pending_actions.select_for_update().filter(status=ApprovalStatus.PENDING):
        pa.status, pa.decided_by, pa.decided_at, pa.note = ApprovalStatus.REJECTED, user, timezone.now(), reason
        pa.save()
        audit.log(
            "payment.cancelled", actor_type="user", actor_name=user.get_username(), actor_user=user,
            document=doc, after=audit.snapshot(pa), detail=reason,
        )


# --------------------------------------------------------------------------
# payments
# --------------------------------------------------------------------------
def _payment_payload(doc: ExpenseDocument) -> dict:
    vendor, shipment = doc.vendor, doc.shipment
    return {
        "amount": str(_money(doc.total)),
        "currency": doc.currency,
        "iban": normalize_iban(doc.iban),
        "vendor": vendor.name if vendor is not None else (doc.extracted or {}).get("vendor_name", ""),
        "vendor_id": doc.vendor_id,
        "document_id": doc.pk,
        "document_number": doc.number,
        "shipment": shipment.ref if shipment is not None else None,
        "category": doc.category,
        "date": doc.date.isoformat() if doc.date else None,
    }


def _payload_mismatch(payload: dict, doc: ExpenseDocument) -> dict:
    """Fields whose approved-for value differs from the document now. Empty = OK."""
    current = _payment_payload(doc)
    diffs = {}
    if doc.total is None or _money(payload["amount"]) != _money(doc.total):
        diffs["amount"] = {"requested": payload["amount"], "now": current["amount"] if doc.total is not None else None}
    for key in ("iban", "currency", "vendor_id"):
        if payload.get(key) != current[key]:
            diffs[key] = {"requested": payload.get(key), "now": current[key]}
    return diffs


def request_payment(doc: ExpenseDocument, *, requested_by: str) -> PendingAction:
    """Create PendingAction(PAY, PENDING) for a READY document (idempotent). Never pays.

    The payload freezes amount, IBAN, currency and vendor at request time.
    """
    with transaction.atomic():
        locked = _lock_doc(doc)
        if locked.status != S.READY:
            raise InvalidTransition(f"document {locked.pk} is {locked.status}, payment needs READY")
        existing = locked.pending_actions.filter(
            action_type=PendingAction.ActionType.PAY, status=ApprovalStatus.PENDING
        ).first()
        if existing:
            return existing
        if locked.total is None or not locked.iban:
            raise ValueError(f"document {locked.pk}: cannot request payment without total and IBAN")
        pa = PendingAction.objects.create(
            action_type=PendingAction.ActionType.PAY, document=locked,
            payload=_payment_payload(locked), requested_by=requested_by,
        )
        audit.log(
            "payment.requested", actor_type=_actor_type_for(requested_by), actor_name=requested_by,
            document=locked, after=audit.snapshot(pa),
        )
    return pa


def decide_payment(pending_action_id: int, user, approve: bool, note: str = "") -> PendingAction:
    """perm core.approve_payment.

    Approve: refuses with PayloadMismatch (and an audit event) if the document's
    amount / IBAN / currency / vendor changed since the request. Otherwise the
    document goes APPROVED and its actual cost is booked.
    Reject: the document goes REJECTED.
    """
    _require_perm(user, "core.approve_payment")
    username = user.get_username()
    mismatch: dict = {}
    with transaction.atomic():
        pa = PendingAction.objects.select_for_update().get(pk=pending_action_id)
        if pa.action_type != PendingAction.ActionType.PAY:
            raise InvalidTransition(f"pending action {pa.pk} is not a payment")
        if pa.status != ApprovalStatus.PENDING:
            raise InvalidTransition(f"payment {pa.pk} already {pa.status}")
        doc = _lock_doc(pa.document_id)

        if approve:
            if doc.status != S.READY:
                raise InvalidTransition(f"document {doc.pk} is {doc.status}, not READY")
            if _has_open_block_issues(doc):
                raise InvalidTransition(f"document {doc.pk} has open blocking issues")
            mismatch = _payload_mismatch(pa.payload, doc)
            if not mismatch:
                pa.status, pa.decided_by, pa.decided_at, pa.note = ApprovalStatus.APPROVED, user, timezone.now(), note
                pa.save()
                _transition(doc, S.APPROVED, actor_type="user", actor_name=username, actor_user=user,
                            detail=f"payment #{pa.pk} approved")
                audit.log("payment.approved", actor_type="user", actor_name=username, actor_user=user,
                          document=doc, after=audit.snapshot(pa), detail=note)
                expenses.apply_actual_cost(doc)
        else:
            pa.status, pa.decided_by, pa.decided_at, pa.note = ApprovalStatus.REJECTED, user, timezone.now(), note
            pa.save()
            audit.log("payment.rejected", actor_type="user", actor_name=username, actor_user=user,
                      document=doc, after=audit.snapshot(pa), detail=note)
            if doc.status in (S.READY, S.NEEDS_REVIEW):
                _transition(doc, S.REJECTED, actor_type="user", actor_name=username, actor_user=user,
                            detail=f"payment #{pa.pk} rejected")

    if mismatch:
        audit.log(
            "payment.approval_refused", actor_type="user", actor_name=username, actor_user=user,
            document=doc, before=pa.payload, after=_payment_payload(doc),
            detail=f"document changed after the payment was requested: {sorted(mismatch)}",
        )
        raise PayloadMismatch(f"payment {pa.pk}: document changed since request: {mismatch}")
    return pa


# --------------------------------------------------------------------------
# weekly report
# --------------------------------------------------------------------------
def request_report_send(report: WeeklyReport, *, requested_by: str) -> PendingAction:
    """Create PendingAction(SEND_REPORT, PENDING) for a DRAFT report (idempotent). Never sends."""
    with transaction.atomic():
        locked = WeeklyReport.objects.select_for_update().get(pk=report.pk)
        if locked.status != WeeklyReport.Status.DRAFT:
            raise InvalidTransition(f"report {locked.pk} is {locked.status}, only DRAFT can be sent")
        existing = locked.pending_actions.filter(
            action_type=PendingAction.ActionType.SEND_REPORT, status=ApprovalStatus.PENDING
        ).first()
        if existing:
            return existing
        pa = PendingAction.objects.create(
            action_type=PendingAction.ActionType.SEND_REPORT, report=locked, requested_by=requested_by,
            payload={"period_start": locked.period_start.isoformat(), "period_end": locked.period_end.isoformat()},
        )
        audit.log(
            "report.send_requested", actor_type=_actor_type_for(requested_by), actor_name=requested_by,
            after=audit.snapshot(pa), detail=f"weekly report {locked.period_start}..{locked.period_end}",
        )
    return pa


def decide_report_send(pending_action_id: int, user, approve: bool, note: str = "") -> WeeklyReport:
    """perm core.approve_spend.

    Approve: DRAFT -> APPROVED -> SENT. "Sending" = write outbox/weekly_report_<start>_<end>.md
    and email it via EMAIL_BACKEND (console by default). Reject: report stays DRAFT.
    """
    _require_perm(user, "core.approve_spend")
    username = user.get_username()
    with transaction.atomic():
        pa = PendingAction.objects.select_for_update().get(pk=pending_action_id)
        if pa.action_type != PendingAction.ActionType.SEND_REPORT:
            raise InvalidTransition(f"pending action {pa.pk} is not a report send")
        if pa.status != ApprovalStatus.PENDING:
            raise InvalidTransition(f"report send {pa.pk} already {pa.status}")
        report = WeeklyReport.objects.select_for_update().get(pk=pa.report_id)
        before = audit.snapshot(report)
        now = timezone.now()
        pa.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        pa.decided_by, pa.decided_at, pa.note = user, now, note
        pa.save()

        if not approve:
            audit.log("report.send_rejected", actor_type="user", actor_name=username, actor_user=user,
                      before=before, after=audit.snapshot(report), detail=note)
            return report

        if report.status != WeeklyReport.Status.DRAFT:
            raise InvalidTransition(f"report {report.pk} is {report.status}, only DRAFT can be sent")
        report.status, report.approved_by = WeeklyReport.Status.APPROVED, user
        report.save()
        path = _send_report(report)
        report.status, report.sent_at = WeeklyReport.Status.SENT, timezone.now()
        report.save()
        audit.log("report.sent", actor_type="user", actor_name=username, actor_user=user,
                  before=before, after={**audit.snapshot(report), "outbox_file": str(path)}, detail=note)
    return report


def _send_report(report: WeeklyReport):
    """Write the report to the outbox and email it. Returns the file path."""
    from django.conf import settings
    from django.core.mail import send_mail

    outbox = settings.OUTBOX_DIR
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"weekly_report_{report.period_start:%Y-%m-%d}_{report.period_end:%Y-%m-%d}.md"
    path.write_text(report.body_md or "", encoding="utf-8")
    send_mail(
        subject=f"Logistics spend report {report.period_start:%d %b} – {report.period_end:%d %b %Y}",
        message=report.body_md or "",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=settings.REPORT_RECIPIENTS,
    )
    return path
