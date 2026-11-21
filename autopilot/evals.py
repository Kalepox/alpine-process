"""Agent evals (plan.md B.9): scenario fixtures with invariants, printed as a table for the pitch.

routes   8 planning scenarios: solver invariants + the advisor's recommendation must be a real option
monitor  3 in-transit scenarios: detection -> replan -> the right kind of answer
risk     5 canned news snippets -> risk_scout's structurer must classify them right (needs Gemini)

Nothing is written to the database: agent steps go to an in-memory recorder.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Callable

from autopilot.domain.risk import CorridorRisk, RiskOverlay
from autopilot.domain.schemas import CargoSpec, Constraints, RiskSignal, RoutingRequest
from autopilot.domain.solver import PlanResult, RouteSolver
from autopilot.runner import gemini_available, run_agent_sync

TODAY = dt.date(2026, 11, 21)
RED_SEA = RiskOverlay([CorridorRisk("SUEZ", "RESTRICTED", 0.18, 14, 0.2),
                       CorridorRisk("BAB_EL_MANDEB", "RESTRICTED", 0.35, 21, 0.7)])


@dataclass
class EvalRow:
    suite: str
    case: str
    expect: str
    got: str
    passed: bool | None  # None = skipped
    ms: int


class Recorder:
    """In-memory stand-in for audit.log during evals."""

    def __init__(self):
        self.events = []

    def __call__(self, action, **kw):
        self.events.append(action)
        return SimpleNamespace(action=action)


def _req(origin, destination, cargo_class="CONTAINER_GENERAL", weight_t=40.0, value=400_000, **kw) -> RoutingRequest:
    cargo_kw = {k: kw.pop(k) for k in ("max_transit_days", "dg_class") if k in kw}
    return RoutingRequest(origin=origin, destination=destination, **kw,
                          cargo=CargoSpec(description=cargo_class.lower(), cargo_class=cargo_class, weight_t=weight_t,
                                          value_eur=Decimal(value), **cargo_kw))


def _corridors(o) -> set[str]:
    return {c for leg in o.legs for c in leg.corridors}


def _timed(fn: Callable[[], tuple[bool, str]]) -> tuple[bool, str, int]:
    t = time.perf_counter()
    try:
        ok, got = fn()
    except Exception as e:
        ok, got = False, f"{type(e).__name__}: {e}"
    return ok, got, round((time.perf_counter() - t) * 1000)


# ---------------------------------------------------------------- routes
ROUTE_CASES: list[tuple[str, str, Callable[[], tuple[RoutingRequest, RiskOverlay]], Callable[[PlanResult], bool]]] = [
    ("suez_closed", "no option uses Suez; a Cape option exists",
     lambda: (_req("Belawan", "Mersin", "LIQUID_BULK", 5000, 4_500_000), RED_SEA.with_status("SUEZ", "CLOSED", 30)),
     lambda r: all("SUEZ" not in _corridors(o) for o in r.options) and any("CAPE" in _corridors(o) for o in r.options)),
    ("deadline_5_days", "Dubai→Milan in 5 days: air/road only",
     lambda: (_req("Dubai", "Milan", deadline=TODAY + dt.timedelta(days=5)), RiskOverlay()),
     lambda r: all(l.mode in {"AIR", "ROAD"} for o in r.options for l in o.legs)),
    ("greenest_no_air", "Greenest preset never recommends flying",
     lambda: (_req("Dubai", "Milan", preset="GREENEST"), RiskOverlay()),
     lambda r: all(l.mode != "AIR" for l in r.options[0].legs)),
    ("gold_flies", "€30M gold: recommended by air, ≤1 transfer everywhere",
     lambda: (_req("Dubai", "Istanbul", "HIGH_VALUE", 0.5, 30_000_000), RiskOverlay()),
     lambda r: r.options[0].legs[0].mode == "AIR" and all(len(o.legs) <= 2 for o in r.options)),
    ("reefer_shelf_life", "perishables: every option's P90 within 30 days",
     lambda: (_req("Mumbai", "Milan", "PERISHABLE_REEFER", 20, 300_000, max_transit_days=30), RED_SEA),
     lambda r: all(o.days_p90 <= 30 for o in r.options)),
    ("dg_road_tunnels", "dangerous goods avoid Gotthard/Mont Blanc road tunnels",
     lambda: (_req("Rotterdam", "Milan", "DANGEROUS_GOODS", 20, dg_class="3"), RiskOverlay()),
     lambda r: not any(l.mode == "ROAD" and {"GOTTHARD", "MONT_BLANC"} & set(l.corridors) for o in r.options for l in o.legs)),
    ("copper_by_sea", "40 t copper, far deadline: ships by sea, not air",
     lambda: (_req("Antofagasta", "Milan", value=380_000, deadline=dt.date(2027, 3, 3)), RED_SEA),
     lambda r: r.options[0].legs[0].mode == "SEA"),
    ("red_sea_plan_b", "Red Sea option carries a Cape Plan B",
     lambda: (_req("Belawan", "Mersin", "LIQUID_BULK", 5000, 4_500_000), RED_SEA),
     lambda r: any(o.contingency_key and "CAPE" in _corridors(next(c for c in r.contingencies if c.key == o.contingency_key))
                   for o in r.options if "BAB_EL_MANDEB" in _corridors(o))),
]


def _advise(req: RoutingRequest, options, replan: dict | None = None) -> str:
    """The real advisor if Gemini is configured (validated), else the fallback. Returns 'key (source)'."""
    from autopilot.agents.advisor import advisor_payload, fallback_verdict, route_advisor, validate_verdict

    import json

    if not gemini_available():
        return f"{fallback_verdict(options).recommended_key} (fallback)"
    payload = advisor_payload(req, options, replan)
    raw = run_agent_sync(route_advisor, json.dumps(payload), run=None, log=Recorder())
    verdict, reason = validate_verdict(raw, options, payload)
    return f"{verdict.recommended_key} (gemini)" if verdict else f"REJECTED: {reason}"


def eval_routes(solver: RouteSolver) -> list[EvalRow]:
    rows = []
    for name, expect, setup, check in ROUTE_CASES:
        def run(setup=setup, check=check):
            req, overlay = setup()
            r = solver.plan(req, overlay, today=TODAY)
            advised = _advise(req, r.options)
            ok = check(r) and not advised.startswith("REJECTED")
            return ok, f"{r.options[0].label[:60]} · advisor {advised}"
        ok, got, ms = _timed(run)
        rows.append(EvalRow("routes", name, expect, got, ok, ms))
    return rows


# ---------------------------------------------------------------- monitor
def eval_monitor(solver: RouteSolver) -> list[EvalRow]:
    from autopilot.monitor.detectors import Tracked
    from autopilot.pipelines.monitor import simulated_weather

    palm = _req("Belawan", "Mersin", "LIQUID_BULK", 5000, 4_500_000)
    truck = _req("Rotterdam", "Milan", weight_t=20, value=200_000)

    def snow_brenner():
        route = next(o for o in solver.plan(truck, RiskOverlay(), today=TODAY).options if "BRENNER" in _corridors(o))
        d = simulated_weather(Tracked(1, "truck", route.legs, position=(48.2, 11.6)), "snow")
        r = solver.replan(truck, route.legs, RiskOverlay([CorridorRisk("BRENNER", "RESTRICTED", 0.6, 1)]),
                          position=(48.2, 11.6), today=TODAY)
        return d.needs_replan and "BRENNER" not in _corridors(r.options[0]), f"{d.alert_code} → {r.options[0].label[:55]}"

    def bab_closed(days: int, want_kind: str):
        def run():
            route = next(o for o in solver.plan(palm, RED_SEA, today=TODAY).options if "BAB_EL_MANDEB" in _corridors(o))
            situation = RED_SEA.with_status("BAB_EL_MANDEB", "CLOSED", days)
            r = solver.replan(palm, route.legs, situation, position=(12.6, 50.0), today=TODAY, demurrage_per_day=25_000)
            best = r.options[0]
            advised = _advise(palm, r.options, {"blocking": r.meta["blocking"], "wait_days": days, "start": r.meta["start"]})
            return best.kind == want_kind and not advised.startswith("REJECTED"), \
                f"{best.kind} (break-even {best.break_even_days} d) · advisor {advised}"
        return run

    rows = []
    for name, expect, fn in [
        ("snow_at_brenner", "blocking snow → replan avoids Brenner", snow_brenner),
        ("bab_closed_14d", "short closure: WAIT beats rerouting", bab_closed(14, "WAIT")),
        ("bab_closed_60d", "long closure: reroute beats WAIT", bab_closed(60, "ROUTE")),
    ]:
        ok, got, ms = _timed(fn)
        rows.append(EvalRow("monitor", name, expect, got, ok, ms))
    return rows


# ---------------------------------------------------------------- risk (Gemini)
RISK_CASES = [
    ("red_sea_attacks", "sea", "BAB_EL_MANDEB", {"CLOSED"}, {"SEVERE", "HIGH"},
     "18 Nov 2026: missiles hit two merchant ships near Bab-el-Mandeb. All major carriers have suspended "
     "transits; the navy says the strait is effectively closed to commercial traffic."),
    ("suez_normal", "sea", "SUEZ", {"OPEN"}, {"LOW"},
     "20 Nov 2026: the Suez Canal Authority reports normal traffic, about 70 transits per day, no incidents."),
    ("brenner_snow", "road", "BRENNER", {"CLOSED"}, {"SEVERE", "HIGH"},
     "21 Nov 2026: Austrian authorities closed the Brenner motorway (A13) after 60 cm of fresh snow; "
     "reopening is expected within 24 hours."),
    ("kz_border_queues", "land", "KAZ_RUS_BORDER", {"RESTRICTED"}, {"ELEVATED", "HIGH"},
     "19 Nov 2026: strikes at Kazakh-Russian border crossings cause 3-5 day queues for rail freight; trains still pass."),
    ("gulf_airspace", "air", "AIR_GULF", {"RESTRICTED", "CLOSED"}, {"HIGH", "SEVERE"},
     "20 Nov 2026: parts of Gulf airspace closed after missile alerts; airlines reroute around Qatar, cargo flights delayed."),
]


def eval_risk() -> list[EvalRow]:
    from autopilot.agents.risk_scout import build_structurer

    rows = []
    for name, group, code, statuses, levels, news in RISK_CASES:
        expect = f"{code}: {'/'.join(sorted(statuses))}, level {'/'.join(sorted(levels))}"
        if not gemini_available():
            rows.append(EvalRow("risk", name, expect, "skipped: no GOOGLE_API_KEY", None, 0))
            continue

        def run(group=group, code=code, statuses=statuses, levels=levels, news=news):
            raw = run_agent_sync(build_structurer(group), "Classify.", run=None, state={f"news_{group}": news}, log=Recorder())
            signal = next((RiskSignal.model_validate(s) for s in raw or [] if s.get("corridor") == code), None)
            if signal is None:
                return False, f"no signal for {code}"
            return signal.status in statuses and signal.level in levels, f"{signal.status}, {signal.level}"
        ok, got, ms = _timed(run)
        rows.append(EvalRow("risk", name, expect, got, ok, ms))
    return rows


SUITES = {"routes": eval_routes, "monitor": eval_monitor, "risk": eval_risk}


def run_evals(suites: list[str] | None = None) -> list[EvalRow]:
    solver = RouteSolver()
    rows = []
    for name in suites or list(SUITES):
        fn = SUITES[name]
        rows += fn() if name == "risk" else fn(solver)
    return rows
