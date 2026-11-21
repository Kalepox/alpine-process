"""doc_intake (document -> ExtractedExpense) and doc_explainer (rule issues -> plain wording).

The rules (domain/rules.py) decide the issues. The explainer may only reword them:
code checks it returned exactly the same issue codes and no new numbers.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from pydantic import BaseModel, Field

from autopilot.agents.advisor import ungrounded_numbers
from autopilot.domain.schemas import ExtractedExpense, IssueDraft
from autopilot.runner import call_config, gemini

doc_intake = LlmAgent(
    name="doc_intake",
    model=gemini(),
    generate_content_config=call_config(),
    description="Reads an expense document (invoice, bill, payment order) into structured fields.",
    instruction="""Extract the fields of the attached logistics expense document.
- doc_type: freight_bill, insurance_invoice, demurrage_bill, inspection_invoice, customs, port, payment_order or other.
- Copy amounts, dates, document number, VAT number and IBAN exactly as printed. Amounts without thousands separators.
- shipment_hint: vessel name, AWB or B/L number if printed.
- line_items: one per billed line, with its description.
- If a field is not on the document, leave it null. Never guess.
- confidence: 0-1, how sure you are the amounts and IBAN are read correctly (low for blurry or handwritten scans).""",
    output_schema=ExtractedExpense,
    output_key="extracted",
)


class ExplainedIssue(BaseModel):
    code: str
    message: str
    suggested_action: str


class DocExplanation(BaseModel):
    summary: str = Field(description="2 sentences for the reviewer: what the document is and what needs attention")
    issues: list[ExplainedIssue] = Field(default_factory=list)


doc_explainer = LlmAgent(
    name="doc_explainer",
    model=gemini(),
    generate_content_config=call_config(),
    description="Explains rule findings on an expense document in plain language.",
    instruction="""You receive an expense document's extracted fields and the issues found by deterministic rules.
Write a short summary and, for EACH issue (same codes, same order), a clear message and suggested action
for an accounts-payable reviewer. Do not add, drop or soften issues. Use only numbers present in the input.
For IBAN_CHANGED always keep the advice to verify through the vendor master's contact details.""",
    output_schema=DocExplanation,
    output_key="explanation",
)


def validate_explanation(raw: dict | None, drafts: list[IssueDraft], payload: dict) -> tuple[DocExplanation | None, str]:
    if not raw:
        return None, "no explanation"
    try:
        expl = DocExplanation.model_validate(raw)
    except ValueError as e:
        return None, f"invalid explanation: {e}"
    if [i.code for i in expl.issues] != [d.code for d in drafts]:
        return None, f"issue codes changed: {[i.code for i in expl.issues]} vs {[d.code for d in drafts]}"
    text = " ".join([expl.summary, *(f"{i.message} {i.suggested_action}" for i in expl.issues)])
    if bad := ungrounded_numbers(text, payload):
        return None, f"numbers not in the document or rules: {sorted(bad)}"
    return expl, ""


def fallback_explanation(exp: ExtractedExpense, drafts: list[IssueDraft]) -> DocExplanation:
    what = f"{(exp.doc_type or 'document').replace('_', ' ')} {exp.number or ''} from {exp.vendor_name or 'unknown vendor'}"
    status = f"{len(drafts)} issue(s) need attention." if drafts else "No issues found; ready for payment approval."
    return DocExplanation(summary=f"{what.strip()}, total {exp.currency} {exp.total}. {status}",
                          issues=[ExplainedIssue(code=d.code, message=d.message, suggested_action=d.suggested_action)
                                  for d in drafts])
