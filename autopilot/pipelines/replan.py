"""replan: new options from where the cargo is now (PLAN_B §B2.6, §B2.7 step 11). Owned by B.

    approved route (remaining legs) + last position + current RiskOverlay
      ─► RouteSolver.replan: reroutes + WAIT + break-even                [solver.run]
      ─► route_advisor in replan mode (or fallback)                       [advisor.verdict]
      ─► save_route_options ─► raise_alert ─► request_preapproval (extra spend only)

Triggered by a disruption (scan/simulate/monitor: user_id None) or by a user.
Does NOT choose: the human approves a reroute or WAIT.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from core.services import audit

from autopilot.audit_plugin import jsonable
from autopilot.domain.risk import RiskOverlay
from autopilot.domain.schemas import AdvisorVerdict, Leg, RouteOptionDTO, RoutingRequest
from autopilot.domain.solver import RouteSolver
from autopilot.pipelines.plan import advise
from autopilot.runner import MODEL, PROMPT_VERSION


@dataclass
class ReplanOutcome:
    options: list[RouteOptionDTO]
    verdict: AdvisorVerdict
    meta: dict

    @property
    def recommended(self) -> RouteOptionDTO:
        return next(o for o in self.options if o.recommended)

    def extra_spend(self) -> tuple[Decimal, list[str]]:
        """Recommended option's cost above carrying on as planned, and the categories that grow."""
        if not self.meta.get("baseline_cost"):
            return Decimal("0"), []
        base = {k: Decimal(v) for k, v in self.meta["baseline_breakdown"].items()}
        rec = self.recommended.cost_breakdown
        grown = sorted(k for k, v in rec.items() if k != "CARRYING" and v > base.get(k, Decimal("0")))
        return max(self.recommended.landed_cost - Decimal(self.meta["baseline_cost"]), Decimal("0")), grown


def replan_request(request: RoutingRequest, remaining_legs: list[Leg], *, run, overlay: RiskOverlay, today: dt.date,
                   position: tuple[float, float] | None = None, elapsed_days: float = 0.0,
                   planned_remaining_days: float | None = None, demurrage_per_day: float = 0.0,
                   solver: RouteSolver | None = None) -> ReplanOutcome:
    result = (solver or RouteSolver()).replan(
        request, remaining_legs, overlay, position=position, today=today, elapsed_days=elapsed_days,
        planned_remaining_days=planned_remaining_days, demurrage_per_day=demurrage_per_day)
    audit.log("solver.run", actor_type="agent", actor_name="route_solver", run=run,
              input={"request": jsonable(request), "remaining_legs": [l.model_dump(exclude={"geometry"}) for l in remaining_legs],
                     "position": position, "elapsed_days": elapsed_days, "demurrage_per_day": demurrage_per_day},
              output={**result.meta, "options": [o.label for o in result.options]},
              meta={"tool": "RouteSolver.replan"})
    situation = {"blocking": result.meta["blocking"], "wait_days": result.meta["wait_days"], "start": result.meta["start"]}
    verdict = advise(request, result.options, run=run, replan=situation)
    return ReplanOutcome(result.options, verdict, result.meta)


def replan(shipment_id: int, user_id: int | None = None, trigger: dict | None = None) -> list[int]:
    """Returns the new RouteOption ids (incl. WAIT), one marked recommended.

    trigger: what caused it ({"kind", "code", "severity", "corridor"}); stored in the run for the monitor's cooldown.
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from core.models import Corridor, Shipment
    from core.services import approvals, shipments

    shipment = Shipment.objects.select_related("route").get(pk=shipment_id)
    user = get_user_model().objects.get(pk=user_id) if user_id else None
    now = timezone.now()
    last = shipment.positions.order_by("-ts").first()
    legs = [Leg.model_validate(l) for l in shipment.route.legs[shipment.current_leg_index:]]
    run = audit.start_run("replan", trigger="user" if user else "system", triggered_by=user, shipment=shipment,
                          input={"trigger": trigger or {}, "route_option": shipment.route_id,
                                 "leg_index": shipment.current_leg_index,
                                 "position": [last.lat, last.lon, last.ts.isoformat(), last.source] if last else None},
                          model=MODEL, prompt_version=PROMPT_VERSION)
    try:
        outcome = replan_request(
            RoutingRequest.model_validate({k: v for k, v in shipment.request.items() if k != "text"}), legs,
            run=run, overlay=RiskOverlay.from_rows(Corridor.objects.all()), today=timezone.localdate(),
            position=(last.lat, last.lon) if last and shipment.departed_at else None,
            elapsed_days=(now - shipment.departed_at).total_seconds() / 86400 if shipment.departed_at else 0.0,
            planned_remaining_days=(shipment.eta_planned - now).total_seconds() / 86400 if shipment.eta_planned else None,
            demurrage_per_day=float(shipment.demurrage_per_day or 0),
        )
        saved = shipments.save_route_options(shipment, outcome.options, run=run, actor_name="route_advisor")
        rec_dto = outcome.recommended
        rec = saved[outcome.options.index(rec_dto)]
        blocked = outcome.meta["blocking"]
        shipments.raise_alert(
            shipment, "CORRIDOR_CLOSED" if blocked else "CORRIDOR_AT_RISK", "block" if blocked else "warn",
            f"Replan ready: {len(outcome.options)} options. Recommended {rec_dto.key}: {rec_dto.label}",
            actor_name="route_advisor", run=run)
        extra, categories = outcome.extra_spend()
        if extra > 0:
            approvals.request_preapproval(
                shipment, rec, amount_cap=extra, categories=categories,
                reason=f"{rec_dto.key} costs EUR {extra} more than continuing as planned ({', '.join(blocked) or 'risk'}).",
                requested_by="route_advisor", run=run)
    except Exception as e:
        audit.log("pipeline.failed", actor_type="system", actor_name="replan", run=run, shipment=shipment,
                  detail=f"{type(e).__name__}: {e}")
        audit.finish_run(run, "FAILED", summary=str(e))
        raise
    audit.finish_run(run, "OK", summary=f"{outcome.verdict.reroute_or_wait}: {rec_dto.key} {rec_dto.label}")
    return [o.id for o in saved]
