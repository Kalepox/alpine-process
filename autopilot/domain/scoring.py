"""RouteScorer: five criteria, normalized across the candidate set (PLAN_B §B2.4). Owned by B.

Each criterion is scored 0-1 as a ratio to the best candidate (best = 1, twice
as expensive = 0.5, 30x as expensive = 0.03), so the size of a gap counts,
not just the ranking. Then the request's weights combine them.

Time has two meanings:
- speed matters (no deadline, the Fastest preset, or urgency "high"): ratio of days to the fastest;
- otherwise, with a deadline: deadline safety, i.e. P(on time) and the P90 buffer before the
  deadline. Being faster than needed is only worth its inventory carrying cost, which is
  already part of landed cost, so it is not counted twice.
Cost is always shown; it only *drives* the choice under the Cheapest preset.
"""
from __future__ import annotations

from dataclasses import dataclass

from .cargo import CRITERIA, nudge_weights
from .schemas import CargoClass, Preset, RoutingRequest

PRESETS: dict[Preset, dict[str, float]] = {
    Preset.BEST: {"reliability": 0.35, "time": 0.30, "cost": 0.20, "compliance": 0.10, "co2": 0.05},
    Preset.FASTEST: {"reliability": 0.20, "time": 0.60, "cost": 0.10, "compliance": 0.05, "co2": 0.05},
    Preset.MOST_RELIABLE: {"reliability": 0.60, "time": 0.20, "cost": 0.10, "compliance": 0.05, "co2": 0.05},
    Preset.CHEAPEST: {"reliability": 0.15, "time": 0.15, "cost": 0.60, "compliance": 0.05, "co2": 0.05},
    Preset.GREENEST: {"reliability": 0.15, "time": 0.10, "cost": 0.10, "compliance": 0.05, "co2": 0.60},
}

DEADLINE_MISS_PENALTY_DAYS = 20  # time criterion: a sure deadline miss counts like 20 extra days


@dataclass
class RouteMetrics:
    """Raw numbers for one candidate route (all computed by the solver)."""

    days_p10: float
    days_p50: float
    days_p90: float
    expected_days: float
    p_miss_deadline: float
    landed_cost: float
    co2_kg: float
    reliability: float  # P(on time): no closure x mode reliability
    risk_score: float   # P(at least one corridor closes)
    friction: float     # transfers + customs borders + sanctioned + cargo mismatches

    def raw(self) -> dict[str, float]:
        """Criterion -> raw value where LOWER is better."""
        return {
            "reliability": -self.reliability,
            "time": self.days_p50 + 0.5 * (self.days_p90 - self.days_p50) + DEADLINE_MISS_PENALTY_DAYS * self.p_miss_deadline,
            "cost": self.landed_cost,
            "compliance": self.friction,
            "co2": self.co2_kg,
        }


def weights_for(request: RoutingRequest) -> dict[str, float]:
    """Explicit weights win; otherwise the preset. Cargo nudges apply to both, then renormalize."""
    base = request.weights.model_dump() if request.weights else PRESETS[request.preset]
    if sum(base.values()) <= 0:
        base = PRESETS[Preset.BEST]
    return nudge_weights(base, CargoClass(request.cargo.cargo_class))


def _ratio(best: float, value: float) -> float:
    """best/value for lower-is-better quantities, safe for zeros."""
    return 1.0 if value <= best or value <= 0 else best / value


class RouteScorer:
    def __init__(self, weights: dict[str, float], *, slack_days: float | None = None, speed_matters: bool = True):
        self.weights = weights
        self.slack_days = slack_days
        self.speed_matters = speed_matters or slack_days is None

    def score(self, candidates: list[RouteMetrics]) -> list[dict[str, float]]:
        """Per candidate: {criterion: 0-1, ..., "total": weighted sum}. 1 = best in this set."""
        best_rel = max(m.reliability for m in candidates)
        best_time = min(self._lead_time(m) for m in candidates)
        best_cost = min(m.landed_cost for m in candidates)
        best_co2 = min(m.co2_kg for m in candidates)
        best_friction = min(m.friction for m in candidates)
        out = []
        for m in candidates:
            s = {
                "reliability": m.reliability / best_rel if best_rel > 0 else 1.0,
                "time": self._time_score(m, best_time),
                "cost": _ratio(best_cost, m.landed_cost),
                "compliance": (1 + best_friction) / (1 + m.friction),
                "co2": _ratio(best_co2, m.co2_kg),
            }
            s = {c: round(v, 4) for c, v in s.items()}
            s["total"] = round(sum(self.weights[c] * s[c] for c in CRITERIA), 4)
            out.append(s)
        return out

    @staticmethod
    def _lead_time(m: RouteMetrics) -> float:
        return m.days_p50 + 0.5 * (m.days_p90 - m.days_p50)  # median plus half the spread

    def _time_score(self, m: RouteMetrics, best_time: float) -> float:
        on_time = 1 - m.p_miss_deadline
        if self.speed_matters:
            return on_time * _ratio(best_time, self._lead_time(m))
        buffer = max(0.0, self.slack_days - m.days_p90) / self.slack_days if self.slack_days > 0 else 0.0
        return on_time * (0.8 + 0.2 * min(buffer, 1.0))


def pareto_front(candidates: list[RouteMetrics]) -> list[int]:
    """Indices of candidates no other candidate beats on every criterion."""
    raws = [m.raw() for m in candidates]

    def dominates(a: dict, b: dict) -> bool:
        return all(a[c] <= b[c] for c in CRITERIA) and any(a[c] < b[c] for c in CRITERIA)

    return [i for i, r in enumerate(raws) if not any(dominates(o, r) for j, o in enumerate(raws) if j != i)]
