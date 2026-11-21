"""Expense rules: the source of truth for issues on a document (plan.md §B.3, thresholds as v1). Owned by B.

Pure: the pipeline builds a RuleContext from the database, the rules only read it.
doc_intake (Gemini) extracts the fields, these rules decide the issues,
doc_explainer (Gemini) may only reword them.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal

from .schemas import ExtractedExpense, IssueDraft

MIN_CONFIDENCE = 0.7
DUPLICATE_WINDOW_DAYS = 30
CENT = Decimal("0.02")

# doc_type -> CostCategory; insurance with a war-risk line is WAR_RISK (the reroute surcharge)
DOC_CATEGORY = {
    "freight_bill": "FREIGHT", "insurance_invoice": "INSURANCE", "demurrage_bill": "DEMURRAGE",
    "inspection_invoice": "INSPECTION", "customs": "CUSTOMS", "port": "PORT",
}
# Spend that only exists because of a disruption: it needs an approved pre-approval.
PREAPPROVAL_REQUIRED = {"WAR_RISK", "DEMURRAGE"}

IBAN_ACTION = "Verify with the vendor using contact details from the vendor master, never from this document."


def tolerance(amount: Decimal) -> Decimal:
    """Amounts match within max(EUR 1, 1%)."""
    return max(Decimal("1"), abs(amount) * Decimal("0.01"))


def normalize_iban(iban: str | None) -> str:
    return "".join((iban or "").split()).upper()


def category_for(exp: ExtractedExpense) -> str:
    text = " ".join(li.description.lower() for li in exp.line_items)
    if exp.doc_type == "insurance_invoice" and "war" in text:
        return "WAR_RISK"
    return DOC_CATEGORY.get(exp.doc_type or "", "OTHER")


@dataclass
class VendorFacts:
    name: str
    vat_number: str
    iban: str


@dataclass
class PreapprovalFacts:
    id: int
    amount_cap: Decimal
    categories: list[str]
    already_spent: Decimal = Decimal("0")  # other documents already counted against it


@dataclass
class PriorDoc:
    id: int
    number: str
    total: Decimal | None
    date: dt.date | None


@dataclass
class RuleContext:
    vendor: VendorFacts | None
    category: str
    preapprovals: list[PreapprovalFacts] = field(default_factory=list)  # APPROVED ones of the shipment
    prior_docs: list[PriorDoc] = field(default_factory=list)            # same vendor, not rejected
    shipment_ref: str | None = None


class ExpenseRule(ABC):
    code: str

    @abstractmethod
    def check(self, exp: ExtractedExpense, ctx: RuleContext) -> list[IssueDraft]: ...


class LowConfidenceRule(ExpenseRule):
    code = "LOW_CONFIDENCE"

    def check(self, exp, ctx):
        if exp.total is None:
            return [IssueDraft(code=self.code, severity="block", message="The total amount could not be read.",
                               facts={"confidence": exp.confidence}, suggested_action="Check the document and enter the total.")]
        out = []
        if exp.net is not None and exp.vat is not None and abs(exp.net + exp.vat - exp.total) > CENT:
            out.append(IssueDraft(code=self.code, severity="warn",
                                  message=f"Net {exp.net} + VAT {exp.vat} does not equal total {exp.total}.",
                                  facts={"net": str(exp.net), "vat": str(exp.vat), "total": str(exp.total)},
                                  suggested_action="Check the amounts on the document."))
        elif exp.confidence < MIN_CONFIDENCE:
            out.append(IssueDraft(code=self.code, severity="warn", message=f"Extraction confidence is {exp.confidence:.2f}.",
                                  facts={"confidence": exp.confidence}, suggested_action="Check the extracted fields."))
        return out


class UnknownVendorRule(ExpenseRule):
    code = "UNKNOWN_VENDOR"

    def check(self, exp, ctx):
        if ctx.vendor:
            return []
        return [IssueDraft(code=self.code, severity="block",
                           message=f"Vendor {exp.vendor_name or '?'} (VAT {exp.vendor_vat or '?'}) is not in the vendor master.",
                           facts={"vendor_name": exp.vendor_name, "vendor_vat": exp.vendor_vat},
                           suggested_action="Onboard the vendor through the normal process before paying.")]


class IbanChangedRule(ExpenseRule):
    code = "IBAN_CHANGED"

    def check(self, exp, ctx):
        doc, master = normalize_iban(exp.iban), normalize_iban(ctx.vendor.iban if ctx.vendor else "")
        if not (doc and master) or doc == master:
            return []
        return [IssueDraft(code=self.code, severity="block",
                           message=f"Bank account on the document ({doc}) differs from the vendor master ({master}).",
                           facts={"document_iban": doc, "master_iban": master, "vendor": ctx.vendor.name},
                           suggested_action=IBAN_ACTION)]


class DuplicateRule(ExpenseRule):
    code = "DUPLICATE"

    def check(self, exp, ctx):
        for prior in ctx.prior_docs:
            if exp.number and prior.number and exp.number.strip().upper() == prior.number.strip().upper():
                return [IssueDraft(code=self.code, severity="block",
                                   message=f"Document number {exp.number} was already received from this vendor.",
                                   facts={"number": exp.number, "prior_document": prior.id},
                                   suggested_action="Reject unless the vendor confirms it is a different charge.")]
        for prior in ctx.prior_docs:
            if (exp.total is not None and prior.total is not None and exp.date and prior.date
                    and abs(exp.total - prior.total) <= CENT
                    and abs((exp.date - prior.date).days) <= DUPLICATE_WINDOW_DAYS):
                return [IssueDraft(code=self.code, severity="warn",
                                   message=f"Same vendor and same total ({exp.total}) as document {prior.id} within {DUPLICATE_WINDOW_DAYS} days.",
                                   facts={"total": str(exp.total), "prior_document": prior.id},
                                   suggested_action="Check whether it is the same service billed twice.")]
        return []


def _covering(ctx: RuleContext) -> PreapprovalFacts | None:
    return next((p for p in ctx.preapprovals if ctx.category in p.categories), None)


class NotPreapprovedRule(ExpenseRule):
    code = "NOT_PREAPPROVED"

    def check(self, exp, ctx):
        if ctx.category not in PREAPPROVAL_REQUIRED or _covering(ctx):
            return []
        return [IssueDraft(code=self.code, severity="block",
                           message=f"{ctx.category.replace('_', ' ').title()} charge without an approved pre-approval"
                                   + (f" for {ctx.shipment_ref}." if ctx.shipment_ref else "."),
                           facts={"category": ctx.category, "shipment": ctx.shipment_ref},
                           suggested_action="Ask the manager to approve the spend, or reject the charge.")]


class OverPreapprovalRule(ExpenseRule):
    code = "OVER_PREAPPROVAL"

    def check(self, exp, ctx):
        p = _covering(ctx)
        if p is None or exp.total is None:
            return []
        used = p.already_spent + exp.total
        if used <= p.amount_cap + tolerance(p.amount_cap):
            return []
        return [IssueDraft(code=self.code, severity="block",
                           message=f"{exp.total} brings {ctx.category} spend to {used}, over the pre-approved cap of {p.amount_cap}.",
                           facts={"cap": str(p.amount_cap), "already_spent": str(p.already_spent), "total": str(exp.total),
                                  "excess": str(used - p.amount_cap), "preapproval": p.id},
                           suggested_action="Approve the excess explicitly (override with a note) or dispute the invoice.")]


class RuleEngine:
    def __init__(self, rules: list[ExpenseRule] | None = None):
        self.rules = rules or [LowConfidenceRule(), UnknownVendorRule(), IbanChangedRule(), DuplicateRule(),
                               NotPreapprovedRule(), OverPreapprovalRule()]

    def run(self, exp: ExtractedExpense, ctx: RuleContext) -> list[IssueDraft]:
        return [issue for rule in self.rules for issue in rule.check(exp, ctx)]
