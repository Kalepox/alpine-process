"""Expense track: rules decide issues, Gemini reads and rewords (validated)."""
import datetime as dt
from decimal import Decimal

import pytest

from autopilot.domain.rules import PreapprovalFacts, PriorDoc, RuleContext, RuleEngine, VendorFacts, category_for
from autopilot.domain.schemas import ExtractedExpense, LineItem
from autopilot.pipelines import documents as docs
from autopilot.tests.fakes import FakeAudit, FakeLlm

LLOYDS = VendorFacts("Harbor Marine Insurance", "GB123456789", "GB29 NWBK 6016 1331 9268 19")
CAPE_CAP = PreapprovalFacts(41, Decimal("12000"), ["WAR_RISK", "FREIGHT"])


def exp(**kw) -> ExtractedExpense:
    base = dict(vendor_name="Harbor Marine Insurance", vendor_vat="GB123456789", doc_type="insurance_invoice",
                number="INS-2211", date=dt.date(2026, 11, 20), net=Decimal("14800"), vat=Decimal("0"),
                total=Decimal("14800"), iban="GB29NWBK60161331926819", confidence=0.95,
                line_items=[LineItem(description="War risk additional premium, Cape diversion")])
    return ExtractedExpense(**{**base, **kw})


def ctx(**kw) -> RuleContext:
    return RuleContext(**{"vendor": LLOYDS, "category": "WAR_RISK", "preapprovals": [CAPE_CAP], "shipment_ref": "MT Aurelia Grace", **kw})


def codes(e, c):
    return [(d.code, d.severity) for d in RuleEngine().run(e, c)]


def test_war_risk_invoice_over_the_cape_cap():
    (issue,) = RuleEngine().run(exp(), ctx())
    assert issue.code == "OVER_PREAPPROVAL" and issue.facts["excess"] == "2800"


def test_within_one_percent_of_cap_is_fine():
    assert codes(exp(total=Decimal("12100"), net=Decimal("12100")), ctx()) == []


def test_changed_iban_is_a_fraud_block():
    (issue,) = RuleEngine().run(exp(total=Decimal("9000"), net=Decimal("9000"), iban="DE89 3704 0044 0532 0130 00"), ctx())
    assert issue.code == "IBAN_CHANGED" and issue.severity == "block" and "vendor master" in issue.suggested_action


def test_demurrage_without_preapproval_and_duplicates():
    demurrage = exp(doc_type="demurrage_bill", line_items=[], total=Decimal("50000"), net=Decimal("50000"))
    assert category_for(demurrage) == "DEMURRAGE"
    assert codes(demurrage, ctx(category="DEMURRAGE", preapprovals=[])) == [("NOT_PREAPPROVED", "block")]
    again = ctx(category="FREIGHT", preapprovals=[], prior_docs=[PriorDoc(7, "ins-2211", Decimal("1"), None)])
    assert codes(exp(), again) == [("DUPLICATE", "block")]
    same_total = ctx(category="FREIGHT", preapprovals=[], prior_docs=[PriorDoc(8, "X-1", Decimal("14800"), dt.date(2026, 11, 2))])
    assert codes(exp(), same_total) == [("DUPLICATE", "warn")]


def test_unknown_vendor_and_unreadable_amounts():
    assert ("UNKNOWN_VENDOR", "block") in codes(exp(), ctx(vendor=None))
    assert codes(exp(total=None), ctx())[0] == ("LOW_CONFIDENCE", "block")
    assert ("LOW_CONFIDENCE", "warn") in codes(exp(vat=Decimal("100")), ctx(preapprovals=[], category="FREIGHT"))


def test_clean_freight_bill():
    freight = exp(doc_type="freight_bill", line_items=[LineItem(description="Ocean freight")], total=Decimal("9000"), net=Decimal("9000"))
    assert category_for(freight) == "FREIGHT" and codes(freight, ctx(category="FREIGHT", preapprovals=[])) == []


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    return FakeAudit().install(monkeypatch)


def script(monkeypatch, name, responses):
    llm = FakeLlm(responses=list(responses))
    monkeypatch.setattr(docs, name, getattr(docs, name).model_copy(update={"model": llm}))
    return llm


def test_assess_document_end_to_end(monkeypatch, gemini):
    intake = script(monkeypatch, "doc_intake", [exp().model_dump(mode="json")])
    script(monkeypatch, "doc_explainer", [{
        "summary": "War-risk insurance invoice for MT Aurelia Grace; it exceeds the approved cap.",
        "issues": [{"code": "OVER_PREAPPROVAL", "message": "This premium goes 2800 over the approved cap of 12000.",
                    "suggested_action": "Ask Marco to approve the excess or dispute it."}],
    }])
    out = docs.assess_document(b"%PDF-1.4 fake", "application/pdf", lambda e: ctx(), run=gemini.start_run("process_document"))
    assert out.blocked and out.issues[0].facts["excess"] == "2800"            # facts from the rules
    assert out.issues[0].message.startswith("This premium")                   # wording from Gemini
    sent = intake.requests[0].contents[-1].parts[0]
    assert sent.inline_data.mime_type == "application/pdf"                    # the file itself went to Gemini
    assert [a for a in gemini.actions() if not a.startswith(("agent.", "model."))] == ["rules.run", "explainer.result"]


def test_explainer_cannot_drop_issues_or_invent_numbers(monkeypatch, gemini):
    script(monkeypatch, "doc_intake", [exp().model_dump(mode="json")])
    script(monkeypatch, "doc_explainer", [{"summary": "All fine.", "issues": []}])
    out = docs.assess_document(b"x", "application/pdf", lambda e: ctx(), run=gemini.start_run("p"))
    assert [i.code for i in out.issues] == ["OVER_PREAPPROVAL"] and "codes changed" in gemini.events[-1].detail

    script(monkeypatch, "doc_intake", [exp().model_dump(mode="json")])
    script(monkeypatch, "doc_explainer", [{"summary": "Over by 99999.", "issues": [
        {"code": "OVER_PREAPPROVAL", "message": "Over.", "suggested_action": "Check."}]}])
    out = docs.assess_document(b"x", "application/pdf", lambda e: ctx(), run=gemini.start_run("p"))
    assert "99999" in gemini.events[-1].detail and out.explanation.summary.startswith("insurance invoice")


def test_no_gemini_no_intake(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(docs.IntakeUnavailable):
        docs.extract(b"x", "application/pdf", run=None)
