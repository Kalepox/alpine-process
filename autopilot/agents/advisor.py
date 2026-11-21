"""route_advisor: picks and explains among solver options (PLAN_B §B2.2 #3).

No tools: the solver's options arrive in the message. The verdict is checked by
code (validate_verdict): option keys must exist and every number in the text
must come from the options, otherwise a deterministic fallback is used.
"""
from __future__ import annotations

import json
import re

from google.adk.agents import LlmAgent

from autopilot.domain.schemas import AdvisorVerdict, RouteOptionDTO, RoutingRequest, Tradeoff
from autopilot.domain.tables import CORRIDORS
from autopilot.runner import MODEL

PLAN_B_THRESHOLD = 0.15  # a recommended route whose riskiest corridor is likelier than this must show its Plan B

INSTRUCTION = """You are the route advisor of a logistics back office. You receive a shipping request and
3-5 route options computed by a deterministic solver. Recommend one and explain the trade-offs.

Rules:
- recommended_key must be the key of one option ("R1", "R2", ...). R1 has the highest score for the
  request's priorities; choose another only for a clear reason visible in the data (e.g. a deadline or
  corridor risk the score underweights) and say why.
- rationale: 2-3 sentences for an operations manager. tradeoffs: one short sentence per option, by key.
- Use ONLY facts in the JSON. Every number you write must appear in the JSON exactly as given
  (e.g. "28.6 days", "18% closure risk"). Prefer words ("faster", "cheaper", "riskier") over numbers.
- plan_b_required: true if the recommended option has a possible disruption with p_close_pct >= 15.
- mode "plan": reroute_or_wait is "NA".
- mode "replan": the cargo is already moving and a corridor on its route is closed. Options are reroutes
  (kind ROUTE) from its current position and possibly WAIT (hold until the corridor reopens, then continue).
  delay_days is versus the approved plan. break_even_days on a reroute means: waiting beats this reroute
  if the corridor reopens within that many days; compare it with wait_days (the estimated closure).
  Never recommend an option with an infeasible_reason. Set reroute_or_wait to "WAIT" or "REROUTE" to match
  the recommended option, and mention the break-even in the rationale.
"""

route_advisor = LlmAgent(
    name="route_advisor",
    model=MODEL,
    description="Recommends one of the solver's route options and explains the trade-offs.",
    instruction=INSTRUCTION,
    output_schema=AdvisorVerdict,
    output_key="advisor_verdict",
)


def advisor_payload(request: RoutingRequest, options: list[RouteOptionDTO], replan: dict | None = None) -> dict:
    """What the advisor sees: rounded, human units, nothing it could misread.

    replan: {"blocking": [corridor codes], "wait_days": float, "start": str} when re-planning in transit.
    """
    payload = {
        "mode": "replan" if replan else "plan",
        "request": {
            "cargo": request.cargo.description, "cargo_class": request.cargo.cargo_class.value,
            "weight_t": request.cargo.weight_t, "deadline": str(request.deadline) if request.deadline else None,
            "preset": request.preset.value, "urgency": request.urgency,
        },
        "options": [
            {
                "key": o.key, "label": o.label, "score": o.score.get("total"),
                "days_expected": o.transit_days, "days_p10": o.days_p10, "days_p90": o.days_p90,
                "landed_cost_eur": round(float(o.landed_cost)), "co2_t": round(o.co2_kg / 1000, 1),
                "closure_risk_pct": round(o.risk_score * 100), "reliability_pct": o.score.get("reliability_pct"),
                "deadline_miss_pct": round(o.score.get("p_miss_deadline", 0) * 100),
                "transfers": max(len(o.legs) - 1, 0), "kind": o.kind,
                **({"delay_days": o.delay_days, "break_even_days": o.break_even_days,
                    "infeasible_reason": o.infeasible_reason} if replan else {}),
                "possible_disruptions": [
                    {"corridor": CORRIDORS[d.corridor][0], "p_close_pct": round(d.p_close * 100),
                     "delay_days": d.delay_days, "plan_b": d.contingency_label}
                    for d in o.possible_disruptions
                ],
            }
            for o in options
        ],
    }
    if replan:
        payload["situation"] = {
            "closed": [CORRIDORS[c][0] for c in replan.get("blocking", [])],
            "wait_days": replan.get("wait_days"), "cargo_now": replan.get("start"),
        }
    return payload


_KEY = re.compile(r"\bR\d+(?:-B)?\b")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def ungrounded_numbers(text: str, payload: dict) -> set[str]:
    """Numbers in the advisor's text that do not occur in the payload (small counts are fine)."""
    allowed = set(_NUMBER.findall(json.dumps(payload)))
    found = {n.replace(",", "") for n in _NUMBER.findall(_KEY.sub("", text))}
    return {n for n in found if n not in allowed and not (n.isdigit() and int(n) <= 12)}


def validate_verdict(raw: dict | None, options: list[RouteOptionDTO], payload: dict) -> tuple[AdvisorVerdict | None, str]:
    """(verdict, "") if usable, else (None, reason)."""
    if not raw:
        return None, "no verdict"
    try:
        verdict = AdvisorVerdict.model_validate(raw)
    except ValueError as e:
        return None, f"invalid verdict: {e}"
    if unknown := verdict.unknown_keys({o.key for o in options}):
        return None, f"unknown option keys {sorted(unknown)}"
    if next(o for o in options if o.key == verdict.recommended_key).infeasible_reason:
        return None, f"recommended an infeasible option ({verdict.recommended_key})"
    text = " ".join([verdict.rationale, *(t.text for t in verdict.tradeoffs)])
    if bad := ungrounded_numbers(text, payload):
        return None, f"numbers not in the solver output: {sorted(bad)}"
    return verdict, ""


def fallback_verdict(options: list[RouteOptionDTO]) -> AdvisorVerdict:
    """Deterministic verdict when Gemini is unavailable or its answer failed validation."""
    best = options[0]
    rationale = (f"{best.key} has the highest score for this request's priorities: about {best.transit_days} days "
                 f"door to door ({best.days_p10}-{best.days_p90}), landed cost EUR {best.landed_cost}.")
    if best.kind == "WAIT" and best.break_even_days is not None:
        rationale += f" Waiting beats the best reroute if the corridor reopens within {best.break_even_days} days."
    elif best.break_even_days is not None:
        rationale += f" Waiting would only be better if the corridor reopened within {best.break_even_days} days."
    tradeoffs = []
    for o in options[1:]:
        faster = "faster" if o.transit_days < best.transit_days else "slower"
        cheaper = "cheaper" if o.landed_cost < best.landed_cost else "more expensive"
        riskier = "riskier" if o.risk_score > best.risk_score else "less or equally exposed to closures"
        tradeoffs.append(Tradeoff(option_key=o.key, text=f"{faster}, {cheaper} and {riskier} than {best.key}."))
    return AdvisorVerdict(recommended_key=best.key, rationale=rationale, tradeoffs=tradeoffs,
                          plan_b_required=needs_plan_b(best))


def needs_plan_b(option: RouteOptionDTO) -> bool:
    return any(d.p_close >= PLAN_B_THRESHOLD for d in option.possible_disruptions)


def apply_verdict(options: list[RouteOptionDTO], verdict: AdvisorVerdict, *, replan: bool = False) -> None:
    """Recommended flag + rationale text onto the options (numbers stay the solver's).

    reroute_or_wait is set by code from the recommended option's kind, whatever the model said.
    """
    best = next(o for o in options if o.key == verdict.recommended_key)
    verdict.reroute_or_wait = ("WAIT" if best.kind == "WAIT" else "REROUTE") if replan else "NA"
    verdict.plan_b_required = verdict.plan_b_required or needs_plan_b(best)
    notes = {t.option_key: t.text for t in verdict.tradeoffs}
    for o in options:
        o.recommended = o.key == verdict.recommended_key
        o.rationale = verdict.rationale if o.recommended else notes.get(o.key, "")
