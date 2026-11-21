"""process_document: an expense document against what was approved (plan.md §B.7, secondary track). Owned by B.

    PROCESSING ─► doc_intake (Gemini reads the file)          [agent.doc_intake.*]
               ─► RuleContext from the DB ─► RuleEngine       [rules.run]   (source of truth)
               ─► doc_explainer (wording only, validated)     [explainer.result]
               ─► record_extraction + create_issues
               ─► NEEDS_REVIEW (any block issue) | READY + request_payment
Never pays: request_payment creates a PendingAction a human approves.
"""
from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass

from google.genai import types

from core.services import audit

from autopilot.agents.documents import (
    DocExplanation,
    doc_explainer,
    doc_intake,
    fallback_explanation,
    validate_explanation,
)
from autopilot.audit_plugin import jsonable
from autopilot.domain.rules import RuleContext, RuleEngine
from autopilot.domain.schemas import ExtractedExpense, IssueDraft
from autopilot.runner import MODEL, PROMPT_VERSION, gemini_available, run_agent_sync


class IntakeUnavailable(Exception):
    """Reading a document needs Gemini."""


@dataclass
class DocOutcome:
    extracted: ExtractedExpense
    issues: list[IssueDraft]  # wording from the explainer when valid, facts and codes from the rules
    explanation: DocExplanation

    @property
    def blocked(self) -> bool:
        return any(i.severity == "block" for i in self.issues)


# ---------------------------------------------------------------- pure core
def extract(data: bytes, mime_type: str, *, run, log=None) -> ExtractedExpense:
    """log: optional audit sink for the agent steps (evals record in memory)."""
    if not gemini_available():
        raise IntakeUnavailable("Reading documents needs Gemini (GOOGLE_API_KEY).")
    parts = [types.Part.from_bytes(data=data, mime_type=mime_type), types.Part(text="Extract this document.")]
    return ExtractedExpense.model_validate(run_agent_sync(doc_intake, parts, run=run, log=log) or {})


def check(exp: ExtractedExpense, ctx: RuleContext, *, run, engine: RuleEngine | None = None) -> list[IssueDraft]:
    drafts = (engine or RuleEngine()).run(exp, ctx)
    audit.log("rules.run", actor_type="agent", actor_name="rule_engine", run=run,
              input={"extracted": jsonable(exp), "category": ctx.category,
                     "vendor": jsonable(ctx.vendor.__dict__) if ctx.vendor else None,
                     "preapprovals": [jsonable(p.__dict__) for p in ctx.preapprovals], "prior_docs": len(ctx.prior_docs)},
              output={"fired": [jsonable(d) for d in drafts]})
    return drafts


def explain(exp: ExtractedExpense, drafts: list[IssueDraft], *, run) -> tuple[list[IssueDraft], DocExplanation]:
    """Reworded issues (same codes, severities and facts) + summary; fallback to the rules' own text."""
    payload = {"document": jsonable(exp), "issues": [jsonable(d) for d in drafts]}
    expl, reason = None, "Gemini not configured"
    if gemini_available():
        try:
            raw = run_agent_sync(doc_explainer, json.dumps(payload, ensure_ascii=False), run=run)
            expl, reason = validate_explanation(raw, drafts, payload)
        except Exception as e:
            reason = f"explainer failed: {type(e).__name__}: {e}"
    source = "doc_explainer"
    if expl is None:
        expl, source = fallback_explanation(exp, drafts), "fallback"
    audit.log("explainer.result", actor_type="agent", actor_name="doc_explainer", run=run, output=jsonable(expl),
              detail=f"source={source}" + (f"; fallback reason: {reason}" if source == "fallback" else ""))
    issues = [d.model_copy(update={"message": e.message, "suggested_action": e.suggested_action})
              for d, e in zip(drafts, expl.issues)]
    return issues, expl


def assess_document(data: bytes, mime_type: str, context_for, *, run) -> DocOutcome:
    """context_for: ExtractedExpense -> RuleContext (the DB lookup, injected so this stays testable)."""
    exp = extract(data, mime_type, run=run)
    drafts = check(exp, context_for(exp), run=run)
    issues, expl = explain(exp, drafts, run=run)
    return DocOutcome(exp, issues, expl)


# ---------------------------------------------------------------- pipeline entrypoint (ORM)
def process_document(document_id: int) -> None:
    from core.models import ExpenseDocument
    from core.services import approvals, expenses

    doc = ExpenseDocument.objects.get(pk=document_id)
    run = audit.start_run("process_document", trigger="system", document=doc,
                          input={"file": doc.file.name, "file_sha256": doc.file_sha256}, model=MODEL, prompt_version=PROMPT_VERSION)
    approvals.set_doc_status(doc, "PROCESSING", actor_type="agent", actor_name="doc_intake")
    try:
        with doc.file.open("rb") as f:
            data = f.read()
        mime = mimetypes.guess_type(doc.file.name)[0] or "application/pdf"
        matched = {}

        def context_for(exp: ExtractedExpense) -> RuleContext:
            ctx, matched["vendor"], matched["shipment"] = rule_context(doc, exp)
            matched["category"] = ctx.category
            return ctx

        out = assess_document(data, mime, context_for, run=run)
        expenses.record_extraction(doc, jsonable(out.extracted), vendor=matched["vendor"], shipment=matched["shipment"],
                                   category=matched["category"], summary=out.explanation.summary,
                                   actor_name="doc_intake", run=run)
        approvals.create_issues(doc, out.issues, actor_name="rule_engine")
        if out.blocked:
            approvals.set_doc_status(doc, "NEEDS_REVIEW", actor_type="agent", actor_name="rule_engine")
        else:
            approvals.set_doc_status(doc, "READY", actor_type="agent", actor_name="rule_engine")
            approvals.request_payment(doc, requested_by="autopilot")
    except Exception as e:
        audit.log("pipeline.failed", actor_type="system", actor_name="process_document", run=run, document=doc,
                  detail=f"{type(e).__name__}: {e}")
        approvals.set_doc_status(doc, "FAILED", actor_type="system", actor_name="process_document", detail=str(e))
        audit.finish_run(run, "FAILED", summary=str(e))
        raise
    audit.finish_run(run, "OK", summary=f"{len(out.issues)} issue(s); {'NEEDS_REVIEW' if out.blocked else 'READY'}")


def rule_context(doc, exp: ExtractedExpense):
    """(RuleContext, vendor, shipment) from the database: vendor by VAT, shipment by link or hint."""
    from django.db.models import Sum

    from core.models import ExpenseDocument, Shipment, Vendor

    from autopilot.domain.rules import PreapprovalFacts, PriorDoc, VendorFacts, category_for

    vat = "".join((exp.vendor_vat or "").split()).upper()
    vendor = Vendor.objects.filter(vat_number__iexact=vat).first() if vat else None
    shipment = doc.shipment
    if shipment is None and exp.shipment_hint:
        shipment = Shipment.objects.filter(ref__icontains=exp.shipment_hint.strip()).first()
    category = category_for(exp)

    counted = ExpenseDocument.objects.exclude(pk=doc.pk).filter(status__in=["READY", "APPROVED", "EXPORTED"])
    preapprovals = []
    if shipment is not None:
        for p in shipment.preapprovals.filter(status="APPROVED"):
            spent = counted.filter(shipment=shipment, category__in=p.categories).aggregate(s=Sum("total"))["s"]
            preapprovals.append(PreapprovalFacts(p.pk, p.amount_cap, list(p.categories), spent or 0))
    prior = []
    if vendor is not None:
        for d in ExpenseDocument.objects.exclude(pk=doc.pk).exclude(status__in=["REJECTED", "FAILED"]).filter(vendor=vendor):
            prior.append(PriorDoc(d.pk, d.number, d.total, d.date))
    ctx = RuleContext(
        vendor=VendorFacts(vendor.name, vendor.vat_number, vendor.iban) if vendor else None,
        category=category, preapprovals=preapprovals, prior_docs=prior,
        shipment_ref=shipment.ref if shipment else exp.shipment_hint,
    )
    return ctx, vendor, shipment
