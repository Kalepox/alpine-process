"""plan_routes: plan at booking time (PLAN_B §B2.7 steps 1-6). Owned by B.

    Shipment.request ─► intent_agent (only if it holds free text) ─► RoutingRequest
                     ─► RouteSolver.plan (+ RiskOverlay from Corridor rows)    [solver.run]
                     ─► route_advisor, validated by code (or fallback)          [advisor.verdict]
                     ─► save_route_options (recommended flagged)                ─► human approves

Shipment.request is either a RoutingRequest dict (the /plan form) or contains
"text" (the free-text box), optionally next to form fields, which win.
Never approves anything.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from core.services import audit

from autopilot.agents.advisor import (
    advisor_payload,
    apply_verdict,
    fallback_verdict,
    route_advisor,
    validate_verdict,
)
from autopilot.agents.intent import intent_agent
from autopilot.audit_plugin import jsonable
from autopilot.domain.risk import RiskOverlay
from autopilot.domain.schemas import AdvisorVerdict, RouteOptionDTO, RoutingRequest
from autopilot.domain.solver import RouteSolver
from autopilot.runner import MODEL, PROMPT_VERSION, gemini_available, run_agent_sync


class IntentUnavailable(Exception):
    """Free text without Gemini: the form must be used."""


@dataclass
class PlanOutcome:
    request: RoutingRequest
    options: list[RouteOptionDTO]
    contingencies: list[RouteOptionDTO]
    verdict: AdvisorVerdict


# ---------------------------------------------------------------- pure core (no ORM)
def parse_request(data: dict, *, run, today: dt.date) -> RoutingRequest:
    """Form fields as they are; free text through intent_agent, with form fields taking precedence."""
    text = (data.get("text") or "").strip()
    form = {k: v for k, v in data.items() if k != "text"}
    if not text:
        return RoutingRequest.model_validate(form)
    if not gemini_available():
        try:
            request = RoutingRequest.model_validate(form)
        except ValueError as e:
            raise IntentUnavailable("Free-text planning needs Gemini (GOOGLE_API_KEY); use the form fields.") from e
        audit.log("intent.skipped", actor_type="system", actor_name="intent_agent", run=run,
                  detail="Gemini not configured: free text ignored, form fields used.")
        return request
    message = f"TODAY: {today.isoformat()}\nORDER: {text}\nFORM: {json.dumps(form, default=str)}"
    raw = run_agent_sync(intent_agent, message, run=run)
    request = RoutingRequest.model_validate({**(raw or {}), **form})
    audit.log("intent.parsed", actor_type="agent", actor_name="intent_agent", run=run,
              input={"text": text, "form": form}, output=jsonable(request))
    if request.cargo.value_eur == 0:
        audit.log("intent.missing_value", actor_type="agent", actor_name="intent_agent", run=run,
                  detail="Cargo value not stated: insurance and carrying cost are computed as 0.")
    return request


def advise(request: RoutingRequest, options: list[RouteOptionDTO], *, run, replan: dict | None = None) -> AdvisorVerdict:
    """route_advisor (validated) or the deterministic fallback; applies the verdict to the options."""
    payload = advisor_payload(request, options, replan)
    verdict, reason = None, "Gemini not configured"
    if gemini_available():
        try:
            raw = run_agent_sync(route_advisor, json.dumps(payload, ensure_ascii=False), run=run)
            verdict, reason = validate_verdict(raw, options, payload)
        except Exception as e:  # model/network failure must not lose the solver's work
            reason = f"advisor failed: {type(e).__name__}: {e}"
    source = "route_advisor"
    if verdict is None:
        verdict, source = fallback_verdict(options), "fallback"
    apply_verdict(options, verdict, replan=replan is not None)
    audit.log("advisor.verdict", actor_type="agent", actor_name="route_advisor", run=run,
              input=payload, output=jsonable(verdict),
              detail=f"source={source}" + (f"; fallback reason: {reason}" if source == "fallback" else ""))
    return verdict


def plan_request(data: dict, *, run, overlay: RiskOverlay, today: dt.date, solver: RouteSolver | None = None) -> PlanOutcome:
    request = parse_request(data, run=run, today=today)
    result = (solver or RouteSolver()).plan(request, overlay, today=today)
    audit.log("solver.run", actor_type="agent", actor_name="route_solver", run=run,
              input=jsonable(request), output={**result.meta, "options": [o.label for o in result.options]},
              meta={"tool": "RouteSolver.plan"})
    verdict = advise(request, result.options, run=run)
    return PlanOutcome(request, result.options, result.contingencies, verdict)


# ---------------------------------------------------------------- pipeline entrypoint (ORM)
def plan_routes(shipment_id: int, user_id: int) -> list[int]:
    """Returns the ids of the 3-5 RouteOptions (contingencies are saved too), one marked recommended."""
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from core.models import Corridor, Shipment
    from core.services import shipments

    shipment = Shipment.objects.get(pk=shipment_id)
    user = get_user_model().objects.get(pk=user_id)
    run = audit.start_run("plan_routes", trigger="user", triggered_by=user, shipment=shipment,
                          input=shipment.request, model=MODEL, prompt_version=PROMPT_VERSION)
    try:
        outcome = plan_request(shipment.request, run=run, overlay=RiskOverlay.from_rows(Corridor.objects.all()),
                               today=timezone.localdate())
        if shipment.request.get("text"):  # keep the parsed request: replan must not re-interpret free text
            shipments.update_shipment_request(shipment, {**jsonable(outcome.request), "text": shipment.request["text"]},
                                              actor_name="intent_agent", run=run)
        saved = shipments.save_route_options(shipment, outcome.options + outcome.contingencies,
                                             run=run, actor_name="route_advisor")
    except Exception as e:
        audit.log("pipeline.failed", actor_type="system", actor_name="plan_routes", run=run, shipment=shipment,
                  detail=f"{type(e).__name__}: {e}")
        audit.finish_run(run, "FAILED", summary=str(e))
        raise
    best = next(o for o in outcome.options if o.recommended)
    audit.finish_run(run, "OK", summary=f"{len(outcome.options)} routes; recommended {best.key}: {best.label}")
    return [o.id for o in saved[: len(outcome.options)]]
